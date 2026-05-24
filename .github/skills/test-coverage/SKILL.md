# Test Coverage — Skill

## Purpose

This skill defines the testing strategy, coverage requirements, and test writing patterns for `trading-bot-agent`.

---

## Coverage Requirements

| Area                          | Minimum Coverage |
| ----------------------------- | ---------------- |
| `tradingagents/agents/`       | 80%              |
| `tradingagents/graph/`        | 75%              |
| `tradingagents/llm_clients/`  | 85%              |
| `tradingagents/integrations/` | 70%              |
| `tradingagents/dataflows/`    | 70%              |
| `cli/`                        | 65%              |
| Changed files (PR gate)       | 80%              |

---

## Test Categories

### 1. Unit Tests (`tests/test_*.py`)

Test individual functions/classes in isolation. Mock all external calls.

```python
from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content="FINAL TRANSACTION PROPOSAL: **BUY**\nReasoning: ..."
    )
    return llm

def test_market_analyst_node_returns_report(mock_llm):
    node_fn = create_market_analyst(mock_llm)
    state = {
        "company_of_interest": "AAPL",
        "trade_date": "2025-01-15",
        "messages": [],
    }
    result = node_fn(state)
    assert "market_report" in result
    assert result["sender"] == "market_analyst"
```

### 2. Schema Tests (`tests/test_*.py`)

Test Pydantic models and structured output parsing:

```python
def test_trader_proposal_valid():
    proposal = TraderProposal(
        action=TraderAction.buy,
        reasoning="Strong technical signals with bullish momentum.",
        entry_price=150.0,
        stop_loss=145.0,
    )
    assert proposal.action == TraderAction.buy

def test_trader_proposal_rejects_empty_reasoning():
    with pytest.raises(ValidationError):
        TraderProposal(action=TraderAction.buy, reasoning="")
```

### 3. Integration Tests (`tests/integration/`)

Tests that require running services. Mark with `@pytest.mark.integration`:

```python
@pytest.mark.integration
def test_telegram_notifier_sends_message():
    # Requires TELEGRAM_BOT_TOKEN env var
    ...
```

Run separately: `pytest tests/integration/ -v -m integration`

### 4. Signal Processing Tests

Critical path — test all signal extraction edge cases:

```python
@pytest.mark.parametrize("raw_signal,expected_action", [
    ("FINAL TRANSACTION PROPOSAL: **BUY**", "BUY"),
    ("Recommendation: **SELL** the position", "SELL"),
    ("Decision: HOLD", "HOLD"),
    ("No clear signal", None),
])
def test_signal_extraction(raw_signal, expected_action):
    ...
```

### 5. Ticker Validation Tests

Safety-critical — must test all ticker format variants:

```python
@pytest.mark.parametrize("ticker,expected_safe", [
    ("AAPL", "AAPL"),
    ("NYSE:IBM", "NYSE_IBM"),   # colon sanitized for file paths
    ("../../../etc", None),      # path traversal rejected
    ("A" * 256, None),           # length limit enforced
])
def test_safe_ticker_component(ticker, expected_safe):
    ...
```

---

## Mocking Patterns

### Mock LLM Responses

```python
@patch("tradingagents.agents.analysts.market_analyst.ChatOpenAI")
def test_with_mocked_llm(MockLLM):
    MockLLM.return_value.invoke.return_value = MagicMock(content="...")
```

### Mock yfinance

```python
@patch("tradingagents.dataflows.y_finance.yf.download")
def test_stock_data_fetch(mock_download):
    mock_download.return_value = pd.DataFrame({"Close": [150.0, 152.0]})
```

### Mock External APIs

```python
@patch("requests.get")
def test_alpha_vantage_call(mock_get):
    mock_get.return_value.json.return_value = {"Time Series (Daily)": {...}}
```

---

## Coverage Commands

```bash
# Run with coverage
pytest tests/ -v --cov=tradingagents --cov=cli --cov-report=term-missing --cov-report=html

# Check specific file coverage
pytest tests/test_signal_processing.py -v --cov=tradingagents/graph/signal_processing

# Fail if coverage drops below gate
pytest tests/ --cov=tradingagents --cov-fail-under=75
```

---

## Test File Naming

| Source file                                       | Test file                            |
| ------------------------------------------------- | ------------------------------------ |
| `tradingagents/graph/signal_processing.py`        | `tests/test_signal_processing.py`    |
| `tradingagents/agents/trader/trader.py`           | `tests/test_trader.py`               |
| `tradingagents/llm_clients/factory.py`            | `tests/test_llm_factory.py`          |
| `tradingagents/integrations/telegram_notifier.py` | `tests/integration/test_telegram.py` |

---

## What to Test for Each New Feature

1. **Happy path** — expected inputs produce expected outputs
2. **Empty/null inputs** — graceful handling of missing data
3. **LLM failure** — what happens when LLM raises an exception
4. **API timeout** — what happens when data source times out
5. **Invalid ticker** — rejection of malformed ticker symbols
6. **State isolation** — node returns only its own fields, not other nodes' fields
7. **Structured output fallback** — if structured parsing fails, freetext path works
