"""Autonomous Crypto Ticker Scanner Agent.

Uses Binance 24hr market data + macro context + LLM reasoning to identify
promising crypto pairs for deep analysis.

Pipeline:
    0. Market regime context (BTC/ETH price, BTC dominance, Fear & Greed)
    1. Bulk Binance 24hr discovery (single API call → ~2000 pairs)
    2. In-memory pre-filter (blacklist, volume, pct_change, vol_ratio, tradeCount)
    3. Phase 2 enrichment (CoinGecko trending boost, funding rates)
    4. LLM scoring (5-dimension crypto-aware scoring + manipulation detection)
    5. Return top N candidates for deep 12-agent analysis

Brainstorm reference: docs/brainstorm-crypto-ticker-scanner.md
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Pairs that are always eligible with relaxed thresholds
_TIER1_PAIRS: Set[str] = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"}

# Regex patterns for automatic pre-LLM blacklisting
_BLACKLIST_PATTERNS: List[re.Pattern] = [
    re.compile(r"\d[LS]USDT$"),             # leveraged tokens: ETH3L, BTC2S
    re.compile(r"(UP|DOWN)USDT$"),           # leveraged up/down tokens
    re.compile(r"^(USDC|BUSD|DAI|TUSD|FDUSD)USDT$"),  # stablecoin pairs
    re.compile(r"^USDCUSDT$|^USDTUSDT$"),   # tautological pairs
    re.compile(r"^LUNC|^LUNA"),             # Terra collapse tokens
]

# Additional static blacklist (manipulation history)
_STATIC_BLACKLIST: Set[str] = {
    "BTTCUSDT", "HOTUSDT", "LUNCUSDT", "LUNAUSDT",
    "FTMUSDT",  # already rebranded
}

# Scam name fragments — raise LLM scrutiny, do not hard-exclude
_SCAM_NAME_FRAGMENTS = {"INU", "MOON", "SAFE", "ELON", "PEPE"}

# Fallback watchlist when Binance is unreachable
DEFAULT_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT",
    "XRPUSDT", "DOTUSDT", "AVAXUSDT", "MATICUSDT", "LINKUSDT",
    "UNIUSDT", "ATOMUSDT", "LTCUSDT", "DOGEUSDT", "ARBUSDT",
    "OPUSDT", "APTUSDT", "SUIUSDT", "INJUSDT", "TIAUSDT",
    "SEIUSDT", "BLURUSDT", "JUPUSDT", "WIFUSDT", "BNXUSDT",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScanCandidate:
    """A crypto pair identified by the scanner for potential deep analysis."""
    ticker: str
    score: float = 0.0
    catalyst: str = ""
    signal_type: str = ""      # "breakout", "momentum", "reversal", "volume_spike"
    urgency: str = "normal"    # "high", "normal", "low"
    manipulation_flag: bool = False
    # 5-dimension scores from LLM
    momentum_quality: int = 0
    liquidity_conviction: int = 0
    market_context_alignment: int = 0
    manipulation_risk_inverse: int = 0
    catalyst_quality: int = 0
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanResult:
    """Complete result of a scanning cycle."""
    timestamp: str
    candidates: List[ScanCandidate]
    market_regime: str = "neutral"
    scan_summary: str = ""
    total_screened: int = 0
    total_pre_filtered: int = 0


# ---------------------------------------------------------------------------
# TickerScanner
# ---------------------------------------------------------------------------

class TickerScanner:
    """Autonomous crypto pair discovery using Binance API + LLM ranking.

    Phase 0: Market regime context (BTC/ETH, BTC dominance, Fear & Greed)
    Phase 1: Bulk Binance discovery — single GET /api/v3/ticker/24hr call
    Phase 1b: Enrichment — vol_ratio via klines, CoinGecko trending
    Phase 2: Rule-based pre-filter with Tier 1 special treatment
    Phase 3: LLM 5-dimension crypto-aware scoring + manipulation detection
    """

    def __init__(
        self,
        llm=None,
        config: Optional[Dict[str, Any]] = None,
        analyzed_today: Optional[set] = None,
    ):
        self.llm = llm
        self.config = config or {}
        self.analyzed_today = analyzed_today or set()

        # --- Scanner parameters (brainstorm-aligned) ---
        self.min_quote_volume = self.config.get(
            "scanner_min_quote_volume",
            self.config.get("crypto_min_volume_usd", 5_000_000),
        )
        self.min_pct_change = self.config.get(
            "scanner_min_pct_change",
            self.config.get("crypto_min_price_change_pct", 2.0),
        )
        self.max_pct_change = self.config.get("scanner_max_pct_change", 40.0)
        self.min_vol_ratio = self.config.get("scanner_min_vol_ratio", 1.5)
        self.min_trade_count = self.config.get("scanner_min_trade_count", 1_000)
        self.max_tickers = self.config.get("max_tickers_per_scan", 3)
        self.quote_asset = self.config.get(
            "scanner_quote_currency",
            self.config.get("crypto_quote_asset", "USDT"),
        )
        self.cooldown_hours = self.config.get(
            "scanner_cooldown_hours",
            self.config.get("ticker_cooldown_hours", 4),
        )
        self.tier1_pairs: Set[str] = set(
            self.config.get("scanner_tier1_pairs", list(_TIER1_PAIRS))
        )
        # Context API cache (keyed by name → (value, timestamp))
        self._context_cache: Dict[str, Tuple[Any, float]] = {}
        self._context_cache_ttl = self.config.get("scanner_context_cache_ttl", 900)
        self._ticker_cache_ttl = self.config.get("scanner_cache_ttl_seconds", 60)
        self._bulk_cache: Optional[Tuple[List[Dict], float]] = None

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_get(self, key: str) -> Optional[Any]:
        if key not in self._context_cache:
            return None
        value, ts = self._context_cache[key]
        if time.time() - ts < self._context_cache_ttl:
            return value
        return None

    def _cache_set(self, key: str, value: Any) -> None:
        self._context_cache[key] = (value, time.time())

    # ------------------------------------------------------------------
    # Blacklist check
    # ------------------------------------------------------------------

    def _is_blacklisted(self, symbol: str) -> bool:
        """Return True if the symbol matches any blacklist rule."""
        if symbol in _STATIC_BLACKLIST:
            return True
        for pattern in _BLACKLIST_PATTERNS:
            if pattern.search(symbol):
                return True
        return False

    def _scam_name_warning(self, symbol: str) -> bool:
        """Return True if symbol contains known scam name fragments."""
        upper = symbol.upper()
        return any(frag in upper for frag in _SCAM_NAME_FRAGMENTS)

    # ------------------------------------------------------------------
    # Phase 0: Market Regime Context
    # ------------------------------------------------------------------

    def _fetch_fear_greed(self) -> Dict[str, Any]:
        """Fetch Crypto Fear & Greed Index from alternative.me. Cached 30 min."""
        cached = self._cache_get("fear_greed")
        if cached:
            return cached
        try:
            url = "https://api.alternative.me/fng/?limit=1"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
            entry = data["data"][0]
            result = {
                "value": int(entry["value"]),
                "classification": entry["value_classification"],
            }
            self._cache_set("fear_greed", result)
            return result
        except Exception as exc:
            logger.debug("Fear & Greed fetch failed: %s", exc)
            return {"value": 50, "classification": "Neutral"}

    def _fetch_btc_dominance(self) -> float:
        """Fetch BTC dominance % from CoinGecko /global. Cached 15 min."""
        cached = self._cache_get("btc_dominance")
        if cached is not None:
            return cached
        try:
            url = "https://api.coingecko.com/api/v3/global"
            req = urllib.request.Request(url, headers={"accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read())
            pct = float(data["data"]["market_cap_percentage"].get("btc", 0))
            self._cache_set("btc_dominance", pct)
            return pct
        except Exception as exc:
            logger.debug("BTC dominance fetch failed: %s", exc)
            return 50.0  # neutral assumption

    def _fetch_coingecko_trending(self) -> List[str]:
        """Fetch CoinGecko top trending coin symbols. Cached 15 min."""
        cached = self._cache_get("trending_coins")
        if cached is not None:
            return cached
        try:
            url = "https://api.coingecko.com/api/v3/search/trending"
            req = urllib.request.Request(url, headers={"accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read())
            symbols = [
                c["item"]["symbol"].upper()
                for c in data.get("coins", [])[:7]
                if "item" in c
            ]
            self._cache_set("trending_coins", symbols)
            return symbols
        except Exception as exc:
            logger.debug("CoinGecko trending fetch failed: %s", exc)
            return []

    def _fetch_binance_context(self, symbol: str) -> Dict[str, Any]:
        """Fetch 24hr ticker for a specific symbol (BTC or ETH context)."""
        cache_key = f"ticker_{symbol}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        try:
            from tradingagents.dataflows.binance_client import BinanceClient
            client = BinanceClient()
            t = client.get_ticker_24hr(symbol)
            result = {
                "price": float(t.get("lastPrice", 0)),
                "change_pct": float(t.get("priceChangePercent", 0)),
                "volume_usdt": float(t.get("quoteVolume", 0)),
            }
            self._cache_set(cache_key, result)
            return result
        except Exception as exc:
            logger.debug("%s context fetch failed: %s", symbol, exc)
            return {}

    def _fetch_vol_ratio(self, symbol: str) -> float:
        """Compute vol_ratio: today's volume / 7-day hourly average.

        Fetches last 168 hours of 1h klines and compares today's 24h volume.
        Returns 1.0 on any failure (neutral assumption).
        """
        try:
            from tradingagents.dataflows.binance_client import BinanceClient
            client = BinanceClient()
            klines = client.get_klines(symbol=symbol, interval="1h", limit=168)
            if len(klines) < 24:
                return 1.0
            # Volume for each candle is kline[5]
            all_vols = [float(k[5]) for k in klines]
            # Last 24 bars = today
            today_vol = sum(all_vols[-24:])
            # Previous 144 bars / 6 = 6-day average (1-day windows)
            prev_vols = all_vols[:-24]
            if not prev_vols:
                return 1.0
            # Average daily volume over preceding days
            days = max(1, len(prev_vols) // 24)
            prev_daily_avg = sum(prev_vols) / days
            return round(today_vol / prev_daily_avg, 2) if prev_daily_avg > 0 else 1.0
        except Exception as exc:
            logger.debug("vol_ratio fetch for %s failed: %s", symbol, exc)
            return 1.0

    def _get_market_regime(self, btc_ctx: Dict, eth_ctx: Dict, fng: Dict, btc_dom: float) -> str:
        """Derive market regime from BTC change, ETH change, Fear & Greed, BTC dominance."""
        fng_val = fng.get("value", 50)
        btc_chg = btc_ctx.get("change_pct", 0)
        eth_chg = eth_ctx.get("change_pct", 0)

        majors_up = btc_chg > 2 and eth_chg > 1
        majors_down = btc_chg < -2 or eth_chg < -3

        if majors_up and fng_val > 60:
            regime = "bullish_altseason" if btc_dom < 48 else "bullish_btc_dominant"
        elif majors_down or fng_val < 25:
            regime = "bearish"
        else:
            regime = "neutral"

        return regime

    # ------------------------------------------------------------------
    # Phase 1: Bulk Discovery
    # ------------------------------------------------------------------

    def _fetch_all_pairs_bulk(self) -> List[Dict]:
        """Single GET /api/v3/ticker/24hr → all spot pairs.

        Returns raw list of Binance ticker dicts for *USDT pairs, with blacklisted
        entries already removed. Caches for scanner_cache_ttl_seconds.
        """
        now = time.time()
        if self._bulk_cache is not None:
            cached_data, ts = self._bulk_cache
            if now - ts < self._ticker_cache_ttl:
                return cached_data

        try:
            from tradingagents.dataflows.binance_client import BinanceClient
            client = BinanceClient()
            all_tickers = client.get_ticker_24hr()
        except Exception:
            logger.exception("Bulk Binance fetch failed — using DEFAULT_UNIVERSE fallback")
            return [{"symbol": s} for s in DEFAULT_UNIVERSE]

        results = []
        for t in all_tickers:
            sym: str = t.get("symbol", "")
            if not sym.endswith(self.quote_asset):
                continue
            if self._is_blacklisted(sym):
                continue
            if sym in self.analyzed_today:
                continue
            results.append(t)

        self._bulk_cache = (results, now)
        logger.info("Bulk fetch: %d USDT pairs after blacklist/cooldown filter", len(results))
        return results

    # ------------------------------------------------------------------
    # Phase 2: Rule-Based Pre-Filter
    # ------------------------------------------------------------------

    def _is_tier1(self, symbol: str) -> bool:
        return symbol in self.tier1_pairs

    def pre_filter(self, all_pairs: List[Dict], trending_symbols: List[str]) -> List[Dict]:
        """Apply brainstorm-specified pre-filter thresholds.

        Tier 1 assets (BTC, ETH, BNB, SOL) use relaxed thresholds:
        - min_quote_volume: $1M (vs $5M)
        - min_pct_change: 1% (vs 2%)
        - min_vol_ratio: 1.2x (vs 1.5x)
        - no max_pct_change cap

        All others:
        - min_quote_volume: scanner_min_quote_volume
        - abs(pct_change) in [min_pct_change, max_pct_change]
        - trade_count >= min_trade_count
        - vol_ratio >= min_vol_ratio (computed separately for shortlist)
        """
        candidates = []
        trending_set = {s.upper() for s in trending_symbols}
        seen_symbols: set = set()

        for t in all_pairs:
            sym: str = t.get("symbol", "")
            if not sym or sym in seen_symbols:
                continue
            seen_symbols.add(sym)
            try:
                pct_change = float(t.get("priceChangePercent", 0))
                quote_vol = float(t.get("quoteVolume", 0))
                trade_count = int(t.get("count", 0))
                last_price = float(t.get("lastPrice", 0))
            except (TypeError, ValueError):
                continue

            tier1 = self._is_tier1(sym)

            # Volume gate
            min_vol = 1_000_000 if tier1 else self.min_quote_volume
            if quote_vol < min_vol:
                continue

            # Trade count gate
            if not tier1 and trade_count < self.min_trade_count:
                continue

            # Price change gate
            abs_pct = abs(pct_change)
            min_pct = 1.0 if tier1 else self.min_pct_change
            if abs_pct < min_pct:
                continue
            # Max cap — skip Tier 1 (BTC/ETH can move 40%+ on real events)
            if not tier1 and abs_pct > self.max_pct_change:
                logger.debug("Skipping %s: %+.1f%% exceeds max cap", sym, pct_change)
                continue

            # Detect signal type
            base = sym.replace("USDT", "")
            is_trending = base.upper() in trending_set
            signal_parts = []
            if abs_pct >= self.min_pct_change:
                signal_parts.append("momentum_up" if pct_change > 0 else "momentum_down")
            if quote_vol >= self.min_quote_volume * 3:
                signal_parts.append("volume_spike")
            if is_trending:
                signal_parts.append("trending")

            scam_warn = self._scam_name_warning(sym)

            candidates.append({
                "ticker": sym,
                "price": last_price,
                "pct_change": pct_change,
                "quote_volume": quote_vol,
                "trade_count": trade_count,
                "vol_ratio": 1.0,   # enriched separately for shortlist
                "signal": "+".join(signal_parts) or "screened",
                "is_tier1": tier1,
                "is_trending": is_trending,
                "scam_warning": scam_warn,
                "funding_rate": "N/A",
            })

        # Sort: Tier 1 first, then by quote_volume desc, take top 40 for enrichment
        candidates.sort(
            key=lambda x: (not x["is_tier1"], -x["quote_volume"])
        )
        logger.info("Pre-filter: %d pairs → %d candidates", len(all_pairs), len(candidates))
        return candidates[:40]

    # ------------------------------------------------------------------
    # Phase 1b: Enrichment (vol_ratio via klines for shortlist)
    # ------------------------------------------------------------------

    def _enrich_with_vol_ratios(self, candidates: List[Dict]) -> List[Dict]:
        """Fetch klines for top-15 candidates and compute real vol_ratio.

        Only fetches for non-Tier-1 candidates (Tier 1 ratios are less important).
        Rate: 2 weight per kline call × 15 = 30 weight total (safe).
        """
        # Only enrich the top 15 sorted by quote_volume to control API calls
        to_enrich = [c for c in candidates[:15] if not c["is_tier1"]]
        for c in to_enrich:
            ratio = self._fetch_vol_ratio(c["ticker"])
            c["vol_ratio"] = ratio
            # Apply vol_ratio filter: skip if below threshold (mark, don't remove here)
            if ratio < (1.2 if c["is_tier1"] else self.min_vol_ratio):
                c["signal"] = c["signal"] + "+low_vol_ratio"

        return candidates

    def _apply_vol_ratio_filter(self, candidates: List[Dict]) -> List[Dict]:
        """Remove candidates where vol_ratio is below threshold (post-enrichment)."""
        kept = []
        for c in candidates:
            ratio = c.get("vol_ratio", 1.0)
            min_ratio = 1.2 if c.get("is_tier1") else self.min_vol_ratio
            # If ratio is still 1.0 (not enriched), keep it (unenriched = benefit of doubt)
            if ratio == 1.0 or ratio >= min_ratio:
                kept.append(c)
            else:
                logger.debug("Dropping %s: vol_ratio %.2fx < threshold %.2fx", c["ticker"], ratio, min_ratio)
        return kept

    # ------------------------------------------------------------------
    # Phase 3: LLM Ranking (5-dimension crypto-aware)
    # ------------------------------------------------------------------

    def _build_candidates_text(self, candidates: List[Dict]) -> str:
        lines = []
        for c in candidates[:15]:
            scam = " [SCAM_WARNING]" if c.get("scam_warning") else ""
            trending = " [TRENDING]" if c.get("is_trending") else ""
            lines.append(
                f"  {c['ticker']}: change={c.get('pct_change', 0):+.1f}%, "
                f"quoteVol=${c.get('quote_volume', 0):,.0f}, "
                f"vol_ratio={c.get('vol_ratio', 1.0):.1f}x, "
                f"tradeCount={c.get('trade_count', 0):,}, "
                f"signal={c.get('signal', '?')}, "
                f"funding={c.get('funding_rate', 'N/A')}"
                f"{trending}{scam}"
            )
        return "\n".join(lines)

    _SYSTEM_PROMPT = """You are a Crypto Market Scanner Agent. You identify high-probability trading opportunities in cryptocurrency spot markets.

