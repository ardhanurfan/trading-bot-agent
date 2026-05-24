# Brainstorm — Crypto Ticker Scanner: Discovery Pipeline Design

**Topic**: How should the crypto ticker scanner discover which pairs to analyze and potentially trade, based on real-time market conditions?
**Date**: 2026-05-23
**Status**: Complete — feeds into T8 implementation

---

## Phase 2: Individual Perspectives

---

### 🧠 Trading Strategist (data-analyst perspective)

**Key Observations**

The stock scanner's "gainers/losers + volume" approach translates to crypto but with important modifications. In crypto, _real volume_ is the most trustworthy signal because price is trivially manipulable on low-cap pairs. The most actionable discovery signals, ranked by reliability:

1. **24h USDT volume spike vs 7-day average** — volume ratio > 2.5x is the strongest early-entry signal
2. **Price momentum breakout** — 24h change > 5% WITH rising volume (not just % change alone)
3. **BTC dominance direction** — falling BTC.D = altcoin season = increase altcoin candidate universe; rising BTC.D = stick to BTC/ETH
4. **Funding rate extremes** — perpetual futures funding rate > +0.1% per 8h = crowded longs = squeeze risk; < -0.05% = squeeze up candidate
5. **CoinGecko trending** — organic social search trending is a 2-4h leading indicator before price pop
6. **Fear & Greed Index** — extreme fear (< 25) = contrarian entry zone; extreme greed (> 75) = reduce quality bar, require more confirmations
7. **Sector rotation** — DeFi tokens, L2s, memes move in cohorts. If one DeFi token spikes, scan the cohort

**Top Recommendations**

1. Weight `quoteVolume` (USDT) 3x higher than `pct_change` in pre-filter scoring. A 50% move with low volume is noise; a 10% move with 5x volume is signal.
2. Include BTC and ETH 24h context in EVERY LLM scoring prompt — the LLM cannot score an altcoin's momentum intelligently without knowing whether majors are up or down.
3. Add a "regime gate": if Fear & Greed < 20 (extreme fear), only surface pairs from Tier 1 + reversal signals; if > 80 (extreme greed), raise minimum volume threshold by 2x.

**Risks/Concerns**

- Chasing already-pumped pairs is a core failure mode. A 30%+ 24h move likely means you're in the final 20% of the pump. Need to detect _early-stage_ momentum, not extended moves.
- Meme coins (PEPE, DOGE, SHIB) show massive volume signals but fundamental analysis by the 12 agents is nearly meaningless — may need a meme-coin exclusion flag or separate treatment.

**Open Question for Risk Manager**: At what % gain threshold should a pair be _auto-excluded_ as "already pumped"? Is 25% enough, or should it be 40%?

---

### 🔧 Data Engineer perspective

**Key Observations**

All Phase 1 discovery data is available free-of-charge from Binance public endpoints and a handful of third-party APIs. A single bulk call to `/api/v3/ticker/24hr` returns stats for ~2000+ pairs in one shot — this replaces the stock scanner's universe iteration loop entirely. No more batching 50 tickers; one request → full market picture.

**Critical endpoints:**

| Endpoint                                            | Data                                                         | Auth | Rate Weight |
| --------------------------------------------------- | ------------------------------------------------------------ | ---- | ----------- |
| `GET /api/v3/ticker/24hr`                           | All spot pairs: price, 24h%, volume, quoteVolume, tradeCount | None | 40          |
| `GET /api/v3/klines?symbol=X&interval=1h&limit=168` | 7-day hourly OHLCV for computing vol ratios                  | None | 2           |
| `GET /fapi/v1/premiumIndex`                         | Perpetual futures funding rate (all pairs in one call)       | None | 1           |
| `GET /fapi/v1/openInterest?symbol=X`                | Open interest for one futures pair                           | None | 1           |
| `https://api.coingecko.com/api/v3/search/trending`  | Top 7 trending coins (24h search & social)                   | None | 30 req/min  |
| `https://api.alternative.me/fng/?limit=1`           | Crypto Fear & Greed Index (0-100)                            | None | Unlimited   |
| `https://api.coingecko.com/api/v3/global`           | BTC dominance %, total market cap                            | None | 30 req/min  |

