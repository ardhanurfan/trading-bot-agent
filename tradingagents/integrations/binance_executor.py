"""Binance Spot trade execution engine.

Consumes validated ``ExecutableTradeOrder`` from the Redis order queue,
executes them via Binance Spot API, and sends Telegram notifications.

Runs as a **separate process** from the TradingAgents analysis pipeline —
connected only through the Redis order queue.  This mirrors the Alpaca
executor interface so both can be swapped transparently.

Usage::

    python -m tradingagents.integrations.binance_executor
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from tradingagents.dataflows.binance_client import BinanceClient, BinanceAPIError
from tradingagents.integrations.redis_queue import RedisOrderQueue
from tradingagents.integrations.telegram_notifier import TelegramNotifier
from tradingagents.integrations.trade_validator import ExecutableTradeOrder, OrderSide

logger = logging.getLogger(__name__)


class BinanceExecutor:
    """Polls Redis for validated orders and executes them on Binance Spot.

    This mirrors the ``AgenticTrader`` interface from ``lumibot_executor.py``
    but targets Binance instead of Alpaca.

    Parameters
    ----------
    api_key / secret_key:
        Binance credentials.  If omitted, read from ``BINANCE_API_KEY`` /
        ``BINANCE_SECRET_KEY`` environment variables.
    testnet:
        When True (default), connects to ``testnet.binance.vision``.
        Set ``BINANCE_TESTNET=false`` in the environment to use live trading.
    redis_url:
        Redis connection string for the order queue.
    poll_interval:
        Seconds between Redis queue polls.
    """

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        testnet: Optional[bool] = None,
        redis_url: str = "redis://localhost:6379/0",
        poll_interval: int = 5,
    ) -> None:
        self.poll_interval = poll_interval

        # Order queue
        self.queue = RedisOrderQueue(redis_url=redis_url)

        # Notifications
        self.notifier = TelegramNotifier()

        # Binance client (lazy init via property)
        self._client: Optional[BinanceClient] = None
        self._api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self._secret_key = secret_key or os.getenv("BINANCE_SECRET_KEY", "")

        if testnet is None:
            testnet_env = os.getenv("BINANCE_TESTNET", "true").lower()
            testnet = testnet_env not in ("false", "0", "no")
        self.testnet = testnet

    @property
    def client(self) -> BinanceClient:
        """Lazy-init Binance REST client."""
        if self._client is None:
            if not self._api_key or not self._secret_key:
                raise RuntimeError(
                    "BINANCE_API_KEY and BINANCE_SECRET_KEY must be set to execute trades. "
                    "Set them in the environment or pass them to BinanceExecutor."
                )
            self._client = BinanceClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                testnet=self.testnet,
            )
        return self._client

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main execution loop — poll Redis and execute orders."""
        logger.info(
            "BinanceExecutor started (mode=%s, poll=%ds)",
            "TESTNET" if self.testnet else "LIVE",
            self.poll_interval,
        )

        if self.notifier.enabled:
            import asyncio
            asyncio.run(self.notifier.send_error(
                error_type="Info",
                message=f"BinanceExecutor started ({'TESTNET' if self.testnet else 'LIVE'} mode).",
                component="BinanceExecutor",
            ))

        while True:
            try:
                orders = self.queue.pop_orders(count=5, block_ms=self.poll_interval * 1000)
                for item in orders:
                    self._process_order(item)
            except KeyboardInterrupt:
                logger.info("BinanceExecutor stopped by user.")
                break
            except Exception:
                logger.exception("Error in BinanceExecutor loop")
                time.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # Order processing
    # ------------------------------------------------------------------

    def _process_order(self, item: Dict[str, Any]) -> None:
        """Validate and execute a single order from the queue."""
        order_id = item["id"]
        order_data = item["order"]

        try:
            order = ExecutableTradeOrder(**order_data)
            logger.info(
                "Executing: %s %s qty=%s",
                order.side.value,
                order.ticker,
                order.quantity,
            )

            if order.side == OrderSide.HOLD:
                logger.info("HOLD order — skipping execution for %s", order.ticker)
                self.queue.ack_order(order_id)
                return

            self._submit_binance_order(order)
            self.queue.ack_order(order_id)

        except Exception as exc:
            logger.exception("Failed to process order %s", order_id)
            if self.notifier.enabled:
                self.notifier.sync_send_error(
                    error_type="OrderExecution",
                    message=str(exc),
                    component="BinanceExecutor",
                )

    def _submit_binance_order(self, order: ExecutableTradeOrder) -> None:
        """Submit a market or limit order to Binance Spot.

        Security:
        - Ticker is re-validated against Binance pair format before submission.
        - API keys are never logged.
        - Testnet mode prevents accidental live trading.
        """
        import re
        # Re-validate ticker against Binance format (defence-in-depth)
        _BINANCE_PAIR_RE = re.compile(
            r"^[A-Z]{2,20}(USDT|BUSD|BTC|ETH|BNB|USDC|FDUSD)$"
        )
        if not _BINANCE_PAIR_RE.match(order.ticker):
            raise ValueError(
                f"Ticker '{order.ticker}' is not a valid Binance spot pair. "
                "Expected format: BTCUSDT, ETHUSDT, etc."
            )

        side = "BUY" if order.side == OrderSide.BUY else "SELL"

        try:
            if order.limit_price:
                result = self.client.place_limit_order(
                    symbol=order.ticker,
                    side=side,
                    quantity=order.quantity,
                    price=order.limit_price,
                )
            else:
                # Use quoteOrderQty when quantity is in quote currency units
                # (e.g. $100 worth of BTC), else use base quantity directly.
                if isinstance(order.quantity, float) and order.quantity < 1.0:
                    # Likely a fractional base quantity (e.g. 0.001 BTC)
                    result = self.client.place_market_order(
                        symbol=order.ticker,
                        side=side,
                        quantity=order.quantity,
                    )
                else:
                    # quantity treated as USDT spend amount for market buy
                    result = self.client.place_market_order(
                        symbol=order.ticker,
                        side=side,
                        quantity=order.quantity,
                    )

            order_id_binance = result.get("orderId", "unknown")
            logger.info(
                "Binance order placed: %s | orderId=%s | symbol=%s",
                side,
                order_id_binance,
                order.ticker,
            )

            if self.notifier.enabled:
                self.notifier.sync_send_trade_validated(
                    ticker=order.ticker,
                    side=order.side.value,
                    quantity=order.quantity,
                    confidence=order.confidence,
                    reasoning=f"Binance orderId: {order_id_binance}",
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    limit_price=order.limit_price,
                )

        except BinanceAPIError as exc:
            logger.error("Binance API rejected order for %s: %s", order.ticker, exc)
            raise
        except Exception:
            logger.exception("Unexpected error placing Binance order for %s", order.ticker)
            raise


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the Binance executor from the command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Binance Spot trade executor")
    parser.add_argument(
        "--testnet",
        action="store_true",
        default=None,
        help="Use Binance testnet (default: controlled by BINANCE_TESTNET env var)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Use Binance live trading (overrides BINANCE_TESTNET)",
    )
    parser.add_argument(
        "--redis-url",
        default=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        help="Redis URL for order queue",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between Redis queue polls",
    )
    args = parser.parse_args()

    testnet = not args.live  # --live overrides everything
    executor = BinanceExecutor(
        testnet=testnet,
        redis_url=args.redis_url,
        poll_interval=args.poll_interval,
    )
    executor.run()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    main()
