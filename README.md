<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="./assets/wechat.png" target="_blank"><img alt="WeChat" src="https://img.shields.io/badge/WeChat-TauricResearch-brightgreen?logo=wechat&logoColor=white"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <br>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/Join_GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>

<div align="center">
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=de">Deutsch</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=es">Español</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=fr">français</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ja">日本語</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ko">한국어</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=pt">Português</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ru">Русский</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=zh">中文</a>
</div>

---

# TradingAgents: Multi-Agents LLM Financial Trading Framework (Crypto Edition)

> **⚠️ Open Source Acknowledgment**  
> This project is a heavily modified and upgraded version of the original [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents). The core 12-agent LangGraph architecture belongs to their excellent research. This repository builds upon their work by adding a **fully autonomous daemon loop, a 13th Ticker Scanner agent, Docker Compose integrations, live Telegram alerting, and Binance Spot trade execution.**

## News

- [2026-06] **TradingAgents v6.0 (Crypto Edition)** released! Migrated from US stocks (yfinance + Alpaca) to **Binance Spot crypto trading**.
  - **Binance Ticker Scanner**: Discovers crypto opportunities using real-time Binance volume + Fear & Greed index.
  - **24/7 Continuous Daemon**: No market-hours restriction — crypto never sleeps.
  - **Binance Executor**: Orders execute directly on Binance Spot (testnet by default, flip one env var for live).
  - **CoinGecko Fundamentals**: Free crypto fundamental and news data (no API key required).
- [2026-05] **TradingAgents v5.0 (Autonomous Edition)** released! Transitioned from a manual CLI tool to a fully autonomous, continuous trading daemon.
  - **Ticker Scanner Agent**: Discovers opportunities autonomously using yfinance + LLM ranking.
  - **Continuous Daemon**: Market-hours-aware scheduling loop (`run_autonomous.py`).
  - **Docker Compose Stack**: Fully containerized with `ollama`, `redis`, `tradingagents` brain, and `executor`.
  - **Live Integrations**: Pinecone RAG Memory, Alpaca Execution, and Telegram Alerting.
- [2026-04] **TradingAgents v0.2.4** released with structured-output agents (Research Manager, Trader, Portfolio Manager).
- [2026-03] **TradingAgents v0.2.3** released with multi-language support, GPT-5.4 family models.

<div align="center">
<a href="https://www.star-history.com/#TauricResearch/TradingAgents&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" />
   <img alt="TradingAgents Star History" src="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" style="width: 80%; height: auto;" />
 </picture>
</a>
</div>

> 🎉 **TradingAgents (Autonomous Edition)** is now running 24/7! We have upgraded the original framework with a full autonomous pipeline.

<div align="center">