**Implementation approach:**

1. **Single bulk pull**: `GET /api/v3/ticker/24hr` → parse all `*USDT` pairs into list (~800 pairs)
2. **In-memory pre-filter** (rule-based) → shortlist ~20-40 candidates
3. **Parallel enrichment** (httpx async): klines for vol_ratio + funding rates for shortlisted pairs
4. **External context**: CoinGecko trending + Fear & Greed + BTC dominance (3 requests, cached 15 min)
5. LLM scoring on enriched shortlist

**Recommendations**

1. Use `quoteVolume` (USDT-denominated volume) as the primary liquidity filter, not `volume` (base asset). `quoteVolume` is comparable across all pairs.
2. Pre-filter to `*USDT` spot pairs only — excludes BTC-quoted pairs (ETHBTC, etc.) that are illiquid for retail-style trading.
3. Cache the bulk ticker response for 60 seconds; cache external APIs for 15 minutes. This avoids rate limit issues during frequent scan cycles.

**Risks/Concerns**

- Binance is geo-blocked in some regions (US, UK). Need configurable fallback (Bybit has equivalent APIs). Add `EXCHANGE_PROVIDER = binance|bybit` config flag.
- CoinGecko free tier: 30 req/min and responses can be slow (~1-2s). Always fetch async and don't block the main pipeline on it.

**Open Question for LLM Architect**: Should the klines call be made for all 20-40 shortlisted pairs (40 API calls) or only the top 10 after rule-based scoring?

---

### 🤖 LLM Architect perspective

**Key Observations**

The current stock prompt scores on `catalyst, volume_conviction, technical_setup, news_relevance`. This is stock-market framing and will produce poor results for crypto because:

- "Catalyst" in stocks = earnings/FDA/merger. In crypto, catalysts are: protocol upgrades, exchange listings, CEX trading competitions, whale accumulation, defi yield opportunities.
- "Technical setup" is meaningful but needs crypto context (funding rate, liquidation zones).
- The LLM has no way to flag pump-and-dump if not explicitly told to look for it.
- The LLM needs BTC/ETH market context to avoid scoring an altcoin highly when the whole market is collapsing.

**New crypto-aware scoring dimensions (5 axes):**

| Dimension                   | What it measures                                     | Stock equivalent  |
| --------------------------- | ---------------------------------------------------- | ----------------- |
| `momentum_quality`          | Is volume-backed and early-stage (not extended)?     | catalyst          |
| `liquidity_conviction`      | Is USDT volume high enough to enter/exit at size?    | volume_conviction |
| `market_context_alignment`  | Does BTC/ETH trend support this alt moving?          | macro context     |
| `manipulation_risk_inverse` | How likely is this a pump-and-dump? (lower = better) | N/A (new)         |
| `catalyst_quality`          | Is there an identifiable reason for the move?        | news_relevance    |

**Pump-and-dump detection signals the LLM should evaluate:**

- `pct_change_24h > 50` + `quoteVolume` is thin (< $10M) = high manipulation risk
- `vol_ratio > 10x` without news = suspicious
- Pair not in CoinGecko top 500 by market cap = elevated risk
- Name contains "INU", "SAFE", "MOON", "ELON" = meme/scam flag

**Recommendations**

1. Always inject these market context fields into the prompt: `btc_24h_pct`, `eth_24h_pct`, `fear_greed_value`, `btc_dominance_pct`, `market_regime`.
2. Add explicit "DISQUALIFY if…" rules in system prompt to reduce hallucinated high scores on obvious pump pairs.
3. Keep JSON output schema identical to current implementation (easier T8 integration), just add `manipulation_flag: true/false` to each candidate.

**Risks/Concerns**

- LLMs tend to score meme/narrative tokens highly because they have lots of social media training data about them (DOGE, SHIB, PEPE) — this is the opposite of what we want for signal quality.
- If the prompt contains "top 3" but all candidates are garbage, the LLM will still pick 3. Add explicit "return empty list if all candidates fail quality bar" instruction.

