"""Strict validation layer between AI decision and trade execution.

The ``ExecutableTradeOrder`` schema acts as a gatekeeper: no trade reaches
the execution engine (Lumibot/Alpaca) without passing Pydantic validation.

``parse_decision_to_order`` bridges the existing PortfolioDecision prose
output and the structured order schema.  It first tries a deterministic
regex extraction, then falls back to an LLM extraction call when available.
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from tradingagents.agents.utils.rating import parse_rating

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class OrderSide(str, Enum):
    """Direction of a trade order."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class ExecutableTradeOrder(BaseModel):
    """Validated trade order ready for execution.

    Every field is validated by Pydantic before the order is persisted
    to the order queue.  Orders that fail validation are logged but
    never forwarded to the execution engine.
    """

    ticker: str = Field(
        ...,
        pattern=r"^[A-Z]{2,20}(USDT|BUSD|BTC|ETH|BNB|USDC|FDUSD)$",
        description="Binance spot pair symbol (e.g. BTCUSDT, ETHUSDT)",
    )
    side: OrderSide = Field(
        ...,
        description="Trade direction: buy, sell, or hold",
    )
    quantity: float = Field(
        ...,
        gt=0,
        description="Base asset quantity to trade (e.g. 0.001 BTC). Use small decimals for high-priced assets.",
    )
    limit_price: Optional[float] = Field(
        default=None,
        gt=0,
        description="Optional limit price; None for market orders",
    )
    stop_loss: float = Field(
        ...,
        gt=0,
        description="Stop-loss price level",
    )
    take_profit: float = Field(
        ...,
        gt=0,
        description="Take-profit price level",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model confidence score (0.0–1.0)",
    )
    reasoning: str = Field(
        ...,
        min_length=10,
        description="Short justification for the trade",
    )

    @field_validator("confidence")
    @classmethod
    def minimum_confidence_threshold(cls, v: float) -> float:
        """Reject trades below the minimum confidence threshold."""
        if v < 0.6:
            raise ValueError(
                f"Confidence {v:.2f} is below the minimum threshold of 0.60 — "
                "trade rejected for safety."
            )
        return v

    @field_validator("stop_loss")
    @classmethod
    def stop_loss_sanity(cls, v: float, info) -> float:
        """Ensure stop-loss is below limit price for buy orders."""
        limit = info.data.get("limit_price")
        side = info.data.get("side")
        if side == OrderSide.BUY and limit is not None and v >= limit:
            raise ValueError(
                f"Stop-loss ({v}) must be below limit price ({limit}) for buy orders"
            )
        return v


# ---------------------------------------------------------------------------
# Rating → OrderSide mapping
# ---------------------------------------------------------------------------

_RATING_TO_SIDE = {
    "Buy": OrderSide.BUY,
    "Overweight": OrderSide.BUY,
    "Hold": OrderSide.HOLD,
    "Underweight": OrderSide.SELL,
    "Sell": OrderSide.SELL,
}


# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are a trade order extractor.  Given the portfolio decision below,
extract the trade parameters and return ONLY a valid JSON object.

Required JSON format (no markdown, no explanation):
{{
  "quantity": <float — base asset amount, e.g. 0.001 for BTC, 0.1 for ETH, 10.0 for ADA>,
  "limit_price": <float or null>,
  "stop_loss": <float>,
  "take_profit": <float>,
  "confidence": <float between 0 and 1>,
  "reasoning": "<string, 1-2 sentences>"
}}

Rules:
- quantity is the base asset amount (crypto units), NOT USD value
  For BTC: small fractions (e.g. 0.001–0.1), for altcoins: larger amounts are fine
- confidence reflects how sure the analysis is (0.0 to 1.0)
- stop_loss and take_profit must be positive numbers (price levels in quote currency)
- If values are not mentioned, make reasonable estimates based on the analysis

