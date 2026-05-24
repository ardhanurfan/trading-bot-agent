# Architecture Patterns — Skill

## Purpose

This skill documents the architectural patterns used in `trading-bot-agent` and provides guidance for extending the system consistently.

---

## Core Patterns

### 1. LangGraph Node Pattern

Every agent in the graph is a **pure function** that takes `AgentState` and returns a partial state update:

```python
def create_my_analyst(llm: Any) -> Callable:
    def my_analyst_node(state: AgentState) -> Dict[str, Any]:
        # 1. Extract inputs from state
        ticker = state["company_of_interest"]
        date = state["trade_date"]

        # 2. Build prompt
        messages = [{"role": "system", "content": "..."}, ...]

        # 3. Invoke LLM
        response = llm.invoke(messages)

        # 4. Return partial state update (only changed fields)
        return {
            "my_report": response.content,
            "sender": "my_analyst"
        }
    return my_analyst_node
```

**Rules:**

- Never mutate state in-place
- Always return a dict (never the full AgentState)
- Always set `"sender"` to the node name
- Keep tool calls in separate ToolNode, not inside the analyst

---

### 2. Tool Node Pattern

Tools are **pure functions** with the `@tool` decorator, separate from agents:

```python
from langchain_core.tools import tool

@tool
def get_my_data(ticker: str, date: str) -> str:
    """Fetch [description] for a given ticker and date.

    Args:
        ticker: The stock ticker symbol (e.g., 'AAPL', 'NYSE:IBM')
        date: The analysis date in YYYY-MM-DD format
    Returns:
        A formatted string report
    """
    # Implementation
    return formatted_report
```

**Rules:**

- Docstring is mandatory (LLM uses it for tool selection)
- Return type is always `str` (formatted for LLM consumption)
- No side effects other than cache writes
- Validate `ticker` with `safe_ticker_component()`

---

### 3. Debate/Iteration Pattern

For iterative multi-agent debates (researcher team, risk management):

```python
# State contains a sub-state dict for the debate
class InvestDebateState(TypedDict):
    history: str
    bull_history: str
    bear_history: str
    current_bull_response: str
    current_bear_response: str
    count: int  # number of rounds completed

# ConditionalLogic decides when to end the debate
def should_continue_debate(self, state: AgentState) -> str:
    count = state["investment_debate_state"].get("count", 0)
    if count >= self.max_debate_rounds:
        return "research_manager"
    return "bull_researcher"
```

---

### 4. LLM Client Factory Pattern

All LLM instantiation goes through the factory:

```python
from tradingagents.llm_clients import create_llm_client

llm = create_llm_client(
    provider="openai",
    model="gpt-4.1-mini",
    temperature=0.7,
)
# Returns a LangChain-compatible chat model
```

**When adding a new provider:**

1. Create `tradingagents/llm_clients/<provider>_client.py` extending `BaseLLMClient`
2. Register it in `factory.py`
3. Add model entries to `model_catalog.py`
4. Add validation in `validators.py`

---

### 5. Integration Pattern

Integrations are opt-in via config flags:

```python
# In TradingAgentsGraph.__init__
self.my_integration = None
if self.config.get("my_integration_enabled"):
    from tradingagents.integrations.my_integration import MyIntegration
    self.my_integration = MyIntegration(
        param=self.config.get("my_integration_param")
    )

# Usage later
if self.my_integration:
    self.my_integration.notify(event)
```

**Rules:**

- Always guard with `if self.xxx:` before calling
- Failures in integrations must NOT crash the main trading loop
- Log integration errors at WARNING level; do not raise

---

### 6. Structured Output Pattern

For agents that need structured LLM output:

```python
from tradingagents.agents.utils.structured import bind_structured, invoke_structured_or_freetext

def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        result = invoke_structured_or_freetext(
            structured_llm, llm, messages, render_trader_proposal, "Trader"
        )
        return {"trader_investment_plan": result, "sender": name}
```

**Rules:**

- Always provide a `render_*` fallback for non-structured models
- `invoke_structured_or_freetext` handles model capability detection automatically

---

## Anti-Patterns (Forbidden)

| Anti-pattern                                  | Why forbidden                               |
| --------------------------------------------- | ------------------------------------------- |
| Business logic inside `ToolNode`              | ToolNodes are for tool dispatch only        |
| Direct API calls outside `dataflows/`         | Breaks the data abstraction layer           |
| Hardcoded model names inside agents           | Must come from config                       |
| Stateful agent objects (singletons)           | LangGraph nodes must be pure functions      |
| Sleeping/polling inside nodes                 | Blocks graph execution                      |
| `print()` in production code                  | Use `logger.info()` with structured fields  |
| Modifying `AgentState` fields of other agents | Only update your own output fields + sender |

---

## Adding a New Agent (checklist)

- [ ] Create `tradingagents/agents/<role>/<name>.py` with `create_<name>(llm)` factory
- [ ] Define output fields in `AgentState` (in `agent_states.py`)
- [ ] Register in `tradingagents/agents/__init__.py`
- [ ] Add node and edges in `graph/setup.py`
- [ ] Add conditional logic in `graph/conditional_logic.py` if needed
- [ ] Wire tool node in `trading_graph.py._create_tool_nodes()` if needed
- [ ] Write unit tests in `tests/test_<name>.py`
- [ ] Document in README.md agent list