**Open Question for Risk Manager**: Should `manipulation_flag: true` be a hard disqualifier (drops from output), or does it just lower the score?

---

### 🛡️ Risk Manager perspective

**Key Observations**

The stock scanner has relatively clean inputs — S&P 500 / Nasdaq stocks are all liquid, regulated, and not subject to rug pulls. Crypto is the opposite: 95%+ of pairs are low-liquidity noise with high manipulation probability. The risk framework needs to be inverted — start from "everything is suspect" and qualify candidates up.

**Hard blacklist categories (automatic exclusion before LLM):**

1. **Leveraged tokens**: Symbol contains `3L`, `3S`, `2L`, `2S`, `UP`, `DOWN` (e.g., `BTCUP`, `ETH3S`) — these track leveraged returns and decay over time; never suitable for fundamental analysis
2. **Stablecoin pairs**: `USDCUSDT`, `BUSDUSDT`, `DAIUSDT`, `TUSDUSDT` — zero price movement by design
3. **Post-collapse tokens**: `LUNCUSDT`, `LUNAUSDT`, `FTXUSDT` — perpetually manipulated
4. **Fiat-quoted pairs**: `BTCEUR`, `ETHGBP` — use `BTCUSDT` equivalents instead; avoid double conversions
5. **NFT/Fan tokens**: Symbol ends in `FT`, `FAN`, or contains `NFT` — illiquid, low relevance

**Minimum criteria before LLM scoring (pre-filter thresholds):**

| Metric                     | Minimum    | Recommended | Rationale                                            |
| -------------------------- | ---------- | ----------- | ---------------------------------------------------- |
| `quoteVolume` (24h USDT)   | $5,000,000 | $20,000,000 | Minimum to enter/exit $10K position without slippage |
| `tradeCount` (24h)         | 1,000      | 5,000       | Proxy for organic trading vs wash trading            |
| `pct_change_24h` (abs)     | 3%         | 5%          | Minimum signal threshold                             |
| `pct_change_24h` (max)     | ±40%       | ±30%        | Above this = likely already pumped or in collapse    |
| `vol_ratio` (vs 7-day avg) | 1.5x       | 2.0x        | Volume must confirm price move                       |
| Market cap rank            | top-500    | top-200     | Minimum ecosystem legitimacy                         |
| Age on exchange            | > 14 days  | > 30 days   | New listings are high-risk by default                |

**Pair quality tiers (informational, not a hard filter):**

- **Tier 1** (Blue-chip): BTC, ETH, BNB, SOL, AVAX, ADA, XRP, MATIC, DOT, LINK — always eligible, lower thresholds
- **Tier 2** (Liquid): Top 20-100 market cap, >$50M 24h quoteVolume — eligible with standard thresholds
- **Tier 3** (Watchlist): Top 100-300 market cap, >$20M 24h quoteVolume — eligible with +1 signal confirmation required
- **Tier 4** (Avoid): Everything else unless exceptional circumstances

**Recommendations**

1. `manipulation_flag: true` from LLM should be a HARD disqualifier — do not pass to full 12-agent analysis. The cost of false positives (missing a good trade) is much lower than the cost of false negatives (analyzing a rug pull).
2. Add cooldown: if a pair was analyzed in the last 4 hours, skip it (same as current `analyzed_today` set, but time-windowed).
3. Circuit breaker: if 3 consecutive selected pairs all return HOLD from the trader agent, raise the quality bar (increase `min_quoteVolume` by 1.5x) for the next scan cycle.

**Open Question for Trading Strategist**: For Tier 1 assets (BTC, ETH), should the vol_ratio threshold be _lower_ (since their organic volume is already massive) or the same?

---

## Phase 3: Cross-Pollination

**Consensus Points**

