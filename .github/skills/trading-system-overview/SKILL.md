# Trading System Overview — Skill

## Purpose

This skill provides an authoritative map of the `trading-bot-agent` codebase so any agent can orient quickly without reading every file.

---

## System Architecture

```
trading-bot-agent/
├── tradingagents/
│   ├── agents/                    # All LLM-driven agent logic
│   │   ├── analysts/              # 4 analysts: market, news, social, fundamentals
│   │   ├── researchers/           # bull_researcher, bear_researcher
│   │   ├── managers/              # research_manager, portfolio_manager
│   │   ├── risk_mgmt/             # aggressive, conservative, neutral debators
│   │   ├── trader/                # trader (final decision maker)
│   │   ├── utils/                 # agent_states, agent_utils, memory, tools
│   │   └── schemas.py             # Pydantic output schemas
│   ├── graph/                     # LangGraph graph assembly
│   │   ├── trading_graph.py       # TradingAgentsGraph (main orchestrator)
│   │   ├── setup.py               # GraphSetup (node/edge wiring)
│   │   ├── conditional_logic.py   # edge conditions (debate rounds, etc.)
│   │   ├── propagation.py         # state propagation helpers
│   │   ├── reflection.py          # memory reflection
│   │   ├── signal_processing.py   # signal extraction from raw LLM output
│   │   └── checkpointer.py        # LangGraph checkpointing
│   ├── dataflows/                 # Data source adapters
│   │   ├── alpha_vantage*.py      # Alpha Vantage API wrappers
│   │   ├── y_finance.py           # yfinance adapter
│   │   ├── yfinance_news.py       # yfinance news
│   │   ├── stockstats_utils.py    # technical indicator computation
│   │   ├── interface.py           # unified data interface
│   │   └── config.py              # data layer config
│   ├── llm_clients/               # LLM provider abstraction
│   │   ├── factory.py             # create_llm_client() factory
│   │   ├── base_client.py         # BaseLLMClient ABC
│   │   ├── openai_client.py       # OpenAI / Azure OpenAI
│   │   ├── anthropic_client.py    # Anthropic Claude
│   │   ├── google_client.py       # Google Gemini
│   │   ├── azure_client.py        # Azure OpenAI
│   │   ├── model_catalog.py       # supported model registry
│   │   └── validators.py          # input validation
│   ├── integrations/              # External system connectors
│   │   ├── autonomous_loop.py     # main autonomous trading loop
│   │   ├── telegram_notifier.py   # Telegram Bot notifications
│   │   ├── redis_queue.py         # Redis Streams order queue
│   │   ├── pinecone_memory.py     # Pinecone + Ollama semantic memory
│   │   ├── lumibot_executor.py    # Alpaca trade execution
│   │   ├── ticker_scanner.py      # autonomous ticker discovery
│   │   └── trade_validator.py     # Pydantic trade order validation
│   └── default_config.py          # default configuration dict
├── cli/                           # Typer CLI interface
│   ├── main.py                    # CLI entrypoint
│   ├── config.py                  # config wizard
│   ├── models.py                  # model selection
│   ├── stats_handler.py           # stats display
│   └── utils.py                   # shared CLI utilities
├── tests/                         # pytest test suite
│   ├── conftest.py
│   ├── integration/               # integration tests
│   └── test_*.py                  # unit tests
├── scripts/                       # helper shell scripts
├── main.py                        # simple entry point
├── run_autonomous.py              # autonomous trading runner
├── pyproject.toml                 # project metadata + dependencies
├── requirements.txt               # pip requirements
├── Dockerfile                     # container build
└── docker-compose.yml             # local dev stack
```

---

## Agent Graph Flow

