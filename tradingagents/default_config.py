import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.4",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "binance",        # Options: alpha_vantage, yfinance, binance
        "technical_indicators": "binance",   # Options: alpha_vantage, yfinance, binance
        "fundamental_data": "binance",       # Options: alpha_vantage, yfinance, binance
        "news_data": "binance",              # Options: alpha_vantage, yfinance, binance
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # ===================================================================
    # Integration Settings (RFC-001)
    # All integrations are opt-in and disabled by default.
    # ===================================================================
    # Telegram Notifications
    "telegram_enabled": False,
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    # Trade Validation & Execution
    "execution_enabled": False,          # When True, validated orders are written to the order queue
    "execution_mode": "paper",           # "paper" or "live"
    "order_output_dir": os.path.join(_TRADINGAGENTS_HOME, "orders"),
    "redis_url": os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    "min_confidence_threshold": 0.6,     # Trades below this confidence are rejected
    "max_position_pct": 0.05,            # Max 5% of portfolio per trade
    "max_total_exposure": 0.20,          # Max 20% total exposure
    # Alpaca Brokerage (legacy stock trading — kept for backward compatibility)
    "alpaca_api_key": os.getenv("ALPACA_API_KEY", ""),
    "alpaca_secret_key": os.getenv("ALPACA_SECRET_KEY", ""),
    # Binance Crypto Exchange
    "binance_api_key": os.getenv("BINANCE_API_KEY", ""),
    "binance_secret_key": os.getenv("BINANCE_SECRET_KEY", ""),
    "binance_testnet": os.getenv("BINANCE_TESTNET", "true").lower() not in ("false", "0", "no"),
    # Crypto trading parameters
    "crypto_enabled": True,
    "crypto_quote_asset": "USDT",           # Default quote currency for pairs
    "crypto_min_notional_usd": 10.0,        # Minimum order size in USD
    "crypto_min_volume_usd": 10_000_000,    # Min 24h volume for scanner ($10M)
    "crypto_min_price_change_pct": 3.0,     # Min absolute 24h price change (%) for scanner
    # Pinecone RAG Memory
    "pinecone_enabled": False,
    "pinecone_api_key": os.getenv("PINECONE_API_KEY", ""),
    "pinecone_index": "trading-memory",
    # ===================================================================
    # Autonomous Mode (v5.0)
    # Scanner + continuous loop during market hours
    # ===================================================================
    "autonomous_enabled": False,
    "scan_interval_minutes": 60,        # Minutes between scan cycles
    "max_tickers_per_scan": 3,          # Max tickers to deeply analyze per cycle
    "ticker_cooldown_hours": 4,         # Hours before re-analyzing a ticker
    "portfolio_check_minutes": 15,      # Minutes between portfolio checks
    # Crypto trades 24/7 — market hours set to full day UTC
    "market_timezone": "UTC",
    "market_open": "00:00",
    "market_close": "23:59",
    "pre_market_hour": 0,
    # Scanner settings — legacy compat
    "scanner_min_price": 0.0,           # Not used for crypto (use min_notional instead)
    "scanner_min_volume": 0,            # Not used for crypto (use scanner_min_quote_volume)
    "scanner_universe": "default",      # "default" uses bulk Binance USDT pairs
    "scanner_custom_watchlist": [],     # Custom crypto pair list when universe="custom"
    # Brainstorm-aligned scanner thresholds (v2)
    "scanner_min_quote_volume": 5_000_000,   # Min 24h USDT quote volume for non-Tier1 pairs
    "scanner_min_pct_change": 2.0,           # Min absolute 24h % change to qualify
    "scanner_max_pct_change": 40.0,          # Max absolute 24h % change (anti-manipulation cap)
    "scanner_min_vol_ratio": 1.5,            # Min vol_ratio (today / 7-day avg) for non-Tier1
    "scanner_min_trade_count": 1_000,        # Min number of trades in 24h for non-Tier1
    "scanner_tier1_pairs": [                 # Always eligible, relaxed thresholds
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"
    ],
    "scanner_blacklist_patterns": [          # Additional static blacklist entries
        "BTTCUSDT", "HOTUSDT", "LUNCUSDT", "LUNAUSDT", "FTMUSDT"
    ],
    "scanner_context_cache_ttl": 900,        # Seconds to cache BTC dominance / Fear&Greed
    "scanner_cache_ttl_seconds": 60,         # Seconds to cache bulk ticker data
    "scanner_cooldown_hours": 4,             # Hours before re-scanning an analyzed pair
    "scanner_quote_currency": "USDT",        # Quote currency filter for pair discovery
}