1. **`quoteVolume` (USDT) is the primary liquidity signal** — all 4 perspectives independently identified this as the key metric. Volume in USDT is comparable across all pairs.
2. **BTC/ETH market context must be injected into LLM scoring** — the LLM cannot score an altcoin without knowing whether the overall market is up or down.
3. **Manipulations need explicit filtering** — both the pre-filter (thresholds) and the LLM (explicit prompt instructions) should independently flag suspicious patterns.
4. **Single bulk API call is the right architecture** — `GET /api/v3/ticker/24hr` replaces the entire batching loop. This is architecturally simpler and faster.

**Tension Areas**

- **Trading Strategist vs Risk Manager on threshold aggressiveness**: Strategist wants `vol_ratio > 2.5x` to catch early momentum. Risk Manager wants `pct_change < ±40%` hard cap. These can conflict during altcoin season when legitimate tokens can move 50%+ on real catalysts. **Resolution**: Apply both filters but allow Tier 1 assets to bypass the max pct_change cap.

- **Data Engineer vs LLM Architect on klines enrichment scope**: DE wants klines for all 20-40 shortlisted pairs (40 API calls). LLM Architect prefers top 10 only to save tokens and latency. **Resolution**: Run rule-based scoring first, take top 15, fetch klines for those 15 only. This is 15 klines calls, each cheap (weight 2).

- **LLM Architect vs Risk Manager on `manipulation_flag`**: LLM Architect asks whether it's hard disqualifier or score penalty. Risk Manager says hard disqualifier. **Resolution**: Hard disqualifier, but emit a warning log so the user can audit false positives over time.

---

## Synthesis

---

## 1. Recommended Scanner Pipeline

```
Phase 0: Market Context (3 API calls, cached 15 min)
  ├─ Fear & Greed Index       → alternative.me/fng
  ├─ BTC Dominance %          → CoinGecko /global
  └─ CoinGecko Trending Top7  → CoinGecko /search/trending

Phase 1: Bulk Discovery (1 API call)
  └─ GET /api/v3/ticker/24hr  → all ~2000 pairs
       ↓
  In-memory filter:
  ├─ Keep only *USDT spot pairs
  ├─ Blacklist: leveraged tokens, stablecoins, collapsed tokens
  ├─ Min quoteVolume > $5M (hard floor)
  ├─ Min pct_change_24h (abs) > 2%
  └─ Result: ~50-150 candidates

Phase 1b: Parallel Enrichment (N API calls, async, for shortlist of ~30)
  ├─ klines (1h, 168 bars)    → compute vol_ratio vs 7-day avg
  └─ premiumIndex (bulk)      → funding rates for USDT-M futures pairs

Phase 2: Rule-Based Pre-Filter
  ├─ vol_ratio > 1.5x
  ├─ pct_change_24h (abs) 3-40% window
  ├─ tradeCount > 1,000
  ├─ Max 40% pct_change (skip if likely already pumped)
  ├─ Deduplicate against analyzed_recently (4h window)
  ├─ Boost: CoinGecko trending pairs get +2 score bonus
  ├─ Boost: Funding rate extreme gets +1 urgency flag
  └─ Result: top 15-20 candidates with rule-based scores

Phase 3: LLM Scoring (1 LLM call)
  ├─ Inject: btc_24h_pct, eth_24h_pct, fear_greed, btc_dominance, market_regime
  ├─ Score 5 dimensions per candidate
  ├─ Flag manipulation_risk
  ├─ Disqualify manipulation_flag=true
  └─ Return top N (configurable, default 3) with scores + reasoning

Output: ScanResult with crypto-aware ScanCandidate list
```

---

## 2. Data Sources Matrix

| Signal                   | Source         | Endpoint                      | Auth | Update Freq | Cache TTL |
| ------------------------ | -------------- | ----------------------------- | ---- | ----------- | --------- |
| All pairs price/vol/24h% | Binance        | `GET /api/v3/ticker/24hr`     | None | Real-time   | 60s       |
| OHLCV for vol_ratio      | Binance        | `GET /api/v3/klines`          | None | Per candle  | None      |
| Futures funding rate     | Binance        | `GET /fapi/v1/premiumIndex`   | None | 8h          | 15 min    |
| Open interest            | Binance        | `GET /fapi/v1/openInterest`   | None | Real-time   | 5 min     |
| BTC dominance %          | CoinGecko      | `GET /api/v3/global`          | None | ~5 min      | 15 min    |
| Trending coins           | CoinGecko      | `GET /api/v3/search/trending` | None | 24h         | 15 min    |
| Fear & Greed Index       | Alternative.me | `GET /fng/?limit=1`           | None | Daily       | 30 min    |
| Market cap rank          | CoinGecko      | `GET /api/v3/coins/markets`   | None | ~5 min      | 30 min    |

