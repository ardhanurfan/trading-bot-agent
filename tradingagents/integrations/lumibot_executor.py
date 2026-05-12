"""Lumibot-based trade execution engine.

Consumes validated ``ExecutableTradeOrder`` from the Redis order queue,
executes them via Alpaca API, and sends Telegram notifications on fill.

Runs as a **separate process** from TradingAgents analysis — connected
only through the Redis order queue.  This separation is by design:
Lumibot's event loop (``Strategy.run()``) conflicts with LangGraph's
execution model.

Usage::

    python -m tradingagents.integrations.lumibot_executor
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from lumibot.brokers import Alpaca
    from lumibot.strategies import Strategy
    from lumibot.traders import Trader

    _LUMIBOT_AVAILABLE = True
except ImportError:
    _LUMIBOT_AVAILABLE = False
    logger.info("lumibot not installed — executor unavailable.")

from tradingagents.integrations.redis_queue import RedisOrderQueue
from tradingagents.integrations.telegram_notifier import TelegramNotifier
from tradingagents.integrations.trade_validator import ExecutableTradeOrder


class AgenticTrader:
    """Polls Redis for validated orders and executes via Alpaca.

    This is a simplified executor that doesn't use Lumibot's Strategy
    pattern directly — instead it polls the Redis queue in a loop and
    uses Alpaca's REST API.  This avoids Lumibot's event loop complexity
    while still supporting paper and live trading.
    """

    def __init__(
        self,
        alpaca_api_key: str = "",
        alpaca_secret_key: str = "",
        paper: bool = True,
        redis_url: str = "redis://localhost:6379/0",
        poll_interval: int = 5,
    ):
        self.api_key = alpaca_api_key or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = alpaca_secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        self.paper = paper
        self.poll_interval = poll_interval

        # Order queue
        self.queue = RedisOrderQueue(redis_url=redis_url)

        # Notifications
        self.notifier = TelegramNotifier()

        # Alpaca client (lazy init)
        self._alpaca = None

    @property
    def alpaca(self):
        """Lazy-init Alpaca REST client."""
        if self._alpaca is None:
            try:
                from alpaca.trading.client import TradingClient
                self._alpaca = TradingClient(
                    api_key=self.api_key,
                    secret_key=self.secret_key,
                    paper=self.paper,
                )
            except ImportError:
                logger.error(
                    "alpaca-py not installed. Install with: pip install alpaca-py"
                )
                raise
        return self._alpaca

    def run(self) -> None:
        """Main execution loop — poll Redis and execute orders."""
        logger.info(
            "AgenticTrader started (mode=%s, poll=%ds)",
            "PAPER" if self.paper else "LIVE",
            self.poll_interval,
        )

        if self.notifier.enabled:
            import asyncio
            asyncio.run(self.notifier.send_error(
                error_type="Info",
                message=f"Executor started in {'PAPER' if self.paper else 'LIVE'} mode.",
                component="AgenticTrader",
            ))

        while True:
            try:
                orders = self.queue.pop_orders(count=5, block_ms=self.poll_interval * 1000)
                for item in orders:
                    self._process_order(item)
            except KeyboardInterrupt:
                logger.info("Executor stopped by user.")
                break
            except Exception:
                logger.exception("Error in executor loop")
                time.sleep(self.poll_interval)

    def _process_order(self, item: Dict[str, Any]) -> None:
        """Validate and execute a single order from the queue."""
        order_id = item["id"]
        order_data = item["order"]

        try:
            order = ExecutableTradeOrder(**order_data)
            logger.info("Executing: %s %s x%d", order.side.value, order.ticker, order.quantity)

            if order.side.value == "hold":
                logger.info("HOLD order — skipping execution for %s", order.ticker)
                self.queue.ack_order(order_id)
                return

            self._submit_alpaca_order(order)
            self.queue.ack_order(order_id)

        except Exception as e:
            logger.exception("Failed to process order %s", order_id)
            if self.notifier.enabled:
                self.notifier.sync_send_error(
                    error_type="OrderExecution",
                    message=str(e),
                    component="AgenticTrader",
                )

    def _submit_alpaca_order(self, order: ExecutableTradeOrder) -> None:
        """Submit an order to Alpaca."""
        try:
            from alpaca.trading.requests import (
                MarketOrderRequest,
                LimitOrderRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce

            side = OrderSide.BUY if order.side.value == "buy" else OrderSide.SELL

            if order.limit_price:
                req = LimitOrderRequest(
                    symbol=order.ticker,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=order.limit_price,
                )
            else:
                req = MarketOrderRequest(
                    symbol=order.ticker,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )

            result = self.alpaca.submit_order(req)
            logger.info("Order submitted to Alpaca: %s", result.id)

            if self.notifier.enabled:
                self.notifier.sync_send_trade_validated(
                    ticker=order.ticker,
                    side=order.side.value,
                    quantity=order.quantity,
                    confidence=order.confidence,
                    reasoning=f"Alpaca order ID: {result.id}",
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    limit_price=order.limit_price,
                )

        except ImportError:
            logger.error("alpaca-py not installed for order submission")
        except Exception:
            logger.exception("Alpaca order submission failed for %s", order.ticker)
            raise


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """Run the executor from the command line."""
    import argparse

    parser = argparse.ArgumentParser(description="TradingAgents Executor")
    parser.add_argument("--paper", action="store_true", default=True, help="Paper trading mode")
    parser.add_argument("--live", action="store_true", help="Live trading mode")
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--poll-interval", type=int, default=5)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    executor = AgenticTrader(
        paper=not args.live,
        redis_url=args.redis_url,
        poll_interval=args.poll_interval,
    )
    executor.run()


if __name__ == "__main__":
    main()
