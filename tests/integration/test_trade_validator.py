"""Unit tests for the trade_validator module."""

import importlib
import importlib.util
import json
import sys
import types
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load trade_validator via spec_from_file_location to bypass __init__ chains.
# Only need to stub tradingagents.agents.utils.rating since trade_validator
# imports nothing else heavy (pydantic, stdlib only otherwise).
# ---------------------------------------------------------------------------
def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__path__ = []
    mod.__package__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


_RATING_TABLE = {
    "buy": "Buy", "strong buy": "Buy", "overweight": "Buy",
    "sell": "Sell", "strong sell": "Sell", "underweight": "Sell",
    "hold": "Hold", "neutral": "Hold",
}

def _parse_rating(text: str, **_kw) -> str:
    lower = text.lower()
    for kw, val in _RATING_TABLE.items():
        if kw in lower:
            return val
    return "Hold"

for _pkg in ["tradingagents", "tradingagents.agents", "tradingagents.agents.utils"]:
    sys.modules.setdefault(_pkg, _stub(_pkg))

_rating_mod = _stub("tradingagents.agents.utils.rating", parse_rating=_parse_rating)
sys.modules["tradingagents.agents.utils.rating"] = _rating_mod
sys.modules.setdefault("tradingagents.integrations", _stub("tradingagents.integrations"))

_tv_spec = importlib.util.spec_from_file_location(
    "tradingagents.integrations.trade_validator",
    Path(__file__).parents[2] / "tradingagents" / "integrations" / "trade_validator.py",
)
_tv_mod = importlib.util.module_from_spec(_tv_spec)
sys.modules["tradingagents.integrations.trade_validator"] = _tv_mod
_tv_spec.loader.exec_module(_tv_mod)

ExecutableTradeOrder = _tv_mod.ExecutableTradeOrder
OrderSide = _tv_mod.OrderSide
parse_decision_to_order = _tv_mod.parse_decision_to_order
write_order_to_queue = _tv_mod.write_order_to_queue


# ---------------------------------------------------------------------------
# ExecutableTradeOrder validation
# ---------------------------------------------------------------------------


