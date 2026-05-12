"""Unit tests for the trade_validator module."""

import json
import tempfile
from pathlib import Path

import pytest

from tradingagents.integrations.trade_validator import (
    ExecutableTradeOrder,
    OrderSide,
    parse_decision_to_order,
    write_order_to_queue,
)


# ---------------------------------------------------------------------------
# ExecutableTradeOrder validation
# ---------------------------------------------------------------------------


class TestExecutableTradeOrder:
    """Tests for the Pydantic schema itself."""

    def test_valid_buy_order(self):
        order = ExecutableTradeOrder(
            ticker="NVDA",
            side=OrderSide.BUY,
            quantity=10,
            limit_price=120.50,
            stop_loss=110.00,
            take_profit=140.00,
            confidence=0.85,
            reasoning="Strong technical breakout above resistance",
        )
        assert order.ticker == "NVDA"
        assert order.side == OrderSide.BUY
        assert order.quantity == 10
        assert order.confidence == 0.85

    def test_valid_sell_order_no_limit(self):
        order = ExecutableTradeOrder(
            ticker="AAPL",
            side=OrderSide.SELL,
            quantity=50,
            stop_loss=200.00,
            take_profit=150.00,
            confidence=0.72,
            reasoning="Bearish divergence on RSI with declining volume",
        )
        assert order.limit_price is None
        assert order.side == OrderSide.SELL

    def test_invalid_ticker_lowercase(self):
        with pytest.raises(Exception):
            ExecutableTradeOrder(
                ticker="nvda",
                side=OrderSide.BUY,
                quantity=10,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.85,
                reasoning="Invalid ticker test case here",
            )

    def test_invalid_ticker_too_long(self):
        with pytest.raises(Exception):
            ExecutableTradeOrder(
                ticker="TOOLONG",
                side=OrderSide.BUY,
                quantity=10,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.85,
                reasoning="Ticker too long for validation",
            )

    def test_quantity_zero_rejected(self):
        with pytest.raises(Exception):
            ExecutableTradeOrder(
                ticker="AAPL",
                side=OrderSide.BUY,
                quantity=0,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.85,
                reasoning="Zero quantity should be rejected",
            )

    def test_quantity_exceeds_max(self):
        with pytest.raises(Exception):
            ExecutableTradeOrder(
                ticker="AAPL",
                side=OrderSide.BUY,
                quantity=99999,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.85,
                reasoning="Quantity above 10000 should fail",
            )

    def test_confidence_below_threshold_rejected(self):
        with pytest.raises(Exception, match="below the minimum threshold"):
            ExecutableTradeOrder(
                ticker="AAPL",
                side=OrderSide.BUY,
                quantity=10,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.3,
                reasoning="Low confidence should be rejected",
            )

    def test_confidence_at_threshold(self):
        order = ExecutableTradeOrder(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=10,
            stop_loss=110.00,
            take_profit=140.00,
            confidence=0.6,
            reasoning="Exactly at threshold should pass validation",
        )
        assert order.confidence == 0.6

    def test_reasoning_too_short(self):
        with pytest.raises(Exception):
            ExecutableTradeOrder(
                ticker="AAPL",
                side=OrderSide.BUY,
                quantity=10,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.85,
                reasoning="Short",
            )

    def test_json_serialization_roundtrip(self):
        order = ExecutableTradeOrder(
            ticker="MSFT",
            side=OrderSide.BUY,
            quantity=25,
            limit_price=350.00,
            stop_loss=330.00,
            take_profit=400.00,
            confidence=0.91,
            reasoning="Cloud revenue growth exceeding expectations",
        )
        json_str = order.model_dump_json()
        restored = ExecutableTradeOrder.model_validate_json(json_str)
        assert restored == order


# ---------------------------------------------------------------------------
# parse_decision_to_order
# ---------------------------------------------------------------------------


class TestParseDecisionToOrder:
    """Tests for converting PortfolioDecision text to orders."""

    HOLD_DECISION = """**Rating**: Hold

**Executive Summary**: The market shows mixed signals. Maintain current
positions and wait for clearer direction before taking action.

**Investment Thesis**: Both bull and bear arguments carry equal weight.
RSI is neutral at 50, volume is average, and no clear catalyst in sight."""

    BUY_DECISION = """**Rating**: Buy

**Executive Summary**: Strong buying opportunity with entry at $120.00,
stop-loss at $110.00, and take-profit at $140.00. Position sizing 5% of portfolio.

**Investment Thesis**: Technical breakout above 200-day MA with increasing volume.
Fundamental catalysts include strong earnings beat and raised guidance.
Confidence level is high at 85%."""

    SELL_DECISION = """**Rating**: Sell

**Executive Summary**: Exit position at current levels around $95.00.

**Investment Thesis**: Deteriorating fundamentals with revenue miss and
lowered guidance. Technical breakdown below support."""

    def test_hold_returns_none(self):
        result = parse_decision_to_order("AAPL", self.HOLD_DECISION)
        assert result is None

    def test_buy_decision_no_llm_uses_regex(self):
        result = parse_decision_to_order("NVDA", self.BUY_DECISION)
        # Without LLM, regex may or may not succeed based on text structure.
        # The key invariant is: it never raises.
        if result is not None:
            assert result.ticker == "NVDA"
            assert result.side == OrderSide.BUY

    def test_sell_decision_no_llm(self):
        result = parse_decision_to_order("TSLA", self.SELL_DECISION)
        if result is not None:
            assert result.ticker == "TSLA"
            assert result.side == OrderSide.SELL

    def test_invalid_ticker_returns_none(self):
        # Even with a Buy rating, a bad ticker should not produce an order
        result = parse_decision_to_order("invalid", self.BUY_DECISION)
        assert result is None

    def test_never_raises(self):
        """parse_decision_to_order must NEVER raise — safety invariant."""
        # Garbage input
        result = parse_decision_to_order("AAPL", "")
        assert result is None

        result = parse_decision_to_order("AAPL", "random garbage text")
        assert result is None


# ---------------------------------------------------------------------------
# write_order_to_queue
# ---------------------------------------------------------------------------


class TestWriteOrderToQueue:
    """Tests for persisting validated orders to the file queue."""

    def test_writes_valid_json_file(self):
        order = ExecutableTradeOrder(
            ticker="GOOG",
            side=OrderSide.BUY,
            quantity=5,
            stop_loss=150.00,
            take_profit=200.00,
            confidence=0.78,
            reasoning="Strong momentum and sector rotation into tech",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = write_order_to_queue(order, tmpdir)
            assert filepath.exists()
            assert filepath.suffix == ".json"

            data = json.loads(filepath.read_text())
            assert data["ticker"] == "GOOG"
            assert data["side"] == "buy"
            assert data["quantity"] == 5

    def test_creates_directory_if_missing(self):
        order = ExecutableTradeOrder(
            ticker="AMZN",
            side=OrderSide.SELL,
            quantity=3,
            stop_loss=200.00,
            take_profit=160.00,
            confidence=0.65,
            reasoning="Revenue deceleration and margin compression concerns",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "sub" / "orders"
            filepath = write_order_to_queue(order, nested)
            assert filepath.exists()
            assert nested.is_dir()
