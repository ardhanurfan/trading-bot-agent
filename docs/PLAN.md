# PLAN — Binance Crypto Trading Migration

**Version**: 1.0  
**Status**: Draft — Awaiting Approval  
**Scope**: Migrate trading-bot-agent from US stock trading (yfinance + Alpaca) to crypto trading on Binance

---

## §1 — Goal

Replace the data source and execution layer of the trading-bot-agent so it trades crypto pairs on Binance (Spot) instead of US stocks on Alpaca. The 12-agent LLM orchestration framework, technical indicators, Redis queue, Telegram notifier, Pinecone memory, and all LangGraph orchestration are preserved unchanged.

**Key outcomes**:

- Fetch real-time crypto OHLCV data from Binance Spot API (KLINES endpoint)
- Execute crypto buy/sell orders on Binance Spot (testnet by default, live opt-in)
- Scan for high-volume crypto pairs autonomously (24/7, no market hours gate)
- Ticker validation supports Binance pair format (e.g. `BTCUSDT`, `ETHUSDT`)

---

## §2 — Architecture Overview

```
┌────────────────────────────────────────────────────┐
│              LangGraph 12-Agent Framework            │
│  (Market, News, Fundamentals, Bull/Bear, Trader, …) │
│               ← NO CHANGES HERE →                   │
└───────────────────────┬────────────────────────────┘
                        │ uses
          ┌─────────────▼──────────────┐
          │    Agent Tools Layer        │
          │ core_stock_tools.py         │
          │ technical_indicators_tools  │
          │ news_data_tools             │
          │ [NEW] crypto_on_chain_tools │
          └─────────────┬──────────────┘
                        │ routes via
          ┌─────────────▼──────────────┐
          │   dataflows/interface.py    │
          │   vendor: "yfinance" (keep) │
          │   vendor: "binance"  [NEW]  │
          └─────────────┬──────────────┘
                        │
          ┌─────────────▼──────────────┐
          │ [NEW] binance_client.py     │  ← REST client, auth, rate-limit
          │ [NEW] binance_spot_data.py  │  ← KLINES, tickers, volumes
          └─────────────────────────────┘

┌────────────────────────────────────────────────────┐
│             Execution Pipeline                       │
│  trade_validator.py  [MODIFIED: ticker regex]        │
│  redis_queue.py      [NO CHANGE]                     │
│  [NEW] binance_executor.py  ← replaces lumibot       │
│  telegram_notifier.py [NO CHANGE]                    │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│             Autonomous Scanning                      │
│  ticker_scanner.py   [MODIFIED: Binance data]        │
│  autonomous_loop.py  [MODIFIED: 24/7, no hours]      │
└────────────────────────────────────────────────────┘
```

---

## §3 — Tech Stack Changes

| Component       | Before                            | After                                 |
| --------------- | --------------------------------- | ------------------------------------- |
| Market data     | yfinance, Alpha Vantage           | Binance REST API (KLINES, 24hrTicker) |
| Trade execution | Alpaca (via lumibot-trader)       | Binance Spot Trading API              |
| Ticker format   | `AAPL`, `MSFT`                    | `BTCUSDT`, `ETHUSDT`                  |
| Market hours    | NYSE 9:30–16:00 ET                | 24/7 (no gate)                        |
| Fundamentals    | Balance sheets, income statements | CoinGecko market cap, supply metrics  |
| New dependency  | —                                 | `python-binance>=1.0.17`              |

**Keep unchanged**: stockstats indicators (SMA, MACD, RSI, Bollinger, ATR, MFI), all LangGraph nodes, Redis, Telegram, Pinecone, all LLM clients.

---

## §4 — Tasks

### Phase 1 — Data Source (Medium complexity)

- [ ] **T1**: Create `tradingagents/dataflows/binance_client.py`
  - BinanceClient class with HMAC auth, testnet/live toggle
  - `get_klines(symbol, interval, limit)` → normalized DataFrame (open, high, low, close, volume)
  - `get_24hr_ticker(symbol)` → price change, volume, last price
  - `get_top_volume_pairs(quote_asset="USDT", limit=50)` → for ticker scanner
  - Rate limiting (Binance allows 1200 weight/min)
  - Security: API key from env vars only, never hardcoded

