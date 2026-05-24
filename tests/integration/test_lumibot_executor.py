"""Tests for tradingagents.integrations.lumibot_executor.AgenticTrader."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps then load lumibot_executor via spec_from_file_location
# ---------------------------------------------------------------------------

_INTEG_DIR = Path(__file__).parents[2] / "tradingagents" / "integrations"


def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__path__ = []
    mod.__package__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# Lumibot (not installed)
for _pkg in ["lumibot", "lumibot.brokers", "lumibot.strategies", "lumibot.traders"]:
    sys.modules.setdefault(_pkg, _stub(_pkg))

# Redis (not installed) - needed by redis_queue
for _pkg in ["redis", "redis.client", "redis.asyncio"]:
    sys.modules.setdefault(_pkg, _stub(_pkg))

# Minimal tradingagents stubs
for _pkg in ["tradingagents", "tradingagents.integrations",
             "tradingagents.agents", "tradingagents.agents.utils"]:
    sys.modules.setdefault(_pkg, _stub(_pkg))

# Stub rating module so trade_validator loads
_rating_mod = _stub("tradingagents.agents.utils.rating")
_rating_mod.parse_rating = lambda text, **_kw: "Hold"
sys.modules.setdefault("tradingagents.agents.utils.rating", _rating_mod)

# Load real redis_queue, telegram_notifier, trade_validator via spec
def _load_module(dotted_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(dotted_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod

_rq_mod = _load_module(
    "tradingagents.integrations.redis_queue", _INTEG_DIR / "redis_queue.py"
)
_tn_mod = _load_module(
    "tradingagents.integrations.telegram_notifier", _INTEG_DIR / "telegram_notifier.py"
)
_tv_mod = _load_module(
    "tradingagents.integrations.trade_validator", _INTEG_DIR / "trade_validator.py"
)

# Now load lumibot_executor
_le_mod = _load_module(
    "tradingagents.integrations.lumibot_executor", _INTEG_DIR / "lumibot_executor.py"
)

AgenticTrader = _le_mod.AgenticTrader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trader(**overrides):
    defaults = dict(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
        paper=True,
        redis_url="redis://localhost:6379/0",
        poll_interval=5,
    )
    defaults.update(overrides)
    with patch.object(_rq_mod, "redis"):
        return AgenticTrader(**defaults)


def _make_order_item(ticker="BTCUSDT", side="buy", quantity=0.001,
                     confidence=0.8, stop_loss=40000.0, take_profit=50000.0):
    """Build a queue item dict matching what RedisOrderQueue.pop_orders returns."""
    return {
        "id": "order-001",
        "order": {
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "limit_price": None,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": confidence,
            "reasoning": "Test order for executor unit tests",
        },
    }


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------

class TestInit:
    def test_default_paper_mode(self):
        trader = _make_trader()
        assert trader.paper is True

    def test_custom_poll_interval(self):
        trader = _make_trader(poll_interval=10)
        assert trader.poll_interval == 10

    def test_alpaca_starts_none(self):
        trader = _make_trader()
        assert trader._alpaca is None

    def test_api_keys_stored(self):
        trader = _make_trader(alpaca_api_key="ak", alpaca_secret_key="sk")
        assert trader.api_key == "ak"
        assert trader.secret_key == "sk"

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "env-ak")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "env-sk")
        trader = _make_trader(alpaca_api_key="", alpaca_secret_key="")
        assert trader.api_key == "env-ak"
        assert trader.secret_key == "env-sk"


# ---------------------------------------------------------------------------
# TestAlpacaProperty
# ---------------------------------------------------------------------------

class TestAlpacaProperty:
    def test_raises_import_error_when_alpaca_not_installed(self):
        trader = _make_trader()
        with pytest.raises(ImportError):
            _ = trader.alpaca

    def test_lazy_init_caches_client(self):
        trader = _make_trader()
        mock_client = MagicMock()
        mock_trading_client_cls = MagicMock(return_value=mock_client)
        mock_alpaca_pkg = _stub("alpaca")
        mock_trading_mod = _stub("alpaca.trading")
        mock_trading_client_mod = _stub("alpaca.trading.client",
                                        TradingClient=mock_trading_client_cls)
        with patch.dict(sys.modules, {
            "alpaca": mock_alpaca_pkg,
            "alpaca.trading": mock_trading_mod,
            "alpaca.trading.client": mock_trading_client_mod,
        }):
            first = trader.alpaca
            second = trader.alpaca

        assert first is second
        mock_trading_client_cls.assert_called_once()


# ---------------------------------------------------------------------------
# TestProcessOrder
# ---------------------------------------------------------------------------

class TestProcessOrder:
    def test_hold_order_acked_without_alpaca(self):
        trader = _make_trader()
        trader.queue = MagicMock()
        item = _make_order_item(side="hold")

        trader._process_order(item)

        trader.queue.ack_order.assert_called_once_with("order-001")
        # alpaca is never accessed for HOLD orders
        assert trader._alpaca is None

    def test_invalid_order_data_logs_error(self):
        trader = _make_trader()
        trader.queue = MagicMock()
        bad_item = {"id": "bad-001", "order": {"ticker": "invalid"}}

        trader._process_order(bad_item)  # must not raise

        trader.queue.ack_order.assert_not_called()

    def test_notifier_called_on_error(self):
        trader = _make_trader()
        trader.queue = MagicMock()
        trader.notifier = MagicMock()
        trader.notifier.enabled = True

        bad_item = {"id": "bad-002", "order": {"ticker": "invalid"}}
        trader._process_order(bad_item)

        trader.notifier.sync_send_error.assert_called_once()

    def test_valid_buy_order_calls_submit(self):
        trader = _make_trader()
        trader.queue = MagicMock()
        trader._submit_alpaca_order = MagicMock()

        trader._process_order(_make_order_item(side="buy"))

        trader._submit_alpaca_order.assert_called_once()
        trader.queue.ack_order.assert_called_once_with("order-001")

    def test_exception_in_submit_caught(self):
        trader = _make_trader()
        trader.queue = MagicMock()
        trader._submit_alpaca_order = MagicMock(side_effect=RuntimeError("Alpaca down"))

        trader._process_order(_make_order_item(side="buy"))  # must not propagate

        trader.queue.ack_order.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunLoop
# ---------------------------------------------------------------------------

class TestRunLoop:
    def test_keyboard_interrupt_breaks_loop(self):
        trader = _make_trader()
        trader.queue = MagicMock()
        trader.queue.pop_orders.side_effect = KeyboardInterrupt

        trader.run()  # must return without raising

    def test_exception_in_loop_is_recovered(self):
        """Exception during pop_orders should not terminate the loop permanently.
        We simulate: first call raises, second raises KeyboardInterrupt to break.
        """
        trader = _make_trader(poll_interval=0)
        trader.queue = MagicMock()
        trader.queue.pop_orders.side_effect = [
            RuntimeError("transient error"),
            KeyboardInterrupt,
        ]

        with patch("time.sleep"):
            trader.run()  # recovers from RuntimeError, exits on KeyboardInterrupt

    def test_orders_processed_in_loop(self):
        trader = _make_trader(poll_interval=0)
        trader.queue = MagicMock()
        trader._process_order = MagicMock()
        trader.queue.pop_orders.side_effect = [
            [_make_order_item()],
            KeyboardInterrupt,
        ]

        trader.run()

        trader._process_order.assert_called_once()