**Not recommended (for now):**

- LunarCrush: API key required, not free tier for real-time; add in future if needed
- CoinMarketCap: Rate limits on free tier are too restrictive for a scanner loop
- Order book depth (`/api/v3/depth`): Too many calls (one per pair); check spread only on final candidates

---

## 3. Guardrails Table

### Hard Blacklist (auto-exclude, no LLM needed)

| Rule             | Pattern / Threshold                                           | Example matches       |
| ---------------- | ------------------------------------------------------------- | --------------------- |
| Leveraged tokens | Symbol contains `3L`, `3S`, `2L`, `2S`, `UP`, `DOWN`          | BTCUP, ETH3S, BNBDOWN |
| Stablecoin pairs | Symbol in `{USDCUSDT, BUSDUSDT, DAIUSDT, TUSDUSDT, USDTUSDC}` | USDCUSDT              |
| Collapsed tokens | Symbol in `{LUNCUSDT, LUNAUSDT}`                              | LUNCUSDT              |
| Non-USDT quote   | Symbol does not end in `USDT`                                 | ETHBTC, BNBETH        |
| Already analyzed | In `analyzed_recently` cache (4h window)                      | Any recent pair       |

### Minimum Quality Thresholds (pre-filter)

| Metric                     | Min Threshold         | Tier 1 override                  |
| -------------------------- | --------------------- | -------------------------------- |
| `quoteVolume` 24h          | $5,000,000 USDT       | $1,000,000 (BTC/ETH always pass) |
| `tradeCount` 24h           | 1,000 trades          | No override                      |
| `pct_change_24h` (abs min) | 2.0%                  | 1.0%                             |
| `pct_change_24h` (abs max) | 40%                   | No override (Tier 1 no max cap)  |
| `vol_ratio` vs 7-day avg   | 1.5x                  | 1.2x                             |
| Pair age on exchange       | > 14 days (estimated) | No restriction for Tier 1        |

### LLM Manipulation Disqualifiers (hard, regardless of score)

| Condition                                                           | Action                    |
| ------------------------------------------------------------------- | ------------------------- |
| `pct_change > 60%` + `quoteVolume < $10M`                           | Hard disqualify           |
| `vol_ratio > 15x` with no identifiable catalyst                     | Hard disqualify + warn    |
| LLM `manipulation_flag: true`                                       | Hard disqualify + log     |
| Symbol contains `INU`, `MOON`, `SAFE`, `ELON` (known scam patterns) | Warn + raise LLM scrutiny |

---

## 4. LLM Prompt Template (Phase 3 Scoring)

