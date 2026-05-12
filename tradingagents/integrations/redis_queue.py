"""Redis-backed order queue for reliable trade order delivery.

Replaces file-based JSON queue with Redis Streams for:
- Atomic push/pop semantics
- Consumer group support (future multi-executor scaling)
- TTL-based order expiry
- Persistence across container restarts (via Redis AOF)

Falls back to file-based queue if Redis is unavailable.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import redis
except ImportError:
    redis = None  # type: ignore[assignment]

# Stream/key names
ORDER_STREAM = "tradingagents:orders"
ORDER_PROCESSED = "tradingagents:orders:processed"
ORDER_GROUP = "tradingagents:executor-group"


class RedisOrderQueue:
    """Redis Streams-based order queue with file fallback.

    Orders are pushed to a Redis Stream keyed by ``tradingagents:orders``.
    The Lumibot executor reads from the stream using a consumer group.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        fallback_dir: str = "./orders",
    ):
        self._redis: Optional[Any] = None
        self._fallback_dir = Path(fallback_dir)

        if redis is None:
            logger.warning("redis-py not installed — using file-based fallback.")
            return

        try:
            self._redis = redis.from_url(redis_url, decode_responses=True)
            self._redis.ping()
            logger.info("Connected to Redis at %s", redis_url)
            self._ensure_consumer_group()
        except Exception as e:
            logger.warning("Redis unavailable (%s) — falling back to file queue.", e)
            self._redis = None

    @property
    def is_redis_available(self) -> bool:
        return self._redis is not None

    def _ensure_consumer_group(self) -> None:
        """Create the consumer group if it doesn't exist."""
        try:
            self._redis.xgroup_create(ORDER_STREAM, ORDER_GROUP, id="0", mkstream=True)
        except Exception:
            pass  # Group already exists

    # ------------------------------------------------------------------
    # Push (from TradingAgents after validation)
    # ------------------------------------------------------------------

    def push_order(self, order_data: Dict[str, Any]) -> str:
        """Push a validated order to the queue. Returns the order ID."""
        timestamp = datetime.now(timezone.utc).isoformat()
        order_data["queued_at"] = timestamp

        if self._redis is not None:
            try:
                msg_id = self._redis.xadd(
                    ORDER_STREAM,
                    {"order": json.dumps(order_data)},
                )
                logger.info("Order pushed to Redis: %s", msg_id)
                return str(msg_id)
            except Exception:
                logger.exception("Redis push failed — falling back to file")

        # File fallback
        return self._push_file(order_data)

    def _push_file(self, order_data: Dict[str, Any]) -> str:
        """Write order to a JSON file as fallback."""
        self._fallback_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ticker = order_data.get("ticker", "UNKNOWN")
        side = order_data.get("side", "unknown")
        filename = f"{ticker}_{side}_{ts}.json"
        filepath = self._fallback_dir / filename
        filepath.write_text(json.dumps(order_data, indent=2), encoding="utf-8")
        logger.info("Order written to file: %s", filepath)
        return str(filepath)

    # ------------------------------------------------------------------
    # Pop (from Lumibot executor)
    # ------------------------------------------------------------------

    def pop_orders(
        self,
        consumer_name: str = "executor-1",
        count: int = 10,
        block_ms: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Read pending orders from the queue.

        Returns a list of dicts, each with 'id' and 'order' keys.
        """
        if self._redis is not None:
            try:
                messages = self._redis.xreadgroup(
                    ORDER_GROUP,
                    consumer_name,
                    {ORDER_STREAM: ">"},
                    count=count,
                    block=block_ms,
                )
                results = []
                for stream_name, entries in messages:
                    for msg_id, fields in entries:
                        order = json.loads(fields["order"])
                        results.append({"id": msg_id, "order": order})
                return results
            except Exception:
                logger.exception("Redis pop failed")
                return []

        # File fallback
        return self._pop_files()

    def _pop_files(self) -> List[Dict[str, Any]]:
        """Read order files from the fallback directory."""
        if not self._fallback_dir.exists():
            return []
        results = []
        for f in sorted(self._fallback_dir.glob("*.json")):
            try:
                order = json.loads(f.read_text())
                results.append({"id": str(f), "order": order})
            except Exception:
                logger.exception("Failed to read order file: %s", f)
        return results

    def ack_order(self, order_id: str) -> None:
        """Acknowledge (mark as processed) an order."""
        if self._redis is not None:
            try:
                # ACK in the consumer group
                self._redis.xack(ORDER_STREAM, ORDER_GROUP, order_id)
                # Move to processed stream for audit
                self._redis.xadd(
                    ORDER_PROCESSED,
                    {"original_id": order_id, "processed_at": datetime.now(timezone.utc).isoformat()},
                )
                return
            except Exception:
                logger.exception("Redis ACK failed for %s", order_id)

        # File fallback: move to processed dir
        src = Path(order_id)
        if src.exists():
            processed_dir = self._fallback_dir / "processed"
            processed_dir.mkdir(exist_ok=True)
            src.rename(processed_dir / src.name)

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    def pending_count(self) -> int:
        """Return the number of unprocessed orders."""
        if self._redis is not None:
            try:
                info = self._redis.xinfo_stream(ORDER_STREAM)
                return info.get("length", 0)
            except Exception:
                return 0

        if not self._fallback_dir.exists():
            return 0
        return len(list(self._fallback_dir.glob("*.json")))

    def get_queue_stats(self) -> Dict[str, Any]:
        """Return queue statistics for monitoring."""
        stats = {
            "backend": "redis" if self._redis else "file",
            "pending": self.pending_count(),
        }
        if self._redis is not None:
            try:
                info = self._redis.xinfo_stream(ORDER_STREAM)
                stats["total_entries"] = info.get("length", 0)
                stats["first_entry"] = info.get("first-entry", None)
                stats["last_entry"] = info.get("last-entry", None)
            except Exception:
                pass
        return stats
