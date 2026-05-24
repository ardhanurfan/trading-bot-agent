"""Integration tests for tradingagents/integrations/binance_executor.py."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub heavy dependencies not installed in this environment
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
    "langchain", "langgraph", "langgraph.graph",
    "langchain_openai", "langchain_anthropic", "langchain_google_genai",
]:
    sys.modules.setdefault(_name, _stub_module(_name))

sys.modules["yfinance.exceptions"].YFRateLimitError = Exception  # type: ignore


# Load trade_validator directly, bypassing the agents/__init__ chain
def _load_tv():
    root = pathlib.Path(__file__).parent.parent.parent
    tv_path = root / "tradingagents" / "integrations" / "trade_validator.py"
    _rating_stub = types.ModuleType("tradingagents.agents.utils.rating")
    _rating_stub.parse_rating = lambda text, scale=10: 5.0  # type: ignore
    sys.modules.setdefault("tradingagents.agents.utils.rating", _rating_stub)
    spec = importlib.util.spec_from_file_location(
        "tradingagents.integrations.trade_validator", tv_path
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    sys.modules["tradingagents.integrations.trade_validator"] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


_tv = _load_tv()
ExecutableTradeOrder = _tv.ExecutableTradeOrder
OrderSide = _tv.OrderSide


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor(testnet: bool = True):
    """Create a BinanceExecutor with mocked Redis and Telegram."""
    from tradingagents.integrations.binance_executor import BinanceExecutor

    with patch("tradingagents.integrations.binance_executor.RedisOrderQueue"), \
         patch("tradingagents.integrations.binance_executor.TelegramNotifier") as mock_tg:
        mock_tg.return_value.enabled = False
        executor = BinanceExecutor(
            api_key="test_key",
            secret_key="test_secret",
            testnet=testnet,
        )
    return executor


def _make_order(
    ticker: str = "BTCUSDT",
    side: str = "buy",
    quantity: float = 0.001,
    limit_price: float = None,
    stop_loss: float = 40000.0,
    take_profit: float = 45000.0,
    confidence: float = 0.75,
    reasoning: str = "test trade reasoning for unit test purposes",
) -> dict:
    data = {
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "confidence": confidence,
        "reasoning": reasoning,
    }
    if limit_price is not None:
        data["limit_price"] = limit_price
    return data


# ---------------------------------------------------------------------------
# BinanceExecutor initialisation
# ---------------------------------------------------------------------------

class TestBinanceExecutorInit:
    def test_testnet_defaults_true(self):
        from tradingagents.integrations.binance_executor import BinanceExecutor

        with patch("tradingagents.integrations.binance_executor.RedisOrderQueue"), \
             patch("tradingagents.integrations.binance_executor.TelegramNotifier") as mt, \
             patch.dict("os.environ", {"BINANCE_TESTNET": "true"}):
            mt.return_value.enabled = False
            executor = BinanceExecutor(api_key="k", secret_key="s")

        assert executor.testnet is True

    def test_testnet_false_from_env(self):
        from tradingagents.integrations.binance_executor import BinanceExecutor

        with patch("tradingagents.integrations.binance_executor.RedisOrderQueue"), \
             patch("tradingagents.integrations.binance_executor.TelegramNotifier") as mt, \
             patch.dict("os.environ", {"BINANCE_TESTNET": "false"}):
            mt.return_value.enabled = False
            executor = BinanceExecutor(api_key="k", secret_key="s")

        assert executor.testnet is False


# ---------------------------------------------------------------------------
# HOLD order — should skip execution
# ---------------------------------------------------------------------------

class TestHoldOrder:
    def test_hold_does_not_call_client(self):
        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        executor._client = mock_client

        order_item = {
            "id": "hold-1",
            "order": _make_order(side="hold"),
        }

        executor._process_order(order_item)

        mock_client.place_market_order.assert_not_called()
        mock_client.place_limit_order.assert_not_called()
        executor.queue.ack_order.assert_called_once_with("hold-1")


# ---------------------------------------------------------------------------
# BUY / SELL market orders
# ---------------------------------------------------------------------------

class TestMarketOrders:
    def test_buy_market_order_calls_place_market_order(self):
        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        mock_client.place_market_order.return_value = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.001",
            "cummulativeQuoteQty": "42.5",
        }
        executor._client = mock_client

        order_item = {
            "id": "buy-1",
            "order": _make_order(ticker="ETHUSDT", side="buy", quantity=0.1),
        }

        executor._process_order(order_item)

        mock_client.place_market_order.assert_called_once_with(
            symbol="ETHUSDT", side="BUY", quantity=0.1
        )
        executor.queue.ack_order.assert_called_once_with("buy-1")

    def test_sell_market_order_calls_place_market_order_with_sell(self):
        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        mock_client.place_market_order.return_value = {
            "orderId": 54321,
            "status": "FILLED",
            "executedQty": "0.05",
            "cummulativeQuoteQty": "2100.0",
        }
        executor._client = mock_client

        order_item = {
            "id": "sell-1",
            "order": _make_order(ticker="SOLUSDT", side="sell", quantity=0.05),
        }

        executor._process_order(order_item)

        mock_client.place_market_order.assert_called_once_with(
            symbol="SOLUSDT", side="SELL", quantity=0.05
        )


# ---------------------------------------------------------------------------
# Limit orders
# ---------------------------------------------------------------------------

class TestLimitOrders:
    def test_limit_order_calls_place_limit_order(self):
        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        mock_client.place_limit_order.return_value = {
            "orderId": 99999,
            "status": "NEW",
        }
        executor._client = mock_client

        order_item = {
            "id": "limit-1",
            "order": _make_order(ticker="BNBUSDT", side="buy", quantity=1.0, limit_price=350.0,
                                stop_loss=300.0, take_profit=400.0),
        }

        executor._process_order(order_item)

        mock_client.place_limit_order.assert_called_once_with(
            symbol="BNBUSDT", side="BUY", quantity=1.0, price=350.0
        )


# ---------------------------------------------------------------------------
# Ticker format validation (defence-in-depth in _submit_binance_order)
# ---------------------------------------------------------------------------

class TestTickerValidation:
    @pytest.mark.parametrize("valid_ticker", [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ETHBTC", "BNBETH", "SOLUSDC", "BTCBUSD", "ETHFDUSD",
    ])
    def test_valid_binance_tickers_accepted(self, valid_ticker):
        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        mock_client.place_market_order.return_value = {"orderId": 1, "status": "FILLED"}
        executor._client = mock_client

        order_item = {
            "id": "valid-1",
            "order": _make_order(ticker=valid_ticker, side="buy"),
        }

        # Should NOT raise ValueError
        executor._process_order(order_item)

    @pytest.mark.parametrize("invalid_ticker", [
        "AAPL", "MSFT", "BRK-B", "ETH/USDT", "../../BTCUSDT",
        "BTCUSDT;DROP TABLE orders", "", "btcusdt",
    ])
    def test_invalid_tickers_rejected(self, invalid_ticker):
        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        executor._client = mock_client

        # Build a mock order with the bad ticker
        bad_order = MagicMock(spec=ExecutableTradeOrder)
        bad_order.ticker = invalid_ticker
        bad_order.side = OrderSide.BUY
        bad_order.quantity = 0.001
        bad_order.order_type = "market"
        bad_order.price = 0.0

        with pytest.raises(ValueError, match="not a valid Binance spot pair"):
            executor._submit_binance_order(bad_order)

        mock_client.place_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# Decimal quantity handling
# ---------------------------------------------------------------------------

class TestDecimalQuantity:
    @pytest.mark.parametrize("qty", [0.001, 0.5, 1.0, 100.0, 0.00001])
    def test_positive_float_quantities_accepted_by_schema(self, qty):
        order = ExecutableTradeOrder(
            ticker="BTCUSDT",
            side=OrderSide.BUY,
            quantity=qty,
            stop_loss=40000.0,
            take_profit=45000.0,
            confidence=0.75,
            reasoning="decimal test reasoning for validation purposes",
        )
        assert order.quantity == pytest.approx(qty)

    @pytest.mark.parametrize("bad_qty", [-0.001, -1.0, 0.0])
    def test_non_positive_quantity_rejected_by_schema(self, bad_qty):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExecutableTradeOrder(
                ticker="BTCUSDT",
                side=OrderSide.BUY,
                quantity=bad_qty,
                stop_loss=40000.0,
                take_profit=45000.0,
                confidence=0.75,
                reasoning="bad qty test reasoning for validation check",
            )


# ---------------------------------------------------------------------------
# Lazy client property — no API key raises RuntimeError
# ---------------------------------------------------------------------------

class TestClientLazyInit:
    def test_client_raises_when_no_api_key(self):
        from tradingagents.integrations.binance_executor import BinanceExecutor

        with patch("tradingagents.integrations.binance_executor.RedisOrderQueue"), \
             patch("tradingagents.integrations.binance_executor.TelegramNotifier") as mt:
            mt.return_value.enabled = False
            executor = BinanceExecutor(api_key="", secret_key="")

        with pytest.raises(RuntimeError, match="BINANCE_API_KEY"):
            _ = executor.client

    def test_client_creates_binance_client_with_testnet_flag(self):
        from tradingagents.integrations.binance_executor import BinanceExecutor

        with patch("tradingagents.integrations.binance_executor.RedisOrderQueue"), \
             patch("tradingagents.integrations.binance_executor.TelegramNotifier") as mt:
            mt.return_value.enabled = False
            executor = BinanceExecutor(api_key="mykey", secret_key="mysecret", testnet=True)

        with patch("tradingagents.integrations.binance_executor.BinanceClient") as mock_bc:
            mock_bc.return_value = MagicMock()
            _ = executor.client

        mock_bc.assert_called_once_with(api_key="mykey", secret_key="mysecret", testnet=True)

    def test_client_created_only_once(self):
        """Second access to executor.client returns the cached instance."""
        from tradingagents.integrations.binance_executor import BinanceExecutor

        with patch("tradingagents.integrations.binance_executor.RedisOrderQueue"), \
             patch("tradingagents.integrations.binance_executor.TelegramNotifier") as mt:
            mt.return_value.enabled = False
            executor = BinanceExecutor(api_key="k", secret_key="s", testnet=True)

        with patch("tradingagents.integrations.binance_executor.BinanceClient") as mock_bc:
            mock_bc.return_value = MagicMock()
            _ = executor.client
            _ = executor.client

        assert mock_bc.call_count == 1


# ---------------------------------------------------------------------------
# BinanceAPIError propagation
# ---------------------------------------------------------------------------

class TestBinanceAPIError:
    def test_binance_api_error_is_re_raised(self):
        """BinanceAPIError from the client should propagate out of _submit_binance_order."""
        from tradingagents.dataflows.binance_client import BinanceAPIError

        executor = _make_executor()
        mock_client = MagicMock()
        mock_client.place_market_order.side_effect = BinanceAPIError(400, -2010, "INSUFFICIENT_BALANCE")
        executor._client = mock_client

        order = ExecutableTradeOrder(
            ticker="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.001,
            stop_loss=40000.0,
            take_profit=45000.0,
            confidence=0.8,
            reasoning="api error test reasoning for validation check",
        )

        with pytest.raises(BinanceAPIError, match="INSUFFICIENT_BALANCE"):
            executor._submit_binance_order(order)

    def test_process_order_does_not_ack_on_exception(self):
        """When _submit_binance_order raises, the order must NOT be acked."""
        from tradingagents.dataflows.binance_client import BinanceAPIError

        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        mock_client.place_market_order.side_effect = BinanceAPIError(500, -1000, "SERVER_ERROR")
        executor._client = mock_client

        order_item = {
            "id": "fail-1",
            "order": _make_order(ticker="BTCUSDT", side="buy"),
        }

        executor._process_order(order_item)  # should not raise

        executor.queue.ack_order.assert_not_called()

    def test_process_order_does_not_ack_on_invalid_order_data(self):
        """Malformed order data causes validation failure; ack must NOT be called."""
        executor = _make_executor()
        executor.queue = MagicMock()

        bad_item = {
            "id": "bad-order",
            "order": {"ticker": "BTCUSDT", "side": "buy"},  # missing required fields
        }

        executor._process_order(bad_item)  # should not raise

        executor.queue.ack_order.assert_not_called()


# ---------------------------------------------------------------------------
# Notifier integration (when enabled)
# ---------------------------------------------------------------------------

class TestNotifierCalls:
    def test_notifier_send_trade_validated_on_success(self):
        """When notifier is enabled, sync_send_trade_validated should be called after fill."""
        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        mock_client.place_market_order.return_value = {
            "orderId": 77777,
            "status": "FILLED",
            "executedQty": "0.001",
            "cummulativeQuoteQty": "42.5",
        }
        executor._client = mock_client

        executor.notifier = MagicMock()
        executor.notifier.enabled = True

        order_item = {
            "id": "notify-1",
            "order": _make_order(ticker="BTCUSDT", side="buy"),
        }

        executor._process_order(order_item)

        executor.notifier.sync_send_trade_validated.assert_called_once()
        call_kwargs = executor.notifier.sync_send_trade_validated.call_args[1]
        assert call_kwargs["ticker"] == "BTCUSDT"
        assert call_kwargs["side"] == "buy"

    def test_notifier_send_error_on_binance_failure(self):
        """BinanceAPIError triggers notifier.sync_send_error when notifier enabled."""
        from tradingagents.dataflows.binance_client import BinanceAPIError

        executor = _make_executor()
        executor.queue = MagicMock()

        mock_client = MagicMock()
        mock_client.place_market_order.side_effect = BinanceAPIError(400, -1100, "BAD_REQUEST")
        executor._client = mock_client

        executor.notifier = MagicMock()
        executor.notifier.enabled = True

        order_item = {
            "id": "notify-err-1",
            "order": _make_order(ticker="ETHUSDT", side="sell"),
        }

        executor._process_order(order_item)

        executor.notifier.sync_send_error.assert_called_once()


# ---------------------------------------------------------------------------
# run() loop behaviour
# ---------------------------------------------------------------------------

class TestRunLoop:
    def test_run_stops_on_keyboard_interrupt(self):
        """KeyboardInterrupt exits the polling loop cleanly."""
        executor = _make_executor()
        executor.notifier.enabled = False

        mock_queue = MagicMock()
        # First pop raises KeyboardInterrupt
        mock_queue.pop_orders.side_effect = KeyboardInterrupt
        executor.queue = mock_queue

        executor.run()  # must return without raising

        mock_queue.pop_orders.assert_called_once()

    def test_run_processes_multiple_orders_then_stops(self):
        """run() calls _process_order for each item returned by pop_orders."""
        executor = _make_executor()
        executor.notifier.enabled = False

        mock_client = MagicMock()
        mock_client.place_market_order.return_value = {"orderId": 1, "status": "FILLED"}
        executor._client = mock_client

        mock_queue = MagicMock()
        orders = [
            {"id": f"ord-{i}", "order": _make_order(side="buy")}
            for i in range(3)
        ]
        # Return 3 orders on first call, then KeyboardInterrupt
        mock_queue.pop_orders.side_effect = [orders, KeyboardInterrupt]
        executor.queue = mock_queue

        executor.run()

        assert mock_queue.ack_order.call_count == 3

    def test_run_continues_after_non_interrupt_exception(self):
        """A non-KeyboardInterrupt exception is logged and the loop continues."""
        executor = _make_executor()
        executor.notifier.enabled = False
        executor.poll_interval = 0  # no sleep in test

        mock_queue = MagicMock()
        # First pop raises generic error, second raises KeyboardInterrupt to exit
        mock_queue.pop_orders.side_effect = [RuntimeError("redis down"), KeyboardInterrupt]
        executor.queue = mock_queue

        with patch("time.sleep"):  # don't actually sleep
            executor.run()

        assert mock_queue.pop_orders.call_count == 2

