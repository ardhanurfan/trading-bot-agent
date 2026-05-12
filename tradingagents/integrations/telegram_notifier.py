"""Telegram Bot API integration for trade alerts and error notifications.

Sends structured HTML messages to a configured Telegram chat when:
- A trade order is validated and/or executed
- A validation failure occurs (order rejected)
- A system error is raised in any component

All methods are async and use ``httpx`` for non-blocking HTTP.
Callers in synchronous code can use ``asyncio.run()`` or the
convenience ``sync_*`` wrappers provided below.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]
    logger.warning(
        "httpx not installed — Telegram notifications will be disabled. "
        "Install with: pip install httpx"
    )


class TelegramNotifier:
    """Send structured alerts to a Telegram chat via the Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._enabled = bool(self.bot_token and self.chat_id)

        if not self._enabled:
            logger.info(
                "TelegramNotifier disabled — TELEGRAM_BOT_TOKEN or "
                "TELEGRAM_CHAT_ID not set."
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Core send
    # ------------------------------------------------------------------

    async def _send(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> dict:
        """Send a message to the configured Telegram chat."""
        if not self._enabled:
            logger.debug("Telegram send skipped (disabled).")
            return {}

        if httpx is None:
            logger.warning("httpx unavailable — cannot send Telegram message.")
            return {}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_notification": disable_notification,
                    },
                )
                response.raise_for_status()
                return response.json()
        except Exception:
            logger.exception("Failed to send Telegram message")
            return {}

    # ------------------------------------------------------------------
    # Trade notifications
    # ------------------------------------------------------------------

    async def send_trade_validated(
        self,
        ticker: str,
        side: str,
        quantity: int,
        confidence: float,
        reasoning: str,
        stop_loss: float,
        take_profit: float,
        limit_price: Optional[float] = None,
    ) -> None:
        """Notify that a trade order passed validation and is queued."""
        emoji = "🟢" if side.upper() == "BUY" else "🔴"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        price_line = f"💰 Limit: <code>${limit_price:,.2f}</code>\n" if limit_price else ""

        msg = (
            f"{emoji} <b>ORDER VALIDATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Ticker:</b> <code>{ticker}</code>\n"
            f"📊 <b>Side:</b> <code>{side.upper()}</code>\n"
            f"📦 <b>Quantity:</b> <code>{quantity}</code>\n"
            f"{price_line}"
            f"🎯 <b>Confidence:</b> <code>{confidence:.0%}</code>\n"
            f"🛑 <b>Stop Loss:</b> <code>${stop_loss:,.2f}</code>\n"
            f"🏁 <b>Take Profit:</b> <code>${take_profit:,.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <b>Reasoning:</b>\n<i>{reasoning[:300]}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 <code>{ts}</code>"
        )
        await self._send(msg)

    async def send_trade_executed(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price: float,
        pnl: Optional[float] = None,
    ) -> None:
        """Notify that a trade was filled by the broker."""
        emoji = "🟢" if side.upper() == "BUY" else "🔴"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        msg = (
            f"{emoji} <b>TRADE FILLED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <code>{ticker}</code> | <code>{side.upper()}</code> "
            f"x<code>{quantity}</code> @ <code>${price:,.2f}</code>\n"
        )
        if pnl is not None:
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            msg += f"{pnl_emoji} <b>PnL:</b> <code>${pnl:,.2f}</code>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━\n🕐 <code>{ts}</code>"

        await self._send(msg)

    async def send_hold_decision(self, ticker: str, rating: str) -> None:
        """Notify that the analysis resulted in a HOLD (no trade)."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"⏸️ <b>NO TRADE — {rating.upper()}</b>\n"
            f"📌 <code>{ticker}</code> — Analysis complete, no order generated.\n"
            f"🕐 <code>{ts}</code>"
        )
        await self._send(msg, disable_notification=True)

    async def send_validation_failed(
        self,
        ticker: str,
        error: str,
    ) -> None:
        """Notify that a trade order failed Pydantic validation."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"⚠️ <b>VALIDATION FAILED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Ticker:</b> <code>{ticker}</code>\n"
            f"❌ <b>Error:</b>\n<i>{error[:500]}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 <code>{ts}</code>"
        )
        await self._send(msg)

    # ------------------------------------------------------------------
    # System notifications
    # ------------------------------------------------------------------

    async def send_error(
        self,
        error_type: str,
        message: str,
        component: str = "Unknown",
    ) -> None:
        """Send a system error notification."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"🚨 <b>SYSTEM ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚙️ <b>Component:</b> <code>{component}</code>\n"
            f"❌ <b>Type:</b> <code>{error_type}</code>\n"
            f"📝 <b>Details:</b>\n<i>{message[:500]}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 <code>{ts}</code>"
        )
        await self._send(msg)

    async def send_pipeline_start(self, ticker: str, trade_date: str) -> None:
        """Notify that an analysis pipeline has started."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"🚀 <b>ANALYSIS STARTED</b>\n"
            f"📌 <code>{ticker}</code> | Date: <code>{trade_date}</code>\n"
            f"🕐 <code>{ts}</code>"
        )
        await self._send(msg, disable_notification=True)

    async def send_pipeline_complete(
        self,
        ticker: str,
        rating: str,
        duration_seconds: float,
    ) -> None:
        """Notify that an analysis pipeline has completed."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"✅ <b>ANALYSIS COMPLETE</b>\n"
            f"📌 <code>{ticker}</code> | Rating: <b>{rating}</b>\n"
            f"⏱️ Duration: <code>{duration_seconds:.1f}s</code>\n"
            f"🕐 <code>{ts}</code>"
        )
        await self._send(msg, disable_notification=True)

    # ------------------------------------------------------------------
    # Synchronous convenience wrappers
    # ------------------------------------------------------------------

    def sync_send_trade_validated(self, **kwargs) -> None:
        """Synchronous wrapper for send_trade_validated."""
        self._run_async(self.send_trade_validated(**kwargs))

    def sync_send_error(self, **kwargs) -> None:
        """Synchronous wrapper for send_error."""
        self._run_async(self.send_error(**kwargs))

    def sync_send_hold_decision(self, **kwargs) -> None:
        """Synchronous wrapper for send_hold_decision."""
        self._run_async(self.send_hold_decision(**kwargs))

    def sync_send_validation_failed(self, **kwargs) -> None:
        """Synchronous wrapper for send_validation_failed."""
        self._run_async(self.send_validation_failed(**kwargs))

    def sync_send_pipeline_start(self, **kwargs) -> None:
        """Synchronous wrapper for send_pipeline_start."""
        self._run_async(self.send_pipeline_start(**kwargs))

    def sync_send_pipeline_complete(self, **kwargs) -> None:
        """Synchronous wrapper for send_pipeline_complete."""
        self._run_async(self.send_pipeline_complete(**kwargs))

    @staticmethod
    def _run_async(coro) -> None:
        """Run an async coroutine from synchronous code safely."""
        try:
            loop = asyncio.get_running_loop()
            # Already in an async context — schedule as a task
            loop.create_task(coro)
        except RuntimeError:
            # No running loop — safe to use asyncio.run
            asyncio.run(coro)