```python
CRYPTO_SCANNER_SYSTEM_PROMPT = """You are a Crypto Market Scanner Agent. You identify high-probability trading opportunities in cryptocurrency spot markets.

Your job is to score a shortlist of crypto trading pairs and identify the top candidates for deep multi-agent analysis.

SCORING DIMENSIONS (each 1-10):
- momentum_quality: Is the price move backed by genuine volume? Is it early-stage (not already extended)?
- liquidity_conviction: Is 24h USDT volume high enough to enter and exit a position cleanly?
- market_context_alignment: Given BTC/ETH performance and market regime, does this altcoin setup make sense?
- manipulation_risk_inverse: How confident are you this is NOT a pump-and-dump? (10 = very safe, 1 = obvious manipulation)
- catalyst_quality: Is there an identifiable reason for the move (protocol news, listing, on-chain activity)?

MANIPULATION FLAGS — set manipulation_flag=true if ANY of these apply:
- pct_change > 50% with quoteVolume < $15M
- vol_ratio > 12x with no identifiable catalyst
- Coin is not in top 500 market cap and showing >30% move
- Classic pump-and-dump name patterns (INU, MOON, SAFE, ELON, etc.)

DISQUALIFY if manipulation_flag is true — do NOT include in output candidates.
If ALL candidates fail quality bar, return empty candidates list rather than forcing picks.

Total score = (momentum_quality + liquidity_conviction + market_context_alignment + manipulation_risk_inverse + catalyst_quality) / 5
"""

CRYPTO_SCANNER_USER_PROMPT = """MARKET CONTEXT:
- BTC 24h: {btc_24h_pct:+.1f}%
- ETH 24h: {eth_24h_pct:+.1f}%
- BTC Dominance: {btc_dominance:.1f}%
- Fear & Greed Index: {fear_greed_value}/100 ({fear_greed_label})
- Market Regime: {market_regime}
- CoinGecko Trending: {trending_coins}

CANDIDATES ({n_candidates} pairs after pre-filter):
{candidates_text}

Score each candidate. Return top {max_tickers} by total score. Disqualify any with manipulation_flag=true.

Return ONLY valid JSON (no markdown, no explanation):
{{
  "candidates": [
    {{
      "ticker": "SOLUSDT",
      "score": 7.8,
      "catalyst": "Breaking above 200-day MA on 3x average volume, aligned with ETH strength",
      "signal_type": "breakout",
      "urgency": "high",
      "manipulation_flag": false,
      "momentum_quality": 8,
      "liquidity_conviction": 9,
      "market_context_alignment": 7,
      "manipulation_risk_inverse": 9,
      "catalyst_quality": 6
    }}
  ],
  "market_regime": "bullish_altseason|bullish_btc_dominant|bearish|neutral",
  "scan_summary": "Brief 1-2 sentence summary of market conditions and why these pairs were selected"
}}"""

# candidates_text format per pair:
CANDIDATE_LINE = (
    "  {ticker}: change={pct_change:+.1f}%, quoteVol=${quote_volume:,.0f}, "
    "vol_ratio={vol_ratio:.1f}x, tradeCount={trade_count:,}, "
    "signal={signal}, funding={funding_rate}"
)
```

---

## 5. T8 Task Update — `ticker_scanner.py` Implementation Notes

### Class rename / restructure

Replace `TickerScanner` with `CryptoTickerScanner` (or make it a subclass). Key structural changes:

```
TickerScanner (current)              CryptoTickerScanner (new)
─────────────────────────────────    ─────────────────────────────────
DEFAULT_UNIVERSE (50 stocks)     →   No static universe; dynamic from Binance bulk API
_batch_scan_universe()           →   _fetch_all_pairs_bulk()           # single API call
_scan_sectors()                  →   _fetch_market_context()           # Fear/Greed, BTC.D, trending
_scan_news()                     →   removed (absorbed into LLM prompt context)
pre_filter()                     →   pre_filter() with crypto thresholds + blacklist
_llm_rank()                      →   _llm_rank() with new prompt template
ScanCandidate.ticker             →   ScanCandidate.ticker (e.g. "SOLUSDT")
config: scanner_min_price=5.0    →   config: scanner_min_quote_volume=5_000_000
config: scanner_min_volume=500k  →   config: scanner_min_trade_count=1000
```

### New config keys to add to `default_config.py`

```python
# Crypto Scanner
"scanner_mode": "crypto",                    # "stock" | "crypto"
"scanner_exchange": "binance",               # "binance" | "bybit"
"scanner_min_quote_volume": 5_000_000,       # USDT, 24h
"scanner_min_trade_count": 1_000,
"scanner_max_pct_change": 40.0,              # hard cap
"scanner_min_pct_change": 2.0,               # min signal
"scanner_min_vol_ratio": 1.5,                # vs 7-day avg
"scanner_quote_currency": "USDT",
"scanner_tier1_pairs": [                     # always eligible, relaxed thresholds
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"
],
"scanner_blacklist_patterns": [              # hard-exclude by regex
    r"\d[LS]USDT$",                          # leveraged tokens
    r"(UP|DOWN)USDT$",                        # up/down tokens
    r"^(USDC|BUSD|DAI|TUSD|USDT)USDT$",     # stablecoin pairs
    r"^LUNC|^LUNA",                           # terra collapse
],
"scanner_cooldown_hours": 4,                 # skip pairs analyzed recently
"scanner_cache_ttl_seconds": 60,             # bulk ticker cache
"scanner_context_cache_ttl": 900,            # Fear/Greed + BTC.D cache (15 min)
```

