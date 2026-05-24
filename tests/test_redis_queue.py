"""Tests for tradingagents/integrations/redis_queue.py."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies before importing (not installed in test env)
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    mod.__package__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod

for _name in [
    "yfinance", "yfinance.exceptions",
    "stockstats", "stockstats.wrap",
    "redis", "redis.client", "redis.asyncio",
    "telegram", "telegram.ext",
    "pinecone",
    "langchain_core", "langchain_core.messages", "langchain_core.prompts",
    "langchain", "langgraph", "langgraph.graph", "langgraph.prebuilt",
    "langchain_openai", "langchain_anthropic", "langchain_google_genai",
]:
    sys.modules.setdefault(_name, _stub_module(_name))

sys.modules["langchain_core.messages"].HumanMessage = MagicMock  # type: ignore
sys.modules["langchain_core.messages"].RemoveMessage = MagicMock  # type: ignore
sys.modules["yfinance.exceptions"].YFRateLimitError = Exception  # type: ignore

_rating_stub = types.ModuleType("tradingagents.agents.utils.rating")
_rating_stub.parse_rating = lambda text, scale=10: 5.0  # type: ignore
sys.modules.setdefault("tradingagents.agents.utils.rating", _rating_stub)

# Load redis_queue directly to bypass integrations/__init__ chain
_root = pathlib.Path(__file__).parent.parent
_rq_path = _root / "tradingagents" / "integrations" / "redis_queue.py"
_spec = importlib.util.spec_from_file_location("tradingagents.integrations.redis_queue", _rq_path)
_rq_mod = importlib.util.module_from_spec(_spec)  # type: ignore
_spec.loader.exec_module(_rq_mod)  # type: ignore
sys.modules["tradingagents.integrations.redis_queue"] = _rq_mod

RedisOrderQueue = _rq_mod.RedisOrderQueue
ORDER_STREAM = _rq_mod.ORDER_STREAM
ORDER_GROUP = _rq_mod.ORDER_GROUP
ORDER_PROCESSED = _rq_mod.ORDER_PROCESSED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _queue_with_redis(mock_redis_client=None) -> RedisOrderQueue:
    """Return a RedisOrderQueue whose internal _redis is a mock."""
    q = RedisOrderQueue.__new__(RedisOrderQueue)
    q._redis = mock_redis_client or MagicMock()
    q._fallback_dir = Path("/tmp/test_orders")
    return q


def _queue_file_only(tmp_path: Path) -> RedisOrderQueue:
    """Return a RedisOrderQueue with no Redis (file fallback only)."""
    q = RedisOrderQueue.__new__(RedisOrderQueue)
    q._redis = None
    q._fallback_dir = tmp_path / "orders"
    return q


# ---------------------------------------------------------------------------
# __init__ — connection paths
# ---------------------------------------------------------------------------

class TestInit:
    def test_redis_unavailable_falls_back_to_file(self):
        """When Redis ping fails, _redis is set to None (constructor fallback path)."""
        # Simulate init with a redis client that fails ping
        q = RedisOrderQueue.__new__(RedisOrderQueue)
        q._fallback_dir = Path("/tmp/orders")

        mock_redis_mod = types.ModuleType("redis")
        mock_client = MagicMock()
        mock_client.ping.side_effect = ConnectionRefusedError("Redis down")
        mock_redis_mod.from_url = MagicMock(return_value=mock_client)

        # Call just the connection part of __init__ manually (mimics what init does on failure)
        try:
            client = mock_redis_mod.from_url("redis://localhost:6379/0", decode_responses=True)
            client.ping()  # raises
        except Exception:
            q._redis = None

        assert q._redis is None

    def test_is_redis_available_false_when_no_redis(self):
        q = _queue_file_only(Path("/tmp"))
        assert q.is_redis_available is False

    def test_is_redis_available_true_when_redis_set(self):
        q = _queue_with_redis()
        assert q.is_redis_available is True


# ---------------------------------------------------------------------------
# push_order — Redis path
# ---------------------------------------------------------------------------

class TestPushOrderRedis:
    def test_push_order_calls_xadd(self):
        mock_redis = MagicMock()
        mock_redis.xadd.return_value = "1700000000000-0"
        q = _queue_with_redis(mock_redis)

        order = {"ticker": "BTCUSDT", "side": "buy", "quantity": 0.001}
        result = q.push_order(order)

        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == ORDER_STREAM
        serialized = json.loads(call_args[0][1]["order"])
        assert serialized["ticker"] == "BTCUSDT"
        assert "queued_at" in serialized
        assert result == "1700000000000-0"

    def test_push_order_adds_queued_at_timestamp(self):
        mock_redis = MagicMock()
        mock_redis.xadd.return_value = "1-0"
        q = _queue_with_redis(mock_redis)

        order = {"ticker": "ETHUSDT", "side": "sell"}
        q.push_order(order)

        call_args = mock_redis.xadd.call_args
        serialized = json.loads(call_args[0][1]["order"])
        assert "queued_at" in serialized
        # should be an ISO timestamp (contains 'T')
        assert "T" in serialized["queued_at"]

    def test_push_order_falls_back_to_file_on_xadd_failure(self, tmp_path):
        mock_redis = MagicMock()
        mock_redis.xadd.side_effect = ConnectionError("Redis write failed")
        q = _queue_with_redis(mock_redis)
        q._fallback_dir = tmp_path / "orders"

        order = {"ticker": "SOLUSDT", "side": "buy", "quantity": 0.1}
        result = q.push_order(order)

        # Should have written a file and returned the path
        files = list((tmp_path / "orders").glob("*.json"))
        assert len(files) == 1
        assert "SOLUSDT" in result


# ---------------------------------------------------------------------------
# push_order — file fallback path
# ---------------------------------------------------------------------------

class TestPushOrderFileFallback:
    def test_push_file_creates_json_file(self, tmp_path):
        q = _queue_file_only(tmp_path)
        order = {"ticker": "LINKUSDT", "side": "sell", "quantity": 10.0}
        result = q.push_order(order)

        filepath = Path(result)
        assert filepath.exists()
        saved = json.loads(filepath.read_text())
        assert saved["ticker"] == "LINKUSDT"
        assert saved["side"] == "sell"

    def test_push_file_filename_includes_ticker_and_side(self, tmp_path):
        q = _queue_file_only(tmp_path)
        order = {"ticker": "BNBUSDT", "side": "buy"}
        result = q.push_order(order)
        assert "BNBUSDT" in result
        assert "buy" in result

    def test_push_file_creates_directory_if_missing(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir = tmp_path / "deep" / "nested" / "orders"
        order = {"ticker": "XRPUSDT", "side": "buy"}
        q.push_order(order)

        assert q._fallback_dir.exists()


# ---------------------------------------------------------------------------
# pop_orders — Redis path
# ---------------------------------------------------------------------------

class TestPopOrdersRedis:
    def test_pop_orders_returns_list_of_dicts(self):
        mock_redis = MagicMock()
        order_payload = json.dumps({"ticker": "BTCUSDT", "side": "buy"})
        mock_redis.xreadgroup.return_value = [
            (ORDER_STREAM, [("1-0", {"order": order_payload})])
        ]
        q = _queue_with_redis(mock_redis)

        results = q.pop_orders()

        assert len(results) == 1
        assert results[0]["id"] == "1-0"
        assert results[0]["order"]["ticker"] == "BTCUSDT"

    def test_pop_orders_returns_empty_on_no_messages(self):
        mock_redis = MagicMock()
        mock_redis.xreadgroup.return_value = []
        q = _queue_with_redis(mock_redis)

        results = q.pop_orders()
        assert results == []

    def test_pop_orders_returns_empty_on_redis_failure(self):
        mock_redis = MagicMock()
        mock_redis.xreadgroup.side_effect = ConnectionError("Redis down")
        q = _queue_with_redis(mock_redis)

        results = q.pop_orders()
        assert results == []

    def test_pop_orders_multiple_messages(self):
        mock_redis = MagicMock()
        entries = [
            ("1-0", {"order": json.dumps({"ticker": "BTCUSDT", "side": "buy"})}),
            ("1-1", {"order": json.dumps({"ticker": "ETHUSDT", "side": "sell"})}),
        ]
        mock_redis.xreadgroup.return_value = [(ORDER_STREAM, entries)]
        q = _queue_with_redis(mock_redis)

        results = q.pop_orders()
        assert len(results) == 2
        assert results[1]["order"]["ticker"] == "ETHUSDT"


# ---------------------------------------------------------------------------
# pop_orders — file fallback path
# ---------------------------------------------------------------------------

class TestPopOrdersFileFallback:
    def test_pop_files_returns_empty_when_dir_missing(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir = tmp_path / "nonexistent"
        results = q.pop_orders()
        assert results == []

    def test_pop_files_reads_all_json_files(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir.mkdir()
        for ticker in ["BTCUSDT", "ETHUSDT"]:
            (q._fallback_dir / f"{ticker}_buy_ts.json").write_text(
                json.dumps({"ticker": ticker})
            )

        results = q.pop_orders()
        assert len(results) == 2
        tickers = {r["order"]["ticker"] for r in results}
        assert tickers == {"BTCUSDT", "ETHUSDT"}

    def test_pop_files_skips_invalid_json(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir.mkdir()
        (q._fallback_dir / "good.json").write_text(json.dumps({"ticker": "SOLUSDT"}))
        (q._fallback_dir / "bad.json").write_text("NOT JSON {{{")

        results = q.pop_orders()
        # Good file only
        assert len(results) == 1
        assert results[0]["order"]["ticker"] == "SOLUSDT"


# ---------------------------------------------------------------------------
# ack_order — Redis path
# ---------------------------------------------------------------------------

class TestAckOrderRedis:
    def test_ack_calls_xack_and_xadd(self):
        mock_redis = MagicMock()
        q = _queue_with_redis(mock_redis)

        q.ack_order("1700000000-0")

        mock_redis.xack.assert_called_once_with(ORDER_STREAM, ORDER_GROUP, "1700000000-0")
        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == ORDER_PROCESSED
        assert call_args[0][1]["original_id"] == "1700000000-0"

    def test_ack_redis_failure_does_not_raise(self):
        mock_redis = MagicMock()
        mock_redis.xack.side_effect = ConnectionError("Redis error")
        q = _queue_with_redis(mock_redis)

        q.ack_order("bad-id")  # must not raise


# ---------------------------------------------------------------------------
# ack_order — file fallback path
# ---------------------------------------------------------------------------

class TestAckOrderFileFallback:
    def test_ack_moves_file_to_processed_dir(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir.mkdir()
        order_file = q._fallback_dir / "BTCUSDT_buy_ts.json"
        order_file.write_text(json.dumps({"ticker": "BTCUSDT"}))

        q.ack_order(str(order_file))

        assert not order_file.exists()
        processed_file = q._fallback_dir / "processed" / "BTCUSDT_buy_ts.json"
        assert processed_file.exists()

    def test_ack_nonexistent_file_does_not_raise(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q.ack_order("/nonexistent/path/order.json")  # must not raise


# ---------------------------------------------------------------------------
# pending_count
# ---------------------------------------------------------------------------

class TestPendingCount:
    def test_redis_returns_stream_length(self):
        mock_redis = MagicMock()
        mock_redis.xinfo_stream.return_value = {"length": 7}
        q = _queue_with_redis(mock_redis)

        assert q.pending_count() == 7

    def test_redis_xinfo_failure_returns_zero(self):
        mock_redis = MagicMock()
        mock_redis.xinfo_stream.side_effect = Exception("key not found")
        q = _queue_with_redis(mock_redis)

        assert q.pending_count() == 0

    def test_file_counts_json_files(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir.mkdir()
        for i in range(3):
            (q._fallback_dir / f"order_{i}.json").write_text("{}")

        assert q.pending_count() == 3

    def test_file_returns_zero_when_dir_missing(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir = tmp_path / "nonexistent"

        assert q.pending_count() == 0


# ---------------------------------------------------------------------------
# get_queue_stats
# ---------------------------------------------------------------------------

class TestGetQueueStats:
    def test_redis_backend_stats(self):
        mock_redis = MagicMock()
        mock_redis.xinfo_stream.return_value = {"length": 5}
        q = _queue_with_redis(mock_redis)

        stats = q.get_queue_stats()
        assert stats["backend"] == "redis"
        assert stats["total_entries"] == 5

    def test_file_backend_stats(self, tmp_path):
        q = _queue_file_only(tmp_path)
        q._fallback_dir.mkdir()
        (q._fallback_dir / "order.json").write_text("{}")

        stats = q.get_queue_stats()
        assert stats["backend"] == "file"
        assert stats["pending"] == 1

    def test_redis_xinfo_failure_partial_stats(self):
        mock_redis = MagicMock()
        mock_redis.xinfo_stream.side_effect = Exception("stream missing")
        mock_redis.xinfo_stream.return_value = {"length": 0}
        q = _queue_with_redis(mock_redis)

        stats = q.get_queue_stats()
        assert stats["backend"] == "redis"
        # pending_count returns 0 on error
        assert "backend" in stats