🚀 [TradingAgents](#tradingagents-framework) | ⚡ [Installation & Docker](#installation-and-docker) | 🤖 [Autonomous Loop](#autonomous-mode) | 📦 [Package Usage](#tradingagents-package)

</div>

## TradingAgents Framework

TradingAgents is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. In **v6.0 (Crypto Edition)**, the data source and execution layer have been migrated to **Binance Spot**. The system discovers crypto opportunities 24/7, runs them through the 12-agent LangGraph analysis pipeline, and sends validated trades to the Binance API via a Redis queue.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> TradingAgents framework is designed for research purposes. [It is not intended as financial, investment, or trading advice.](https://tauric.ai/disclaimer/)

### Autonomous Ticker Scanner (v6.0 — Crypto)

- **Phase 0 — Macro Context**: Fetches BTC price + Fear & Greed index to determine market regime (bullish/bearish/neutral).
- **Phase 1 — Volume Scan**: Calls Binance 24hr ticker endpoint; filters by minimum USDT volume ($10M default) and price change (3% default).
- **Phase 2 — News Signals**: Fetches CoinGecko trending coins and cross-references with scan candidates.
- **Phase 3 — LLM Ranking**: Scores and ranks up to 50 candidates using an LLM with macro context embedded in the prompt.
- Feeds the top 3 candidates into the deep analysis pipeline continuously.

### Analyst Team

- Fundamentals Analyst: Evaluates company financials and performance metrics.
- Sentiment Analyst: Analyzes social media and public sentiment.
- News Analyst: Monitors global news and macroeconomic indicators.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI).

### Researcher Team

- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team.

### Trader Agent & Execution Layer

- Composes reports to make informed trading decisions.
- Pydantic Validator enforces strict JSON schema outputs with Binance pair format (`BTCUSDT`, `ETHUSDT`, etc.).
- Orders are queued in **Redis** and picked up by the independent **Binance Executor** service.
- Testnet mode is **on by default** — set `BINANCE_TESTNET=false` only when ready for live trading.

### Risk Management and Portfolio Manager

- Evaluates portfolio risk. If approved, the order is sent to the simulated/live exchange.
- **Telegram Notifier** alerts the user on validation, execution, and system status.

## Installation and Docker

### Docker Stack (Recommended)

Run the full autonomous system via Docker Compose:

```bash
git clone https://github.com/ardhanurfan/trading-bot-agent.git
cd trading-bot-agent

# Configure API Keys
cp .env.example .env
# Edit .env — required: LLM key + BINANCE_API_KEY + BINANCE_SECRET_KEY
# BINANCE_TESTNET=true is the default (safe for testing)

# Start the Infrastructure & Autonomous Brain
docker compose up -d

# Start the Binance Executor (testnet by default)
docker compose --profile executor up -d executor
```

### Manual Installation

Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
pip install . httpx pinecone python-dotenv
```

## Autonomous Mode

The system now runs as a daemon (`run_autonomous.py`) **24/7** — crypto markets never close.

- **Scan**: Queries Binance for top-volume USDT pairs + Fear & Greed index. Filters to top 3 candidates.
- **Analyze**: Runs the 12-agent LangGraph pipeline on each.
- **Execute**: Validated orders go to Binance Spot (testnet by default).
- **Report**: Sends live updates to Telegram.
- **Sleep**: Waits `SCAN_INTERVAL_MINUTES` before the next cycle.

### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen (Alibaba DashScope)
export ZHIPU_API_KEY=...           # GLM (Zhipu)
export OPENROUTER_API_KEY=...      # OpenRouter
```

**Binance API Setup (required for crypto trading):**

1. Go to [Binance API Management](https://www.binance.com/en/my/settings/api-management)
2. Create a new API key → Enable **Read** + **Trade** permissions
3. Do **NOT** enable Withdrawal
4. Set an IP whitelist for production security
5. For testnet: create a separate key at [testnet.binance.vision](https://testnet.binance.vision)

```bash
export BINANCE_API_KEY=your_key
export BINANCE_SECRET_KEY=your_secret
export BINANCE_TESTNET=true    # Switch to false only for live trading
```

**Free APIs (no key required):**

- [CoinGecko API](https://www.coingecko.com/en/api) — crypto fundamentals and trending coins
- [Alternative.me Fear & Greed](https://alternative.me/crypto/fear-and-greed-index/) — market sentiment

For enterprise providers (e.g. Azure OpenAI, AWS Bedrock), copy `.env.enterprise.example` to `.env.enterprise` and fill in your credentials.

For local models, configure Ollama with `llm_provider: "ollama"` in your config.

Alternatively, copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

### CLI Usage

Launch the interactive CLI:

```bash
tradingagents          # installed command
python -m cli.main     # alternative: run directly from source
```

You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more.

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## TradingAgents Package

### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen (Alibaba DashScope), GLM (Zhipu), OpenRouter, Ollama for local models, and Azure OpenAI for enterprise.

### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # openai, google, anthropic, xai, deepseek, qwen, glm, openrouter, ollama, azure
config["deep_think_llm"] = "gpt-5.4"     # Model for complex reasoning
config["quick_think_llm"] = "gpt-5.4-mini" # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

See `tradingagents/default_config.py` for all configuration options.

## Persistence and Recovery

TradingAgents persists two kinds of state across runs.

### Decision log

The decision log is always on. Each completed run appends its decision to `~/.tradingagents/memory/trading_memory.md`. On the next run for the same ticker, TradingAgents fetches the realised return (raw and alpha vs SPY), generates a one-paragraph reflection, and injects the most recent same-ticker decisions plus recent cross-ticker lessons into the Portfolio Manager prompt, so each analysis carries forward what worked and what didn't.

Override the path with `TRADINGAGENTS_MEMORY_LOG_PATH`.

### Checkpoint resume

Checkpoint resume is opt-in via `--checkpoint`. When enabled, LangGraph saves state after each node so a crashed or interrupted run resumes from the last successful step instead of starting over. On a resume run you will see `Resuming from step N for <TICKER> on <date>` in the logs; on a new run you will see `Starting fresh`. Checkpoints are cleared automatically on successful completion.

Per-ticker SQLite databases live at `~/.tradingagents/cache/checkpoints/<TICKER>.db` (override the base with `TRADINGAGENTS_CACHE_DIR`). Use `--clear-checkpoints` to reset all of them before a run.

```bash
tradingagents analyze --checkpoint           # enable for this run
tradingagents analyze --clear-checkpoints    # reset before running
```

```python
config = DEFAULT_CONFIG.copy()
config["checkpoint_enabled"] = True
ta = TradingAgentsGraph(config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
```

## Contributing

We welcome contributions from the community! Whether it's fixing a bug, improving documentation, or suggesting a new feature, your input helps make this project better. If you are interested in this line of research, please consider joining our open-source financial AI research community [Tauric Research](https://tauric.ai/).

Past contributions, including code, design feedback, and bug reports, are credited per release in [`CHANGELOG.md`](CHANGELOG.md).

## Citation

Please reference our work if you find _TradingAgents_ provides you with some help :)

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```
