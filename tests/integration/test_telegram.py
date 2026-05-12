"""Unit tests for TelegramNotifier (no real API calls)."""

import pytest

from tradingagents.integrations.telegram_notifier import TelegramNotifier


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