Your job is to score a shortlist of crypto trading pairs and identify the top candidates for deep multi-agent analysis.

SCORING DIMENSIONS (each 1-10):
- momentum_quality: Is the price move backed by genuine volume? Is it early-stage (not already extended)?
- liquidity_conviction: Is 24h USDT volume high enough to enter and exit a position cleanly?
- market_context_alignment: Given BTC/ETH performance and market regime, does this altcoin setup make sense?
- manipulation_risk_inverse: How confident are you this is NOT a pump-and-dump? (10 = very safe, 1 = obvious manipulation)
- catalyst_quality: Is there an identifiable reason for the move (protocol news, listing, on-chain activity)?

MANIPULATION FLAGS — set manipulation_flag=true if ANY of these apply:
- pct_change > 50% with quoteVol < $15M
- vol_ratio > 12x with no identifiable catalyst
- Coin name contains classic pump-and-dump patterns (INU, MOON, SAFE, ELON, etc.)
- SCAM_WARNING tag present in candidate data

DISQUALIFY: if manipulation_flag=true, do NOT include that candidate in the output list.
If ALL candidates fail quality bar, return an empty candidates list — do NOT force picks.

total_score = (momentum_quality + liquidity_conviction + market_context_alignment + manipulation_risk_inverse + catalyst_quality) / 5"""

    def _llm_rank(
        self,
        candidates: List[Dict],
        context: Dict[str, Any],
        total_screened: int,
        total_pre_filtered: int,
    ) -> ScanResult:
        """Rank candidates using 5-dimension LLM scoring."""
        btc_ctx = context.get("btc_context", {})
        eth_ctx = context.get("eth_context", {})
        fng = context.get("fear_greed", {})
        btc_dom = context.get("btc_dominance", 50.0)
        regime = context.get("market_regime", "neutral")
        trending = context.get("trending_coins", [])
        trending_text = ", ".join(trending) if trending else "N/A"
        candidates_text = self._build_candidates_text(candidates)

        user_prompt = f"""MARKET CONTEXT:
- BTC 24h: {btc_ctx.get('change_pct', 0):+.1f}% (${btc_ctx.get('price', 0):,.0f})
- ETH 24h: {eth_ctx.get('change_pct', 0):+.1f}%
- BTC Dominance: {btc_dom:.1f}%
- Fear & Greed Index: {fng.get('value', 50)}/100 ({fng.get('classification', 'Neutral')})
- Market Regime: {regime}
- CoinGecko Trending: {trending_text}

CANDIDATES ({len(candidates)} pairs after pre-filter):
{candidates_text}

Score each candidate on all 5 dimensions. Disqualify any with manipulation_flag=true.
Return top {self.max_tickers} by total_score. If all fail quality bar, return empty candidates list.

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
  "market_regime": "{regime}",
  "scan_summary": "Brief 1-2 sentence summary of market conditions and selection rationale"
}}"""

        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            response = self.llm.invoke([
                SystemMessage(content=self._SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])
        except Exception:
            # Fallback for LLMs that accept plain string
            response = self.llm.invoke(f"{self._SYSTEM_PROMPT}\n\n{user_prompt}")

        text = response.content if hasattr(response, "content") else str(response)
        parsed = self._extract_json(text)
        if not parsed or "candidates" not in parsed:
            raise ValueError(f"LLM returned unparseable response: {text[:300]}")

        result_candidates = []
        for c in parsed["candidates"][:self.max_tickers]:
            if c.get("manipulation_flag"):
                logger.warning("LLM flagged %s as manipulation — skipping", c.get("ticker"))
                continue
            result_candidates.append(ScanCandidate(
                ticker=c.get("ticker", ""),
                score=float(c.get("score", 0)),
                catalyst=c.get("catalyst", ""),
                signal_type=c.get("signal_type", "unknown"),
                urgency=c.get("urgency", "normal"),
                manipulation_flag=bool(c.get("manipulation_flag", False)),
                momentum_quality=int(c.get("momentum_quality", 0)),
                liquidity_conviction=int(c.get("liquidity_conviction", 0)),
                market_context_alignment=int(c.get("market_context_alignment", 0)),
                manipulation_risk_inverse=int(c.get("manipulation_risk_inverse", 0)),
                catalyst_quality=int(c.get("catalyst_quality", 0)),
            ))

        return ScanResult(
            timestamp=datetime.now().isoformat(),
            candidates=result_candidates,
            market_regime=parsed.get("market_regime", regime),
            scan_summary=parsed.get("scan_summary", ""),
            total_screened=total_screened,
            total_pre_filtered=total_pre_filtered,
        )

    def _rule_based_rank(
        self,
        candidates: List[Dict],
        total_screened: int,
        total_pre_filtered: int,
        market_regime: str = "neutral",
    ) -> ScanResult:
        """Fallback ranking: (|pct_change| * 0.5 + vol_ratio * 2) — scam_penalty."""
        scored = []
        for c in candidates:
            pct = abs(c.get("pct_change", 0))
            vol = c.get("vol_ratio", 1.0)
            scam_pen = 2.0 if c.get("scam_warning") else 0.0
            score = round(min(10, max(0, pct * 0.5 + vol * 2.0 - scam_pen)), 1)
            signal = "momentum" if c.get("pct_change", 0) > 0 else "reversal"
            scored.append(ScanCandidate(
                ticker=c["ticker"],
                score=score,
                catalyst=f"{c.get('pct_change', 0):+.1f}% move, {vol:.1f}x volume",
                signal_type=signal,
                urgency="high" if score > 7 else "normal",
                raw_data=c,
            ))

        scored.sort(key=lambda x: x.score, reverse=True)
        return ScanResult(
            timestamp=datetime.now().isoformat(),
            candidates=scored[:self.max_tickers],
            market_regime=market_regime,
            scan_summary=f"Rule-based ranking of {len(candidates)} candidates (LLM unavailable)",
            total_screened=total_screened,
            total_pre_filtered=total_pre_filtered,
        )

    def rank_candidates(
        self,
        candidates: List[Dict],
        context: Dict[str, Any],
    ) -> ScanResult:
        """LLM rank → rule-based fallback."""
        total_screened = context.get("total_screened", len(candidates))
        total_pre_filtered = len(candidates)
        regime = context.get("market_regime", "neutral")

        if not candidates:
            return ScanResult(
                timestamp=datetime.now().isoformat(),
                candidates=[],
                market_regime=regime,
                scan_summary="No candidates found after pre-filtering.",
                total_screened=total_screened,
                total_pre_filtered=0,
            )

        if self.llm is not None:
            try:
                return self._llm_rank(candidates, context, total_screened, total_pre_filtered)
            except Exception:
                logger.exception("LLM ranking failed — falling back to rule-based scoring")

        return self._rule_based_rank(candidates, total_screened, total_pre_filtered, regime)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _extract_json(self, text: str) -> Optional[Dict]:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        for pattern in [r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except (json.JSONDecodeError, TypeError):
                    continue
        # Last resort: extract first {...} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    # ------------------------------------------------------------------
    # Main scan entry point
    # ------------------------------------------------------------------

    def scan(self) -> ScanResult:
        """Run the full scanner pipeline: context → bulk → filter → enrich → rank."""
        logger.info("=== Starting crypto ticker scan ===")

        # Phase 0: Market context (cached)
        btc_ctx = self._fetch_binance_context("BTCUSDT")
        eth_ctx = self._fetch_binance_context("ETHUSDT")
        fng = self._fetch_fear_greed()
        btc_dom = self._fetch_btc_dominance()
        trending = self._fetch_coingecko_trending()
        regime = self._get_market_regime(btc_ctx, eth_ctx, fng, btc_dom)

        context: Dict[str, Any] = {
            "btc_context": btc_ctx,
            "eth_context": eth_ctx,
            "fear_greed": fng,
            "btc_dominance": btc_dom,
            "market_regime": regime,
            "trending_coins": trending,
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            "Market context: regime=%s, BTC %+.1f%%, ETH %+.1f%%, F&G %d, BTC.D %.1f%%, trending=%s",
            regime,
            btc_ctx.get("change_pct", 0),
            eth_ctx.get("change_pct", 0),
            fng.get("value", 50),
            btc_dom,
            trending[:3],
        )

        # Phase 1: Bulk Binance discovery
        all_pairs = self._fetch_all_pairs_bulk()
        context["total_screened"] = len(all_pairs)

        # Phase 2: Rule-based pre-filter
        candidates = self.pre_filter(all_pairs, trending)

        # Phase 1b: Enrich top-15 with vol_ratio via klines
        candidates = self._enrich_with_vol_ratios(candidates)
        candidates = self._apply_vol_ratio_filter(candidates)

        logger.info(
            "Post-enrichment: %d candidates (vol_ratio filtered), regime=%s",
            len(candidates),
            regime,
        )

        # Phase 3: LLM ranking
        result = self.rank_candidates(candidates, context)

        logger.info(
            "Scan complete: screened=%d, pre-filtered=%d, ranked=%d",
            result.total_screened,
            result.total_pre_filtered,
            len(result.candidates),
        )

        return result

    # ------------------------------------------------------------------
    # Legacy collect_market_data / _scan_news (backward compat)
    # ------------------------------------------------------------------

    def collect_market_data(self) -> Dict[str, Any]:
        """Legacy interface for compatibility. Prefer scan() for full pipeline."""
        btc_ctx = self._fetch_binance_context("BTCUSDT")
        fng = self._fetch_fear_greed()
        trending = self._fetch_coingecko_trending()
        regime = self._get_market_regime(btc_ctx, {}, fng, 50.0)

        data: Dict[str, Any] = {
            "gainers": [],
            "losers": [],
            "volume_spikes": [],
            "sector_performance": {},
            "news_headlines": [{"ticker": s + "USDT", "title": f"Trending: {s}", "publisher": "CoinGecko"} for s in trending[:3]],
            "btc_context": btc_ctx,
            "fear_greed": fng,
            "market_regime": regime,
            "timestamp": datetime.now().isoformat(),
        }

        all_pairs = self._fetch_all_pairs_bulk()
        candidates = self.pre_filter(all_pairs, trending)

        for c in candidates:
            entry = {
                "ticker": c["ticker"],
                "price": c["price"],
                "pct_change": c["pct_change"],
                "volume": int(c["quote_volume"]),
                "vol_ratio": c.get("vol_ratio", 1.0),
            }
            if c["pct_change"] >= self.min_pct_change:
                data["gainers"].append(entry)
            elif c["pct_change"] <= -self.min_pct_change:
                data["losers"].append(entry)
            if c["quote_volume"] >= self.min_quote_volume * 3:
                data["volume_spikes"].append(entry)

        return data

    def _scan_news(self, tickers: List[str]) -> List[Dict]:
        """Legacy: returns trending headlines from CoinGecko."""
        trending = self._fetch_coingecko_trending()
        return [
            {"ticker": s + "USDT", "title": f"Trending: {s}", "publisher": "CoinGecko"}
            for s in trending[:5]
        ]