Portfolio Decision:
{decision}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_decision_to_order(
    ticker: str,
    portfolio_decision: str,
    llm: Any = None,
    config: dict | None = None,
) -> Optional[ExecutableTradeOrder]:
    """Convert a PortfolioDecision text into a validated ExecutableTradeOrder.

    Returns ``None`` when the decision is Hold or when validation fails
    (invalid params, low confidence, etc.).  Never raises — all errors are
    logged and treated as "do not trade".

    Parameters
    ----------
    ticker:
        Stock ticker symbol (e.g. "NVDA").
    portfolio_decision:
        Raw text from the Portfolio Manager's final_trade_decision.
    llm:
        Optional LangChain LLM for extracting structured params.
        When ``None``, only regex-based extraction is attempted.
    config:
        Optional config dict (currently unused; reserved for future
        threshold overrides).
    """
    try:
        # Step 1: Determine side from the 5-tier rating
        rating = parse_rating(portfolio_decision)
        side = _RATING_TO_SIDE.get(rating, OrderSide.HOLD)

        if side == OrderSide.HOLD:
            logger.info(
                "Rating is '%s' → HOLD. No trade order generated for %s.",
                rating,
                ticker,
            )
            return None

        # Step 2: Try LLM extraction
        if llm is not None:
            order = _extract_via_llm(ticker, side, portfolio_decision, llm)
            if order is not None:
                return order

        # Step 3: Fallback — regex heuristics
        order = _extract_via_regex(ticker, side, portfolio_decision)
        if order is not None:
            return order

        logger.warning(
            "Could not extract trade parameters for %s from decision text. "
            "No order generated.",
            ticker,
        )
        return None

    except Exception:
        logger.exception("Unexpected error in parse_decision_to_order for %s", ticker)
        return None


def write_order_to_queue(order: ExecutableTradeOrder, order_dir: str | Path) -> Path:
    """Persist a validated order as a JSON file in the order queue directory.

    Returns the path to the written file.
    """
    order_dir = Path(order_dir)
    order_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{order.ticker}_{order.side.value}_{timestamp}.json"
    filepath = order_dir / filename

    filepath.write_text(order.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Order written to queue: %s", filepath)
    return filepath


# ---------------------------------------------------------------------------
# Internal extraction helpers
# ---------------------------------------------------------------------------


def _extract_via_llm(
    ticker: str,
    side: OrderSide,
    decision_text: str,
    llm: Any,
) -> Optional[ExecutableTradeOrder]:
    """Use an LLM to extract structured trade parameters from prose."""
    try:
        prompt = _EXTRACTION_PROMPT.format(decision=decision_text)
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)

        # Strip markdown fences if the LLM wraps in ```json ... ```
        content = re.sub(r"^```(?:json)?\s*", "", content.strip())
        content = re.sub(r"\s*```$", "", content.strip())

        params = json.loads(content)
        return ExecutableTradeOrder(ticker=ticker, side=side, **params)

    except json.JSONDecodeError as e:
        logger.warning("LLM returned invalid JSON for %s: %s", ticker, e)
    except Exception as e:
        logger.warning("LLM extraction failed for %s: %s", ticker, e)

    return None


def _extract_via_regex(
    ticker: str,
    side: OrderSide,
    decision_text: str,
) -> Optional[ExecutableTradeOrder]:
    """Best-effort regex extraction of trade params from decision text."""
    try:
        # Try to find price-like numbers in the text
        prices = re.findall(r"\$?([\d,]+\.?\d*)", decision_text)
        prices = [float(p.replace(",", "")) for p in prices if float(p.replace(",", "")) > 1]

        if len(prices) < 2:
            return None

        # Heuristic: first price = entry, use ±5% for SL/TP
        entry = prices[0]
        stop_loss = round(entry * (0.95 if side == OrderSide.BUY else 1.05), 2)
        take_profit = round(entry * (1.10 if side == OrderSide.BUY else 0.90), 2)

        # Extract confidence-like numbers (0.X or X%)
        conf_match = re.search(r"(?:confidence|conviction)[:\s]*(\d+\.?\d*)\s*%?", decision_text, re.I)
        confidence = 0.7  # default
        if conf_match:
            val = float(conf_match.group(1))
            confidence = val / 100 if val > 1 else val

        # Extract a reasoning sentence
        sentences = re.split(r"[.!?]\s+", decision_text)
        reasoning = sentences[0][:200] if sentences else "Automated extraction from analysis"

        return ExecutableTradeOrder(
            ticker=ticker,
            side=side,
            quantity=10,  # conservative default
            limit_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(max(confidence, 0.0), 1.0),
            reasoning=reasoning,
        )
    except Exception as e:
        logger.warning("Regex extraction failed for %s: %s", ticker, e)
        return None