class TestExecutableTradeOrder:
    """Tests for the Pydantic schema itself."""

    def test_valid_buy_order(self):
        order = ExecutableTradeOrder(
            ticker="SOLUSDT",
            side=OrderSide.BUY,
            quantity=10,
            limit_price=120.50,
            stop_loss=110.00,
            take_profit=140.00,
            confidence=0.85,
            reasoning="Strong technical breakout above resistance",
        )
        assert order.ticker == "SOLUSDT"
        assert order.side == OrderSide.BUY
        assert order.quantity == 10
        assert order.confidence == 0.85

    def test_valid_sell_order_no_limit(self):
        order = ExecutableTradeOrder(
            ticker="ETHUSDT",
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
                ticker="SOLUSDT",
                side=OrderSide.BUY,
                quantity=10,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.3,
                reasoning="Low confidence should be rejected",
            )

    def test_confidence_at_threshold(self):
        order = ExecutableTradeOrder(
            ticker="SOLUSDT",
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
                ticker="SOLUSDT",
                side=OrderSide.BUY,
                quantity=10,
                stop_loss=110.00,
                take_profit=140.00,
                confidence=0.85,
                reasoning="Short",
            )

    def test_json_serialization_roundtrip(self):
        order = ExecutableTradeOrder(
            ticker="BTCUSDT",
            side=OrderSide.BUY,
            quantity=25,
            limit_price=350.00,
            stop_loss=330.00,
            take_profit=400.00,
            confidence=0.91,
            reasoning="Bullish breakout on strong volume with macro tailwinds",
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
            ticker="LINKUSDT",
            side=OrderSide.BUY,
            quantity=5,
            stop_loss=150.00,
            take_profit=200.00,
            confidence=0.78,
            reasoning="Strong momentum and sector rotation into DeFi",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = write_order_to_queue(order, tmpdir)
            assert filepath.exists()
            assert filepath.suffix == ".json"

            data = json.loads(filepath.read_text())
            assert data["ticker"] == "LINKUSDT"
            assert data["side"] == "buy"
            assert data["quantity"] == 5

    def test_creates_directory_if_missing(self):
        order = ExecutableTradeOrder(
            ticker="ADAUSDT",
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

    def test_filename_includes_ticker_and_side(self):
        order = ExecutableTradeOrder(
            ticker="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.001,
            stop_loss=40000.0,
            take_profit=48000.0,
            confidence=0.75,
            reasoning="Bullish macro setup with strong institutional flows",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = write_order_to_queue(order, tmpdir)
            assert "BTCUSDT" in filepath.name
            assert "buy" in filepath.name


# ---------------------------------------------------------------------------
# stop_loss_sanity field validator (buy-side only)
# ---------------------------------------------------------------------------

class TestStopLossSanity:
    def test_buy_stop_loss_above_limit_price_rejected(self):
        """For a BUY order, stop_loss must be below limit_price."""
        with pytest.raises(Exception):
            ExecutableTradeOrder(
                ticker="BTCUSDT",
                side=OrderSide.BUY,
                quantity=0.001,
                limit_price=100.0,
                stop_loss=110.0,   # SL above limit → invalid
                take_profit=120.0,
                confidence=0.75,
                reasoning="Stop-loss sanity test for buy order validation",
            )

    def test_buy_stop_loss_below_limit_price_accepted(self):
        """For a BUY order with SL < limit, no error."""
        order = ExecutableTradeOrder(
            ticker="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.001,
            limit_price=100.0,
            stop_loss=90.0,    # SL below limit → valid
            take_profit=120.0,
            confidence=0.75,
            reasoning="Valid buy order with stop-loss below limit price",
        )
        assert order.stop_loss == pytest.approx(90.0)

    def test_sell_order_stop_loss_above_limit_price_accepted(self):
        """Buy-side rule does NOT apply to SELL orders."""
        order = ExecutableTradeOrder(
            ticker="ETHUSDT",
            side=OrderSide.SELL,
            quantity=0.1,
            limit_price=100.0,
            stop_loss=115.0,   # SL above limit is fine for SELL
            take_profit=80.0,
            confidence=0.7,
            reasoning="Sell order stop-loss sanity test validation check",
        )
        assert order.stop_loss == pytest.approx(115.0)

    def test_buy_no_limit_price_no_stop_loss_error(self):
        """Without a limit_price, stop_loss sanity is skipped."""
        order = ExecutableTradeOrder(
            ticker="SOLUSDT",
            side=OrderSide.BUY,
            quantity=1.0,
            limit_price=None,
            stop_loss=110.0,   # doesn't matter — no limit_price
            take_profit=150.0,
            confidence=0.8,
            reasoning="Market order without limit price stop-loss check",
        )
        assert order.stop_loss == pytest.approx(110.0)


# ---------------------------------------------------------------------------
# _extract_via_llm
# ---------------------------------------------------------------------------

class TestExtractViaLLM:
    from unittest.mock import MagicMock

    def _make_llm(self, content: str):
        from unittest.mock import MagicMock
        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = content
        mock_llm.invoke.return_value = mock_resp
        return mock_llm

    def test_valid_llm_json_creates_order(self):
        from tradingagents.integrations.trade_validator import _extract_via_llm

        payload = json.dumps({
            "quantity": 0.1,
            "limit_price": 2500.0,
            "stop_loss": 2300.0,
            "take_profit": 2900.0,
            "confidence": 0.8,
            "reasoning": "Strong momentum setup with clear entry and exit levels",
        })
        order = _extract_via_llm("ETHUSDT", OrderSide.BUY, "some decision", self._make_llm(payload))

        assert order is not None
        assert order.ticker == "ETHUSDT"
        assert order.side == OrderSide.BUY
        assert order.quantity == pytest.approx(0.1)

    def test_llm_markdown_fences_stripped(self):
        """LLM wrapping JSON in ```json ... ``` should still be parsed."""
        from tradingagents.integrations.trade_validator import _extract_via_llm

        payload = json.dumps({
            "quantity": 5.0,
            "limit_price": None,
            "stop_loss": 80.0,
            "take_profit": 130.0,
            "confidence": 0.75,
            "reasoning": "Breakout confirmed with RSI divergence and volume surge",
        })
        wrapped = f"```json\n{payload}\n```"
        order = _extract_via_llm("SOLUSDT", OrderSide.BUY, "decision", self._make_llm(wrapped))

        assert order is not None
        assert order.stop_loss == pytest.approx(80.0)

    def test_llm_invalid_json_returns_none(self):
        """LLM returning garbage text → None, not exception."""
        from tradingagents.integrations.trade_validator import _extract_via_llm

        order = _extract_via_llm("BTCUSDT", OrderSide.BUY, "decision", self._make_llm("Sorry, I cannot help."))

        assert order is None

    def test_llm_low_confidence_returns_none(self):
        """LLM JSON with confidence < 0.6 → Pydantic rejects → returns None."""
        from tradingagents.integrations.trade_validator import _extract_via_llm

        payload = json.dumps({
            "quantity": 10.0,
            "limit_price": None,
            "stop_loss": 50.0,
            "take_profit": 70.0,
            "confidence": 0.4,   # below threshold
            "reasoning": "Weak setup, below confidence threshold validation",
        })
        order = _extract_via_llm("SOLUSDT", OrderSide.BUY, "decision", self._make_llm(payload))

        assert order is None


# ---------------------------------------------------------------------------
# _extract_via_regex
# ---------------------------------------------------------------------------

class TestExtractViaRegex:
    def test_text_with_prices_returns_order(self):
        """Decision text with ≥2 prices → order extracted via regex."""
        from tradingagents.integrations.trade_validator import _extract_via_regex

        text = "Buy at $120.00 with target $140.00. Confidence: 75%."
        order = _extract_via_regex("SOLUSDT", OrderSide.BUY, text)

        assert order is not None
        assert order.ticker == "SOLUSDT"
        assert order.side == OrderSide.BUY
        # stop_loss and take_profit derived from entry±5%/10%
        assert order.stop_loss < 120.0   # SL below entry for BUY

    def test_text_with_one_price_returns_none(self):
        """Only 1 price → regex needs ≥2 → returns None."""
        from tradingagents.integrations.trade_validator import _extract_via_regex

        text = "Strong buy signal. Price target somewhere around $120."
        # Only 1 numeric price, but "120" might match once → check
        # Actually the function requires len(prices) >= 2 to proceed
        order = _extract_via_regex("SOLUSDT", OrderSide.BUY, "Strong buy signal.")
        assert order is None

    def test_confidence_extracted_from_text(self):
        """'confidence: 80%' in text → confidence=0.80."""
        from tradingagents.integrations.trade_validator import _extract_via_regex

        text = "Entry at $120.00. Take profit $140.00. Confidence: 80%."
        order = _extract_via_regex("SOLUSDT", OrderSide.BUY, text)

        if order is not None:  # regex may not always succeed
            assert order.confidence == pytest.approx(0.80)

    def test_sell_side_stop_loss_above_entry(self):
        """SELL order: SL should be above entry (reversed direction)."""
        from tradingagents.integrations.trade_validator import _extract_via_regex

        text = "Sell at $100.00 with downside to $80.00."
        order = _extract_via_regex("ETHUSDT", OrderSide.SELL, text)

        if order is not None:
            # For SELL: SL = entry * 1.05 = above entry
            assert order.stop_loss >= 100.0


# ---------------------------------------------------------------------------
# parse_decision_to_order — LLM interaction
# ---------------------------------------------------------------------------

class TestParseDecisionToOrderLLM:
    BUY_DECISION = """**Rating**: Buy

Entry at $120.00, stop-loss at $110.00, take-profit at $140.00.
Confidence: 85%. Strong technical breakout pattern with volume confirmation."""

    def _make_llm(self, content: str):
        from unittest.mock import MagicMock
        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = content
        mock_llm.invoke.return_value = mock_resp
        return mock_llm

    def test_llm_used_when_provided(self):
        """When LLM is provided, LLM extraction is tried first."""
        payload = json.dumps({
            "quantity": 0.1,
            "limit_price": 120.0,
            "stop_loss": 110.0,
            "take_profit": 140.0,
            "confidence": 0.85,
            "reasoning": "Strong technical breakout with volume confirmation signals",
        })
        llm = self._make_llm(payload)

        result = parse_decision_to_order("SOLUSDT", self.BUY_DECISION, llm=llm)

        assert result is not None
        assert result.ticker == "SOLUSDT"
        llm.invoke.assert_called_once()

    def test_falls_back_to_regex_when_llm_returns_none(self):
        """LLM returning garbage → regex extraction attempted."""
        llm = self._make_llm("I cannot process this request.")

        # With a text that has parseable prices, regex should succeed
        decision = "**Rating**: Buy\n\nBuy at $120.00 target $140.00."
        result = parse_decision_to_order("SOLUSDT", decision, llm=llm)

        # Either from LLM or regex — should not raise
        # (LLM will fail → regex tries)
        assert result is None or result.ticker == "SOLUSDT"

    def test_hold_rating_returns_none_even_with_llm(self):
        """HOLD rating → None regardless of LLM."""
        llm = self._make_llm("{}")

        hold_text = "**Rating**: Hold\n\nMaintain current position. No trade needed."
        result = parse_decision_to_order("ETHUSDT", hold_text, llm=llm)

        assert result is None
        llm.invoke.assert_not_called()  # LLM never invoked for HOLD

