"""TradingAgents — Autonomous Trading Daemon.

Runs continuously during US market hours. The system:
  1. Scans the market for promising stocks (Ticker Scanner Agent)
  2. Runs full 12-agent analysis on top candidates
  3. Validates and queues orders via Redis → Alpaca
  4. Reports everything via Telegram

The user does NOT pick tickers — the system discovers them autonomously.

Usage:
    python run_autonomous.py                                      # Local
    docker compose run tradingagents python run_autonomous.py      # Docker

Configuration:
    All settings in tradingagents/default_config.py or via .env
"""

import logging
import os
import sys

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.expanduser("~/.tradingagents/autonomous.log"),
            mode="a",
        ) if os.path.isdir(os.path.expanduser("~/.tradingagents")) else logging.StreamHandler(),
    ],
)
logger = logging.getLogger("autonomous")

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.integrations.autonomous_loop import AutonomousLoop

# ─── Configuration ────────────────────────────────────────────────────────────
config = DEFAULT_CONFIG.copy()

# LLM settings — optimized for M3 Pro 18GB
config["llm_provider"] = "ollama"
config["deep_think_llm"] = "qwen3:8b"
config["quick_think_llm"] = "qwen3:8b"

# Docker support: read OLLAMA_BASE_URL env var
_ollama_url = os.getenv("OLLAMA_BASE_URL")
if _ollama_url:
    config["backend_url"] = _ollama_url.rstrip("/") + "/v1" if not _ollama_url.endswith("/v1") else _ollama_url

# Debate settings
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1

# Data vendors
config["data_vendors"] = {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "yfinance",
    "news_data": "yfinance",
}

# ─── Integration Settings (enable all for autonomous mode) ────────────────────
config["telegram_enabled"] = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
config["execution_enabled"] = True
config["pinecone_enabled"] = bool(os.getenv("PINECONE_API_KEY"))

# ─── Autonomous Mode Settings ────────────────────────────────────────────────
config["autonomous_enabled"] = True
config["scan_interval_minutes"] = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
config["max_tickers_per_scan"] = int(os.getenv("MAX_TICKERS_PER_SCAN", "3"))
config["ticker_cooldown_hours"] = float(os.getenv("TICKER_COOLDOWN_HOURS", "4"))
config["portfolio_check_minutes"] = int(os.getenv("PORTFOLIO_CHECK_MINUTES", "15"))
config["market_timezone"] = "US/Eastern"
config["market_open"] = "09:30"
config["market_close"] = "16:00"

# Scanner settings
config["scanner_min_price"] = float(os.getenv("SCANNER_MIN_PRICE", "5.0"))
config["scanner_min_volume"] = int(os.getenv("SCANNER_MIN_VOLUME", "500000"))
config["scanner_universe"] = os.getenv("SCANNER_UNIVERSE", "default")

# Custom watchlist (comma-separated in env)
custom_wl = os.getenv("SCANNER_CUSTOM_WATCHLIST", "")
if custom_wl:
    config["scanner_custom_watchlist"] = [t.strip() for t in custom_wl.split(",") if t.strip()]

# ─── Initialize ───────────────────────────────────────────────────────────────
logger.info("=" * 60)
logger.info("  TradingAgents — Autonomous Trading Daemon")
logger.info("  LLM: %s (%s)", config["llm_provider"], config["quick_think_llm"])
logger.info("  Scan interval: %d minutes", config["scan_interval_minutes"])
logger.info("  Max tickers/scan: %d", config["max_tickers_per_scan"])
logger.info("  Telegram: %s", "ON" if config["telegram_enabled"] else "OFF")
logger.info("  Execution: %s (%s)", "ON" if config["execution_enabled"] else "OFF", config["execution_mode"])
logger.info("  Pinecone: %s", "ON" if config["pinecone_enabled"] else "OFF")
logger.info("=" * 60)

# Create TradingAgentsGraph
ta = TradingAgentsGraph(
    debug=False,  # Less verbose for daemon mode
    config=config,
    selected_analysts=["market", "social", "news", "fundamentals"],
)

# Create and run the autonomous loop
loop = AutonomousLoop(graph=ta, config=config)

if __name__ == "__main__":
    loop.run()