### New dependency

Add to `requirements.txt`:

```
httpx>=0.27.0       # async HTTP (replaces requests for Binance calls)
```

Or use `aiohttp` if already present. `yfinance` is no longer needed for the scanner (but keep for other parts of the system that use it for stock data).

### Methods to implement

```python
class CryptoTickerScanner:
    async def _fetch_all_pairs_bulk(self) -> List[Dict]
        # GET /api/v3/ticker/24hr → filter *USDT, apply blacklist

    async def _fetch_market_context(self) -> Dict
        # Fear & Greed + BTC dominance + CoinGecko trending (cached 15 min)

    async def _enrich_with_klines(self, pairs: List[str]) -> Dict[str, float]
        # GET /api/v3/klines for each pair → compute vol_ratio
        # Run in parallel with asyncio.gather

    async def _enrich_with_funding_rates(self, pairs: List[str]) -> Dict[str, float]
        # GET /fapi/v1/premiumIndex (bulk) → extract funding rates

    def _apply_blacklist(self, symbol: str) -> bool
        # Returns True if pair should be excluded

    def _pre_filter_crypto(self, pairs: List[Dict], context: Dict) -> List[Dict]
        # Apply quoteVolume, tradeCount, pct_change window, vol_ratio filters

    def _llm_rank_crypto(self, candidates, context, ...) -> ScanResult
        # Use CRYPTO_SCANNER_SYSTEM_PROMPT + CRYPTO_SCANNER_USER_PROMPT

    async def scan(self) -> ScanResult
        # Async entrypoint: context → bulk → pre-filter → enrich → LLM rank
```

### Backward compatibility

- Keep the existing `TickerScanner` class for stock mode (don't delete)
- In `autonomous_loop.py`, instantiate the correct scanner based on `config["scanner_mode"]`
- Both scanners should return the same `ScanResult` / `ScanCandidate` dataclasses

---

## Open Questions (Human Decision Required)

1. **Spot vs Futures scanning**: Should the scanner surface futures pairs (e.g., `BTCUSDT` perp) or only spot? Futures have funding rate data but require margin. Recommendation: scan spot only for now, use futures data (funding rate) as a _signal_ only.

2. **Bybit fallback**: Binance is geo-blocked for US users. Should we implement a Bybit adapter now (parallel to Binance) or add it as a follow-up? Recommendation: flag as `scanner_exchange = binance|bybit` config but implement Bybit in a separate T8b task.

3. **Meme coin treatment**: DOGE, SHIB, PEPE can meet all volume thresholds but fundamental analysis (LLM agents) is less meaningful. Recommendation: include them if they meet thresholds — the 12 agents will likely return HOLD, which is the correct behavior.

4. **Tier 1 threshold override**: Should BTC/ETH always be eligible regardless of vol_ratio / pct_change? Or is it acceptable to skip a scan cycle on BTC if it's flat? Recommendation: include Tier 1 pairs only if `pct_change > 1%` (softer threshold) to avoid spending analysis cycles on flat major pairs.

---

## Next Steps

- [ ] T8: Implement `CryptoTickerScanner` in `tradingagents/integrations/ticker_scanner.py`
- [ ] T8: Add `httpx` to `requirements.txt`
- [ ] T8: Add crypto scanner config keys to `tradingagents/default_config.py`
- [ ] T8: Update `autonomous_loop.py` to instantiate scanner by `scanner_mode`
- [ ] T8b (future): Bybit adapter fallback
- [ ] T8c (future): LunarCrush social signal integration
