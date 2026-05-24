# Python Development Standards — Skill

## Purpose

This skill defines the coding standards enforced throughout the `trading-bot-agent` project. All agents must comply with these rules during implementation and review.

---

## Stack

| Component     | Choice                                        |
| ------------- | --------------------------------------------- |
| Python        | 3.11+                                         |
| Framework     | LangGraph + LangChain                         |
| LLM clients   | openai, anthropic, google-generativeai, azure |
| Data          | yfinance, alpha_vantage, stockstats           |
| Validation    | Pydantic v2                                   |
| Testing       | pytest + unittest.mock                        |
| Type checking | mypy                                          |
| Linting       | flake8 (max-line-length 120)                  |
| Formatting    | black + isort                                 |
| CLI           | Typer                                         |
| Deployment    | Docker + docker-compose                       |

---

## Error Handling

```python
# Always use specific exception types — never bare except
try:
    result = await llm_client.invoke(messages)
except APIConnectionError as e:
    raise RuntimeError(f"LLM connection failed for {model}: {e}") from e
except APIRateLimitError as e:
    raise RuntimeError(f"Rate limit hit for {model}: {e}") from e

# Always chain exceptions with `from e`
raise ValueError(f"Invalid ticker {ticker}: expected EXCHANGE:SYMBOL format") from e

# Never swallow exceptions silently
# BAD: except Exception: pass
# GOOD: except Exception as e: logger.warning("...", exc_info=True); raise
```

---

## Logging

```python
import logging
logger = logging.getLogger(__name__)

# Structured fields — never f-strings in log calls
logger.info("Analyst report generated", extra={"analyst": "market", "ticker": ticker})
logger.error("LLM call failed", extra={"model": model, "attempt": attempt}, exc_info=True)

# NEVER log secrets, API keys, credentials, or raw LLM prompts containing user data
# BAD: logger.debug(f"API key: {api_key}")
# GOOD: logger.debug("API key configured", extra={"provider": provider})
```

---

## Type Annotations

```python
from typing import Optional, Dict, List, Any, Tuple
from pydantic import BaseModel, Field

# All public functions must have full type annotations
def create_market_analyst(llm: Any) -> Callable[[AgentState], Dict[str, Any]]: ...

# Pydantic models for all structured LLM outputs
class TraderProposal(BaseModel):
    action: TraderAction
    reasoning: str = Field(..., min_length=10)
    entry_price: Optional[float] = None
```

---

## LangGraph Patterns

```python
# State always flows through AgentState TypedDict
def analyst_node(state: AgentState) -> Dict[str, Any]:
    # Always return a dict of state fields to update
    return {"market_report": report, "sender": "market_analyst"}

# Tool nodes are isolated: never put business logic in ToolNode
tool_node = ToolNode([get_stock_data, get_indicators])

# Conditional edges return string labels — never booleans
def should_continue(state: AgentState) -> str:
    if state["messages"][-1].tool_calls:
        return "tools"
    return "next_step"
```

---

## Pydantic Patterns

```python
# V2 model config
class Config(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

# Use Field validators, not custom __init__
@field_validator("ticker")
@classmethod
def validate_ticker(cls, v: str) -> str:
    if not v.strip():
        raise ValueError("Ticker cannot be empty")
    return v.upper().strip()
```

---

## Async vs Sync

- The LangGraph graph runs **synchronously** (`graph.invoke()`), not async.
- LLM client wrappers must expose **synchronous** `invoke()` methods.
- Use `asyncio.run()` only at the top-level entry point if needed.
- Never mix sync and async in the same call chain without an explicit bridge.

---

## Testing Standards

```python
# Always mock external calls (LLM, yfinance, API)
from unittest.mock import MagicMock, patch

def test_market_analyst_node(mock_llm):
    mock_llm.invoke.return_value = MagicMock(content="FINAL TRANSACTION PROPOSAL: BUY")
    # Test the node in isolation, not the full graph

# Test file naming: test_<module_name>.py
# Test function naming: test_<function>_<scenario>
def test_create_trader_returns_buy_on_bullish_plan(): ...
def test_create_trader_returns_sell_on_bearish_plan(): ...

# Parametrize for multiple scenarios
@pytest.mark.parametrize("action,expected", [
    ("BUY", TraderAction.buy),
    ("SELL", TraderAction.sell),
    ("HOLD", TraderAction.hold),
])
def test_trader_action_parsing(action, expected): ...
```

---

## Security Rules

1. **No hardcoded secrets.** All API keys via environment variables only.
2. **Input sanitization.** All ticker symbols validated with `safe_ticker_component()` before use in file paths or API calls.
3. **No `eval()` or `exec()`** on any LLM-generated content.
4. **Rate limiting.** All external API calls must respect rate limits; use exponential backoff.
5. **No sensitive data in cache files.** `data_cache/` must not contain API keys or PII.
6. **Dependency pinning.** `requirements.txt` must pin exact versions for production.

---

## File Organization Rules

- One class or one logical group of functions per file.
- Agent creators go in `tradingagents/agents/<role>/<name>.py`.
- Data adapters go in `tradingagents/dataflows/<source>.py`.
- LLM client implementations go in `tradingagents/llm_clients/<provider>_client.py`.
- Integrations go in `tradingagents/integrations/<service>.py`.
- CLI commands go in `cli/<feature>.py`.
- Tests mirror the source structure: `tests/test_<module>.py`.