- [ ] **T2**: Create `tradingagents/dataflows/binance_spot_data.py`
  - `get_binance_ohlcv(symbol, start_date, end_date)` → CSV-formatted (matches yfinance output schema)
  - `get_binance_indicators(symbol, indicator, curr_date, look_back_days)` → reuses stockstats on Binance KLINES
  - `get_crypto_news(symbol, curr_date)` → CoinGecko news API (free, no key required for basic)

- [ ] **T3**: Update `tradingagents/dataflows/interface.py`
  - Add `"binance"` to `VENDOR_LIST`
  - Wire `VENDOR_METHODS["core_stock_apis"]["binance"]` → `binance_spot_data.get_binance_ohlcv`
  - Wire `VENDOR_METHODS["technical_indicators"]["binance"]` → `binance_spot_data.get_binance_indicators`
  - Wire `VENDOR_METHODS["news_data"]["binance"]` → `binance_spot_data.get_crypto_news`

- [ ] **T4**: Update `tradingagents/default_config.py`
  - Change `data_vendors` defaults to `"binance"` for all categories
  - Add `"binance_testnet": True` config key
  - Add `"crypto_min_notional_usd": 10.0` (min order in $)
  - Add `"crypto_base_quote": "USDT"` (default quote currency)
  - Change `market_timezone` from `"US/Eastern"` to `"UTC"`
  - Change market hours to 00:00–23:59 (effectively 24/7)
  - Update `DEFAULT_UNIVERSE` in `ticker_scanner.py` defaults to crypto pairs

### Phase 2 — Execution Engine (Medium complexity)

- [ ] **T5**: Create `tradingagents/integrations/binance_executor.py`
  - `BinanceExecutor` class (mirrors `AgenticTrader` interface from lumibot_executor.py)
  - Lazy-load Binance client from env vars (`BINANCE_API_KEY`, `BINANCE_SECRET_KEY`)
  - `_poll_redis_loop()` — same Redis consumer pattern as Alpaca executor
  - `_execute_order(order: ExecutableTradeOrder)` → Binance Spot market/limit order
  - Testnet mode: uses `https://testnet.binance.vision` base URL
  - Security: validate ticker is a valid Binance pair before execution
  - Telegram notification on fill (reuse existing notifier)

- [ ] **T6**: Update `tradingagents/integrations/trade_validator.py`
  - Update `ExecutableTradeOrder.ticker` pattern from `^[A-Z]{1,5}$` to `^[A-Z]{2,10}(USDT|BUSD|BTC|ETH|BNB|USDC)$`
  - Update `quantity` field: change type from `int` (shares) to `float` (crypto can be fractional), keep reasonable bounds (e.g. max notional via config)
  - Add `quote_qty: Optional[float]` for market orders using quote currency amount

### Phase 3 — Autonomous Loop (Low complexity)

- [x] **T7**: Update `tradingagents/integrations/autonomous_loop.py`
  - Remove or bypass `is_market_open()` check (crypto trades 24/7)
  - Update log messages referencing "market hours" / "NYSE"

- [x] **T8**: Update `tradingagents/integrations/ticker_scanner.py`
  - Phase 0: BTC/ETH/BTC dominance/F&G context with caching
  - Phase 1: Bulk `/api/v3/ticker/24hr` discovery (single call, ~2000 pairs)
  - Phase 1b: vol_ratio via 7-day klines; CoinGecko trending enrichment
  - Phase 2: Regex blacklist (leveraged tokens, stablecoins, Terra), Tier 1 relaxed thresholds, max_pct_change cap, tradeCount gate
  - Phase 3: 5-dimension LLM scoring + manipulation_flag disqualifier (brainstorm prompt)

### Phase 4 — Config & Environment (Low complexity)

- [ ] **T9**: Update `.env.example`
  - Add `BINANCE_API_KEY=your_binance_api_key_here`
  - Add `BINANCE_SECRET_KEY=your_binance_secret_key_here`
  - Add `BINANCE_TESTNET=true  # Set to false for live trading`
  - Comment out or soft-deprecate `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`

- [ ] **T10**: Update `README.md`
  - Update setup instructions for Binance (testnet account creation, API key with IP whitelist)
  - Note: yfinance/Alpaca still supported via config toggle

### Phase 5 — Crypto On-Chain Tools (Optional, can be separate PR)