```
START
  │
  ├── market_analyst     ─┐
  ├── news_analyst       ─┤ (parallel or sequential, configurable)
  ├── social_media_analyst─┤
  └── fundamentals_analyst─┘
          │
  research_manager (synthesizes analyst reports)
          │
  ┌── bull_researcher ──┐
  └── bear_researcher ──┘ (debate loop, max_debate_rounds)
          │
  trader (proposes BUY/SELL/HOLD)
          │
  ┌── aggressive_debator ──┐
  ├── conservative_debator ─┤ (risk debate loop)
  └── neutral_debator ──────┘
          │
  portfolio_manager (final risk-adjusted decision)
          │
  END → signal_processing → trade_validator → redis_queue → lumibot_executor
```

---

## Key Data Structures

### `AgentState` (LangGraph state)

- `company_of_interest` — ticker symbol
- `trade_date` — analysis date
- `market_report`, `sentiment_report`, `news_report`, `fundamentals_report`
- `investment_debate_state` — bull/bear debate
- `investment_plan` — research manager output
- `trader_investment_plan` — trader output
- `risk_debate_state` — risk management debate
- `final_trade_decision` — portfolio manager output
- `past_context` — injected memory from previous runs

### `TraderProposal` (Pydantic)

- `action`: `Buy | Hold | Sell`
- `reasoning`, `entry_price`, `stop_loss`, `position_sizing`

### `ExecutableTradeOrder` (Pydantic)

- `symbol`, `side`, `quantity`, `order_type`

---

## Configuration Keys (`default_config.py`)

| Key                       | Default          | Description                         |
| ------------------------- | ---------------- | ----------------------------------- |
| `llm_provider`            | `"openai"`       | openai / anthropic / google / azure |
| `deep_think_llm`          | `"o4-mini"`      | reasoning-heavy agent model         |
| `quick_think_llm`         | `"gpt-4.1-mini"` | fast response model                 |
| `max_debate_rounds`       | `1`              | bull/bear debate iterations         |
| `max_risk_discuss_rounds` | `1`              | risk debate iterations              |
| `data_cache_dir`          | `"./data_cache"` | local cache for API responses       |
| `results_dir`             | `"./results"`    | output directory                    |
| `telegram_enabled`        | `False`          | Telegram alerts toggle              |
| `pinecone_enabled`        | `False`          | Pinecone memory toggle              |

---

## Python Commands (canonical)

| Purpose           | Command                                                        |
| ----------------- | -------------------------------------------------------------- |
| Install deps      | `pip install -e ".[dev]"` or `pip install -r requirements.txt` |
| Run tests         | `pytest tests/ -v`                                             |
| Run specific test | `pytest tests/test_signal_processing.py -v`                    |
| Type check        | `mypy tradingagents/ --ignore-missing-imports`                 |
| Lint              | `flake8 tradingagents/ tests/ --max-line-length 120`           |
| Format            | `black tradingagents/ tests/ cli/`                             |
| Sort imports      | `isort tradingagents/ tests/ cli/`                             |
| Run CLI           | `python -m cli.main`                                           |
| Run autonomous    | `python run_autonomous.py`                                     |
| Docker build      | `docker build -t trading-bot-agent .`                          |
| Docker compose    | `docker-compose up --build`                                    |

---

## Security-Sensitive Areas

- `tradingagents/llm_clients/` — API key handling (OpenAI, Anthropic, Google, Azure)
- `tradingagents/integrations/telegram_notifier.py` — Telegram bot token
- `tradingagents/integrations/lumibot_executor.py` — Alpaca live trading credentials
- `tradingagents/integrations/pinecone_memory.py` — Pinecone API key
- `tradingagents/integrations/redis_queue.py` — Redis connection credentials
- `cli/config.py` — credential input from user
- All `.env` file handling

---

## Testing Patterns

- **Unit tests**: `tests/test_*.py` — pure Python, no network
- **Integration tests**: `tests/integration/test_*.py` — may require running services
- **Fixtures**: `tests/conftest.py` — shared fixtures
- **Pattern**: pytest + unittest.mock for LLM calls, pydantic for schema validation
