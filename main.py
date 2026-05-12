"""TradingAgents — Agentic AI Trading System Entry Point.

This script configures and runs the full analysis pipeline using local
Ollama models on MacBook Pro M3 Pro 18GB.

Usage:
    python main.py                           # Interactive
    docker compose run tradingagents python main.py   # Docker

See: tradingagents/default_config.py for all config options.
"""

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ─── Configuration ────────────────────────────────────────────────────────────
config = DEFAULT_CONFIG.copy()

# LLM settings — optimized for M3 Pro 18GB
config["llm_provider"] = "ollama"
config["deep_think_llm"] = "qwen3:8b"      # Deep-think: Research Manager, Portfolio Manager
config["quick_think_llm"] = "qwen3:8b"      # Quick-think: Analysts, Trader, Debaters
config["backend_url"] = None                 # Auto-detected from OLLAMA_BASE_URL env var or defaults to localhost:11434

# Docker support: read OLLAMA_BASE_URL env var if set (e.g. http://ollama:11434/v1)
import os
_ollama_url = os.getenv("OLLAMA_BASE_URL")
if _ollama_url:
    # Ensure /v1 suffix for OpenAI-compatible endpoint
    config["backend_url"] = _ollama_url.rstrip("/") + "/v1" if not _ollama_url.endswith("/v1") else _ollama_url

# Debate settings — reduced for faster testing
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1

# Data vendors — yfinance for all (no API keys needed)
config["data_vendors"] = {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "yfinance",
    "news_data": "yfinance",
}

# ─── Integration Settings ─────────────────────────────────────────────────────
# Telegram: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to enable
config["telegram_enabled"] = True

# Trade Validation + Redis Queue: Enable to validate and queue orders
config["execution_enabled"] = False

# Pinecone RAG Memory: Set PINECONE_API_KEY in .env to enable
config["pinecone_enabled"] = True

# ─── Initialize and Run ───────────────────────────────────────────────────────
ta = TradingAgentsGraph(
    debug=True,
    config=config,
    selected_analysts=["market", "social", "news", "fundamentals"],
)

# Run analysis
ticker = "NVDA"
trade_date = "2026-05-10"

print(f"\n{'='*60}")
print(f"  TradingAgents — Analyzing {ticker} for {trade_date}")
print(f"  LLM: {config['llm_provider']} ({config['quick_think_llm']})")
print(f"  Integrations: Telegram={config['telegram_enabled']} | Execution={config['execution_enabled']} | Pinecone={config['pinecone_enabled']}")
print(f"{'='*60}\n")

final_state, decision = ta.propagate(ticker, trade_date)

print(f"\n{'='*60}")
print(f"  RESULT: {decision}")
print(f"{'='*60}\n")
print(final_state["final_trade_decision"][:500])

# Uncomment to run reflection after seeing results:
# ta.reflect_and_remember(1000)  # parameter is the position returns
