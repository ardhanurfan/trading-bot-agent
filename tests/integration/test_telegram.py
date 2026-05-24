"""Unit tests for TelegramNotifier (no real API calls)."""

import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load telegram_notifier directly to avoid integrations/__init__ chain
# ---------------------------------------------------------------------------

_TN_PATH = (
    Path(__file__).parents[2] / "tradingagents" / "integrations" / "telegram_notifier.py"
)
_tn_spec = importlib.util.spec_from_file_location(
    "tradingagents.integrations.telegram_notifier", _TN_PATH
)
_tn_mod = importlib.util.module_from_spec(_tn_spec)
sys.modules["tradingagents.integrations.telegram_notifier"] = _tn_mod
_tn_spec.loader.exec_module(_tn_mod)

TelegramNotifier = _tn_mod.TelegramNotifier


class TestTelegramNotifierInit:
    """Tests for notifier initialization and configuration."""

    def test_disabled_when_no_credentials(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        assert not notifier.enabled

    def test_disabled_when_partial_credentials(self):
        notifier = TelegramNotifier(bot_token="123:abc", chat_id="")
        assert not notifier.enabled

    def test_enabled_with_credentials(self):
        notifier = TelegramNotifier(bot_token="123:abc", chat_id="-100123")
        assert notifier.enabled

    def test_base_url_format(self):
        notifier = TelegramNotifier(bot_token="123:abc", chat_id="-100123")
        assert notifier.base_url == "https://api.telegram.org/bot123:abc"


class TestTelegramNotifierDisabledSafety:
    """Ensure disabled notifier never raises or sends requests."""

    def test_sync_send_trade_validated_noop(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        # Should not raise
        notifier.sync_send_trade_validated(
            ticker="AAPL",
            side="buy",
            quantity=10,
            confidence=0.8,
            reasoning="Test reasoning text here",
            stop_loss=150.0,
            take_profit=200.0,
        )

    def test_sync_send_error_noop(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        notifier.sync_send_error(
            error_type="TestError",
            message="This is a test error message",
            component="TestComponent",
        )

    def test_sync_send_hold_decision_noop(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        notifier.sync_send_hold_decision(ticker="NVDA", rating="Hold")

    def test_sync_send_pipeline_start_noop(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        notifier.sync_send_pipeline_start(ticker="MSFT", trade_date="2026-05-09")

    def test_sync_send_pipeline_complete_noop(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        notifier.sync_send_pipeline_complete(
            ticker="GOOG", rating="Buy", duration_seconds=120.5
        )

    def test_sync_send_validation_failed_noop(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        notifier.sync_send_validation_failed(
            ticker="TSLA", error="Confidence too low"
        )


# ---------------------------------------------------------------------------
# TestEnvVarFallback
# ---------------------------------------------------------------------------

class TestEnvVarFallback:
    def test_token_read_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-999")
        notifier = TelegramNotifier()
        assert notifier.bot_token == "env-token"
        assert notifier.chat_id == "-999"
        assert notifier.enabled

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        notifier = TelegramNotifier(bot_token="explicit", chat_id="-100")
        assert notifier.bot_token == "explicit"

    def test_missing_env_disabled(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        notifier = TelegramNotifier()
        assert not notifier.enabled


# ---------------------------------------------------------------------------
# TestSendMethod — mock httpx so no real HTTP goes out
# ---------------------------------------------------------------------------

def _make_enabled_notifier():
    return TelegramNotifier(bot_token="123:TEST", chat_id="-100abc")


def _mock_httpx_client(status_code=200, json_resp=None):
    """Return a context-manager-compatible mock for httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=json_resp or {"ok": True})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    mock_httpx = MagicMock()
    mock_httpx.AsyncClient = MagicMock(return_value=mock_client)
    return mock_httpx, mock_client


class TestSendMethod:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_send_disabled_returns_empty(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        result = self._run(notifier._send("hello"))
        assert result == {}

    def test_send_httpx_none_returns_empty(self):
        notifier = _make_enabled_notifier()
        with patch.object(_tn_mod, "httpx", None):
            result = self._run(notifier._send("hello"))
        assert result == {}

    def test_send_makes_post_request(self):
        mock_httpx, mock_client = _mock_httpx_client()
        notifier = _make_enabled_notifier()
        with patch.object(_tn_mod, "httpx", mock_httpx):
            result = self._run(notifier._send("test message"))
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["chat_id"] == "-100abc"
        assert call_kwargs["json"]["text"] == "test message"

    def test_send_exception_returns_empty_dict(self):
        """HTTP errors are caught; send() must return {} not raise."""
        mock_httpx, mock_client = _mock_httpx_client()
        mock_client.post.side_effect = RuntimeError("network down")
        notifier = _make_enabled_notifier()
        with patch.object(_tn_mod, "httpx", mock_httpx):
            result = self._run(notifier._send("message"))
        assert result == {}


# ---------------------------------------------------------------------------
# TestMessageContent — verify HTML structure passed to _send
# ---------------------------------------------------------------------------

class TestMessageContent:
    def _run(self, coro):
        return asyncio.run(coro)

    def _captured_message(self, notifier, coro):
        """Run coro and capture the text arg passed to _send."""
        sent = []

        async def _fake_send(text, **_kw):
            sent.append(text)
            return {}

        notifier._send = _fake_send
        self._run(coro)
        return sent[0] if sent else None

    def test_trade_validated_contains_ticker(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_trade_validated(
            ticker="BTCUSDT", side="buy", quantity=1, confidence=0.85,
            reasoning="Strong setup", stop_loss=40000.0, take_profit=48000.0,
        ))
        assert "BTCUSDT" in msg

    def test_trade_validated_contains_side(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_trade_validated(
            ticker="ETHUSDT", side="sell", quantity=2, confidence=0.75,
            reasoning="Bearish pattern", stop_loss=4000.0, take_profit=3000.0,
        ))
        assert "SELL" in msg

    def test_trade_validated_reasoning_truncated(self):
        """Reasoning >300 chars should be truncated in the message."""
        n = _make_enabled_notifier()
        long_reasoning = "X" * 400
        msg = self._captured_message(n, n.send_trade_validated(
            ticker="SOLUSDT", side="buy", quantity=5, confidence=0.8,
            reasoning=long_reasoning, stop_loss=100.0, take_profit=130.0,
        ))
        # The truncated text should be 300 chars of X, not 400
        assert "X" * 301 not in msg
        assert "X" * 300 in msg

    def test_trade_validated_limit_price_line_present(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_trade_validated(
            ticker="BTCUSDT", side="buy", quantity=1, confidence=0.9,
            reasoning="Entry at limit", stop_loss=40000.0, take_profit=50000.0,
            limit_price=44000.0,
        ))
        assert "44,000.00" in msg

    def test_trade_validated_no_limit_price_line_absent(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_trade_validated(
            ticker="BTCUSDT", side="buy", quantity=1, confidence=0.9,
            reasoning="Market order setup", stop_loss=40000.0, take_profit=50000.0,
            limit_price=None,
        ))
        assert "Limit:" not in msg

    def test_trade_executed_pnl_positive_emoji(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_trade_executed(
            ticker="BTCUSDT", side="buy", quantity=1, price=45000.0, pnl=500.0
        ))
        assert "📈" in msg

    def test_trade_executed_pnl_negative_emoji(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_trade_executed(
            ticker="ETHUSDT", side="sell", quantity=2, price=2500.0, pnl=-200.0
        ))
        assert "📉" in msg

    def test_trade_executed_no_pnl_omitted(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_trade_executed(
            ticker="SOLUSDT", side="buy", quantity=5, price=120.0
        ))
        assert "PnL" not in msg

    def test_hold_decision_message(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_hold_decision("ADAUSDT", "Hold"))
        assert "ADAUSDT" in msg
        assert "NO TRADE" in msg

    def test_validation_failed_message(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_validation_failed("DOTUSDT", "qty too low"))
        assert "DOTUSDT" in msg
        assert "VALIDATION FAILED" in msg

    def test_send_error_message(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_error(
            error_type="TestErr", message="Something went wrong", component="Trader"
        ))
        assert "TestErr" in msg
        assert "Trader" in msg

    def test_send_error_message_truncated(self):
        """Error details >500 chars should be truncated."""
        n = _make_enabled_notifier()
        long_msg = "E" * 600
        msg = self._captured_message(n, n.send_error(
            error_type="Long", message=long_msg, component="Loop"
        ))
        assert "E" * 501 not in msg

    def test_pipeline_start_message(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_pipeline_start("BTCUSDT", "2024-01-01"))
        assert "BTCUSDT" in msg
        assert "ANALYSIS STARTED" in msg

    def test_pipeline_complete_message(self):
        n = _make_enabled_notifier()
        msg = self._captured_message(n, n.send_pipeline_complete("ETHUSDT", "Buy", 45.2))
        assert "ETHUSDT" in msg
        assert "Buy" in msg


# ---------------------------------------------------------------------------
# TestRunAsync
# ---------------------------------------------------------------------------

class TestRunAsync:
    def test_run_async_no_event_loop(self):
        """_run_async should work when there is no running event loop."""
        notifier = TelegramNotifier(bot_token="", chat_id="")
        called = []

        async def _coro():
            called.append(True)

        TelegramNotifier._run_async(_coro())
        assert called

    def test_sync_wrappers_do_not_raise(self):
        """All sync_* wrappers should complete without exception."""
        notifier = TelegramNotifier(bot_token="", chat_id="")
        notifier.sync_send_trade_validated(
            ticker="BTCUSDT", side="buy", quantity=1,
            confidence=0.8, reasoning="Test", stop_loss=40000.0, take_profit=50000.0,
        )
        notifier.sync_send_hold_decision(ticker="ETHUSDT", rating="Hold")
        notifier.sync_send_validation_failed(ticker="SOLUSDT", error="low qty")
        notifier.sync_send_pipeline_start(ticker="BNBUSDT", trade_date="2024-01-01")
        notifier.sync_send_pipeline_complete(ticker="BNBUSDT", rating="Sell", duration_seconds=30.0)
        notifier.sync_send_error(error_type="Test", message="msg", component="X")

