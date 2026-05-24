"""Tests for crypto ticker validation in ExecutableTradeOrder and safe_ticker_component.

Covers:
- Binance pair format acceptance/rejection (trade_validator schema)
- safe_ticker_component path-traversal blocking
- Float quantity validation (gt=0)
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Load trade_validator directly (bypassing the package __init__ chain).
# tradingagents.integrations.__init__ → agents → langchain (not installed).
# We load the module file directly via importlib.util to avoid the chain.
# ---------------------------------------------------------------------------

def _load_trade_validator():
    """Load trade_validator without triggering agents/__init__ imports."""
    import os, pathlib

    root = pathlib.Path(__file__).parent.parent
    tv_path = root / "tradingagents" / "integrations" / "trade_validator.py"

    # Pre-stub the one dep that trade_validator itself needs at module level
    # (tradingagents.agents.utils.rating.parse_rating)
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


_tv = _load_trade_validator()
ExecutableTradeOrder = _tv.ExecutableTradeOrder
OrderSide = _tv.OrderSide


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _make_order(
    ticker: str = "BTCUSDT",
    side: str = "buy",
    quantity: float = 0.001,
    confidence: float = 0.75,
) -> dict:
    """Minimal valid order dict for schema testing."""
    return {
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "stop_loss": 40000.0,
        "take_profit": 45000.0,
        "confidence": confidence,
        "reasoning": "unit test reasoning for ticker validation purposes",
    }


# ---------------------------------------------------------------------------
# ExecutableTradeOrder — ticker pattern
# ---------------------------------------------------------------------------

class TestExecutableTradeOrderTickerPattern:

    @pytest.mark.parametrize("ticker", [
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "ADAUSDT",
        "DOTUSDT",
        "MATICUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "ETHBTC",
        "BNBETH",
        "SOLUSDC",
        "BTCBUSD",
        "ETHFDUSD",
        "BNBBUSD",
        "XRPBNB",
    ])
    def test_valid_crypto_pairs_accepted(self, ticker):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        order = ExecutableTradeOrder(**_make_order(ticker=ticker))
        assert order.ticker == ticker

    @pytest.mark.parametrize("bad_ticker", [
        # Legacy stock formats
        "AAPL",
        "MSFT",
        "GOOGL",
        "BRK-B",
        "BRK.B",
        "TSLA",
        # Path traversal attempts
        "../../BTCUSDT",
        "../ETHUSDT",
        "/BTCUSDT",
        "BTCUSDT/../../etc/passwd",
        # Injection attempts
        "BTCUSDT;DROP",
        "BTC USDT",
        "BTC\nUSDT",
        # Lowercase (Binance uses uppercase only)
        "btcusdt",
        "ethusdt",
        # Invalid suffix
        "BTCEUR",
        "ETHGBP",
        "BTCJPY",
        # Empty / too short
        "",
        "BT",
        "A",
    ])
    def test_invalid_tickers_rejected(self, bad_ticker):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        with pytest.raises(ValidationError):
            ExecutableTradeOrder(**_make_order(ticker=bad_ticker))


# ---------------------------------------------------------------------------
# safe_ticker_component — path traversal blocking
# ---------------------------------------------------------------------------

class TestSafeTickerComponentWithCrypto:

    @pytest.mark.parametrize("safe_ticker", [
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
        "SOLUSDT",
        "XRPUSDT",
    ])
    def test_valid_crypto_tickers_pass_safe_component(self, safe_ticker):
        from tradingagents.dataflows.utils import safe_ticker_component

        result = safe_ticker_component(safe_ticker)
        assert result == safe_ticker

    @pytest.mark.parametrize("traversal", [
        "../../BTCUSDT",
        "../ETHUSDT",
        "BTCUSDT/../../etc",
        "BTCUSDT\x00",
        "BTC USDT",
    ])
    def test_traversal_strings_blocked(self, traversal):
        from tradingagents.dataflows.utils import safe_ticker_component

        with pytest.raises((ValueError, TypeError)):
            safe_ticker_component(traversal)

    def test_crypto_symbol_does_not_escape_path_join(self):
        """Joining a crypto symbol with a directory must not escape the directory."""
        import os
        from tradingagents.dataflows.utils import safe_ticker_component

        base = "/tmp/cache"
        symbol = safe_ticker_component("BTCUSDT")
        full_path = os.path.join(base, symbol)
        assert full_path.startswith(base)


# ---------------------------------------------------------------------------
# Float quantity validation
# ---------------------------------------------------------------------------

class TestFloatQuantityValidation:

    @pytest.mark.parametrize("qty", [
        0.00001,   # very small (e.g. BTC sat-level)
        0.001,     # typical BTC buy
        0.1,       # ETH fraction
        0.5,
        1.0,
        10.0,
        100.0,
        1000.0,    # SOL-scale
        10000.0,   # high-volume asset
    ])
    def test_positive_float_quantities_accepted(self, qty):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        order = ExecutableTradeOrder(**_make_order(quantity=qty))
        assert order.quantity == pytest.approx(qty)

    @pytest.mark.parametrize("bad_qty", [
        0.0,
        -0.001,
        -1.0,
        -100.0,
    ])
    def test_non_positive_quantities_rejected(self, bad_qty):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        with pytest.raises(ValidationError):
            ExecutableTradeOrder(**_make_order(quantity=bad_qty))


# ---------------------------------------------------------------------------
# Confidence threshold validation
# ---------------------------------------------------------------------------

class TestConfidenceThreshold:
    """The schema enforces minimum confidence of 0.60."""

    def test_high_confidence_accepted(self):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        order = ExecutableTradeOrder(**_make_order(confidence=0.85))
        assert order.confidence == pytest.approx(0.85)

    def test_minimum_confidence_boundary(self):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        order = ExecutableTradeOrder(**_make_order(confidence=0.60))
        assert order.confidence == pytest.approx(0.60)

    def test_below_minimum_confidence_rejected(self):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        with pytest.raises(ValidationError, match="minimum threshold"):
            ExecutableTradeOrder(**_make_order(confidence=0.59))

    def test_zero_confidence_rejected(self):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        with pytest.raises(ValidationError):
            ExecutableTradeOrder(**_make_order(confidence=0.0))


# ---------------------------------------------------------------------------
# OrderSide enum
# ---------------------------------------------------------------------------

class TestOrderSide:
    def test_buy_sell_hold_accepted(self):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder, OrderSide

        for side in ("buy", "sell", "hold"):
            order = ExecutableTradeOrder(**_make_order(side=side))
            assert order.side.value == side

    def test_invalid_side_rejected(self):
        from tradingagents.integrations.trade_validator import ExecutableTradeOrder

        with pytest.raises(ValidationError):
            ExecutableTradeOrder(**_make_order(side="short"))