- [x] **T11**: Create `tradingagents/agents/utils/crypto_on_chain_tools.py`
  - `get_crypto_fundamentals(symbol)` → CoinGecko: market cap, circulating supply, 24hr volume, FDV
  - `get_crypto_market_sentiment(symbol)` → Fear & Greed Index, Binance funding rate
  - Register tools in `agent_utils.py` for `FundamentalsAnalyst`

### Phase 6 — Tests (High — required for merge)

- [ ] **T12**: Create `tests/test_binance_data.py`
  - Mock `python-binance` Client, test `get_binance_ohlcv()` output schema matches yfinance schema
  - Test KLINES parsing, timezone normalization, DataFrame column names
  - Test rate-limit retry behavior

- [ ] **T13**: Create `tests/integration/test_binance_executor.py`
  - Mock Binance testnet client, test order placement for BUY/SELL/HOLD
  - Test rejection of invalid pairs
  - Test decimal quantity handling

- [ ] **T14**: Create `tests/test_crypto_tickers.py`
  - Test new `ExecutableTradeOrder` pattern accepts `BTCUSDT`, `ETHUSDT`, `BNBUSDC`
  - Test rejection of old stock format `AAPL`, `MSFT`
  - Test `safe_ticker_component()` still blocks path traversal for crypto symbols

---

## §5 — Validation Plan

```bash
# After each phase, run:
python -m py_compile tradingagents/dataflows/binance_client.py
python -m py_compile tradingagents/dataflows/binance_spot_data.py
python -m py_compile tradingagents/integrations/binance_executor.py

# Full type check
mypy tradingagents/ --ignore-missing-imports

# Lint
flake8 tradingagents/ --max-line-length=120
black --check tradingagents/

# Tests
pytest tests/test_binance_data.py -v
pytest tests/test_crypto_tickers.py -v
pytest tests/ -v --ignore=tests/integration  # All unit tests
```

**Manual smoke test (testnet)**:

1. Set `BINANCE_TESTNET=true`, add testnet API keys
2. Run: `python -c "from tradingagents.dataflows.binance_spot_data import get_binance_ohlcv; print(get_binance_ohlcv('BTCUSDT', '2024-01-01', '2024-01-07'))"`
3. Verify DataFrame shape and column names

---

## §6 — Rollback Plan

All changes are **additive** — yfinance/Alpaca support is preserved:

| To revert     | Action                                                     |
| ------------- | ---------------------------------------------------------- |
| Data source   | Set `data_vendors.core_stock_apis: "yfinance"` in config   |
| Execution     | Run `lumibot_executor.py` instead of `binance_executor.py` |
| Ticker format | Config toggle per analysis run                             |

No database migrations or destructive file deletions.

---

## §7 — New Dependencies

```
# Add to requirements.txt and pyproject.toml
python-binance>=1.0.17   # Official Binance REST + WebSocket SDK
```

CoinGecko public API (T11) requires no key for free tier (≤50 req/min).

---

## §8 — Security Checklist

- [ ] `BINANCE_API_KEY` and `BINANCE_SECRET_KEY` read only from environment (never hardcoded)
- [ ] `BINANCE_TESTNET=true` is default — production requires explicit opt-in
- [ ] Binance API key should be created with **IP whitelist** and **no withdrawal permission**
- [ ] All ticker inputs sanitized via `safe_ticker_component()` before use in API calls
- [ ] `python-binance` uses HMAC-SHA256 signatures — verify in code review
- [ ] Log API keys never appear in Telegram alerts or log files

---

## §9 — Dependency Graph

```
T1 → T2 → T3 → (agents automatically use Binance via interface)
T4 (config) — independent, can run in parallel with T1
T5 (executor) — independent of T1-T3 (uses Redis queue only)
T6 (validator) — independent, prerequisite for T5 smoke test
T7, T8 (autonomous) — depends on T1, T2 (need Binance data)
T9, T10 (env/docs) — independent, can run anytime
T12, T13, T14 (tests) — run after their respective implementation tasks
T11 (on-chain tools) — optional, parallel after T2
```

Parallelizable: `{T1, T4, T5, T6, T9, T10}` can all start simultaneously.

---

## §10 — Out of Scope

- WebSocket streaming (real-time tick data) — future enhancement
- Futures/Options/Margin trading — Spot only for v1
- Multi-exchange support (ccxt) — Binance-only for v1
- Portfolio rebalancing — out of scope for this PR
