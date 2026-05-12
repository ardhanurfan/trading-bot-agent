"""Autonomous Ticker Scanner Agent.

Uses yfinance market data + LLM reasoning to identify promising stocks
for deep analysis. This replaces manual ticker selection with an
intelligent, autonomous discovery pipeline.

Pipeline:
    1. Collect raw market data (gainers, losers, volume spikes, news)
    2. Rule-based pre-filter (price, volume, dedup)
    3. LLM ranking (score 1-10 on catalyst, volume, technical, news)
    4. Return top N candidates for deep analysis
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
except ImportError:
    yf = None

# ---------------------------------------------------------------------------
# S&P 500 / Nasdaq 100 universe helpers
# ---------------------------------------------------------------------------

# Top ~50 most liquid US stocks as a default universe
# (avoids scraping Wikipedia at runtime)
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "UNH", "JNJ", "V", "XOM", "JPM", "WMT", "MA", "PG", "HD", "CVX",
    "MRK", "ABBV", "LLY", "PEP", "KO", "COST", "AVGO", "MCD", "TMO",
    "CSCO", "ACN", "ABT", "CRM", "ORCL", "NKE", "AMD", "INTC", "QCOM",
    "TXN", "NEE", "UPS", "RTX", "LOW", "SBUX", "GS", "BLK", "AMAT",
    "ISRG", "BKNG", "MDLZ", "ADP", "GILD",
]


@dataclass
class ScanCandidate:
    """A stock identified by the scanner for potential deep analysis."""
    ticker: str
    score: float = 0.0
    catalyst: str = ""
    signal_type: str = ""  # "momentum", "volume_spike", "news", "reversal"
    urgency: str = "normal"  # "high", "normal", "low"
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


class TickerScanner:
    """Autonomous ticker discovery using yfinance + LLM ranking.

    Phase 1: Rule-based screener (fast, free)
    Phase 2: LLM-powered ranking (smart, focused)
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

        # Scanner parameters from config
        self.min_price = self.config.get("scanner_min_price", 5.0)
        self.min_volume = self.config.get("scanner_min_volume", 500_000)
        self.max_tickers = self.config.get("max_tickers_per_scan", 3)
        self.universe = self._get_universe()

    def _get_universe(self) -> List[str]:
        """Get the stock universe to scan."""
        universe_type = self.config.get("scanner_universe", "default")
        if universe_type == "custom":
            return self.config.get("scanner_custom_watchlist", DEFAULT_UNIVERSE)
        return DEFAULT_UNIVERSE

    # ------------------------------------------------------------------
    # Phase 1: Data Collection + Rule-Based Pre-Filter
    # ------------------------------------------------------------------

    def collect_market_data(self) -> Dict[str, Any]:
        """Collect market data from yfinance for scanner analysis."""
        if yf is None:
            logger.error("yfinance not installed")
            return {}

        data = {
            "gainers": [],
            "losers": [],
            "volume_spikes": [],
            "sector_performance": {},
            "news_headlines": [],
            "timestamp": datetime.now().isoformat(),
        }

        try:
            # 1. Scan universe for movers and volume spikes
            logger.info("Scanning %d stocks in universe...", len(self.universe))
            batch_data = self._batch_scan_universe()
            data["gainers"] = batch_data.get("gainers", [])
            data["losers"] = batch_data.get("losers", [])
            data["volume_spikes"] = batch_data.get("volume_spikes", [])

            # 2. Sector ETF performance
            data["sector_performance"] = self._scan_sectors()

            # 3. News headlines from top movers
            top_movers = [g["ticker"] for g in data["gainers"][:5]] + \
                         [l["ticker"] for l in data["losers"][:3]]
            data["news_headlines"] = self._scan_news(top_movers[:5])

        except Exception:
            logger.exception("Error collecting market data")

        return data

    def _batch_scan_universe(self) -> Dict[str, List[Dict]]:
        """Scan the universe for gainers, losers, and volume spikes."""
        gainers, losers, volume_spikes = [], [], []

        # Process in batches to avoid rate limits
        batch_size = 10
        for i in range(0, len(self.universe), batch_size):
            batch = self.universe[i:i + batch_size]
            try:
                tickers_str = " ".join(batch)
                data = yf.download(
                    tickers_str,
                    period="5d",
                    interval="1d",
                    group_by="ticker",
                    progress=False,
                    threads=True,
                )

                for ticker in batch:
                    try:
                        if len(batch) == 1:
                            df = data
                        else:
                            df = data[ticker] if ticker in data.columns.get_level_values(0) else None

                        if df is None or df.empty or len(df) < 2:
                            continue

                        latest = df.iloc[-1]
                        prev = df.iloc[-2]

                        price = float(latest["Close"])
                        volume = float(latest["Volume"])
                        prev_close = float(prev["Close"])

                        # Price and volume filters
                        if price < self.min_price or volume < self.min_volume:
                            continue

                        pct_change = ((price - prev_close) / prev_close) * 100

                        # Volume spike detection (vs average)
                        avg_volume = float(df["Volume"].mean())
                        vol_ratio = volume / avg_volume if avg_volume > 0 else 1.0

                        entry = {
                            "ticker": ticker,
                            "price": round(price, 2),
                            "pct_change": round(pct_change, 2),
                            "volume": int(volume),
                            "avg_volume": int(avg_volume),
                            "vol_ratio": round(vol_ratio, 2),
                        }

                        if pct_change > 2.0:
                            gainers.append(entry)
                        elif pct_change < -2.0:
                            losers.append(entry)

                        if vol_ratio > 1.5:
                            volume_spikes.append(entry)

                    except Exception:
                        continue  # Skip problematic tickers

            except Exception:
                logger.warning("Batch scan failed for: %s", batch)
                continue

        # Sort by magnitude
        gainers.sort(key=lambda x: x["pct_change"], reverse=True)
        losers.sort(key=lambda x: x["pct_change"])
        volume_spikes.sort(key=lambda x: x["vol_ratio"], reverse=True)

        return {
            "gainers": gainers[:10],
            "losers": losers[:5],
            "volume_spikes": volume_spikes[:10],
        }

    def _scan_sectors(self) -> Dict[str, float]:
        """Check sector ETF performance for rotation signals."""
        sector_etfs = {
            "Technology": "XLK",
            "Healthcare": "XLV",
            "Financials": "XLF",
            "Energy": "XLE",
            "Consumer": "XLY",
            "Industrials": "XLI",
            "Utilities": "XLU",
            "Real Estate": "XLRE",
        }
        performance = {}
        try:
            tickers_str = " ".join(sector_etfs.values())
            data = yf.download(tickers_str, period="5d", interval="1d", progress=False, threads=True)
            for sector, etf in sector_etfs.items():
                try:
                    if etf in data.columns.get_level_values(0):
                        df = data[etf]
                    else:
                        df = data
                    if not df.empty and len(df) >= 2:
                        pct = ((float(df["Close"].iloc[-1]) - float(df["Close"].iloc[-2])) /
                               float(df["Close"].iloc[-2])) * 100
                        performance[sector] = round(pct, 2)
                except Exception:
                    continue
        except Exception:
            logger.warning("Sector scan failed")
        return performance

    def _scan_news(self, tickers: List[str]) -> List[Dict]:
        """Get recent news headlines for given tickers."""
        headlines = []
        for ticker in tickers[:5]:
            try:
                stock = yf.Ticker(ticker)
                news = stock.news
                if news:
                    for item in news[:2]:
                        headlines.append({
                            "ticker": ticker,
                            "title": item.get("title", ""),
                            "publisher": item.get("publisher", ""),
                        })
            except Exception:
                continue
        return headlines

    # ------------------------------------------------------------------
    # Phase 2: Pre-Filtering
    # ------------------------------------------------------------------

    def pre_filter(self, market_data: Dict[str, Any]) -> List[Dict]:
        """Rule-based pre-filter to reduce candidates before LLM ranking."""
        candidates = {}

        # Merge all sources
        for item in market_data.get("gainers", []):
            candidates[item["ticker"]] = {**item, "signal": "momentum_up"}
        for item in market_data.get("losers", []):
            if item["ticker"] not in candidates:
                candidates[item["ticker"]] = {**item, "signal": "momentum_down"}
        for item in market_data.get("volume_spikes", []):
            if item["ticker"] in candidates:
                candidates[item["ticker"]]["signal"] += "+volume"
            else:
                candidates[item["ticker"]] = {**item, "signal": "volume_spike"}

        # Remove already analyzed today
        filtered = {
            k: v for k, v in candidates.items()
            if k not in self.analyzed_today
        }

        logger.info(
            "Pre-filter: %d raw → %d after dedup → %d after cooldown",
            len(candidates) + len(market_data.get("volume_spikes", [])),
            len(candidates),
            len(filtered),
        )

        return list(filtered.values())

    # ------------------------------------------------------------------
    # Phase 3: LLM Ranking
    # ------------------------------------------------------------------

    def rank_candidates(
        self,
        candidates: List[Dict],
        market_data: Dict[str, Any],
    ) -> ScanResult:
        """Use LLM to rank and score candidates. Falls back to rule-based."""
        total_screened = len(self.universe)
        total_pre_filtered = len(candidates)

        if not candidates:
            return ScanResult(
                timestamp=datetime.now().isoformat(),
                candidates=[],
                scan_summary="No candidates found after pre-filtering.",
                total_screened=total_screened,
                total_pre_filtered=0,
            )

        # Try LLM ranking
        if self.llm is not None:
            try:
                return self._llm_rank(candidates, market_data, total_screened, total_pre_filtered)
            except Exception:
                logger.exception("LLM ranking failed — falling back to rule-based")

        # Fallback: score by |pct_change| * vol_ratio
        return self._rule_based_rank(candidates, total_screened, total_pre_filtered)

    def _llm_rank(
        self,
        candidates: List[Dict],
        market_data: Dict[str, Any],
        total_screened: int,
        total_pre_filtered: int,
    ) -> ScanResult:
        """Rank candidates using LLM reasoning."""
        # Build prompt
        candidates_text = "\n".join([
            f"  {c['ticker']}: price=${c.get('price', '?')}, change={c.get('pct_change', '?')}%, "
            f"volume_ratio={c.get('vol_ratio', '?')}x, signal={c.get('signal', '?')}"
            for c in candidates[:15]
        ])

        sectors = market_data.get("sector_performance", {})
        sector_text = ", ".join([f"{k}: {v:+.1f}%" for k, v in sectors.items()]) or "N/A"

        news = market_data.get("news_headlines", [])
        news_text = "\n".join([
            f"  [{n.get('ticker', '?')}] {n.get('title', '')}"
            for n in news[:5]
        ]) or "No recent news"

        prompt = f"""You are a Market Scanner Agent. Analyze these candidates and return the top {self.max_tickers} for deep analysis.

CANDIDATES:
{candidates_text}

SECTOR PERFORMANCE: {sector_text}

NEWS:
{news_text}

Score each on: catalyst (1-10), volume_conviction (1-10), technical_setup (1-10), news_relevance (1-10).
Total score = average of all four.

Return ONLY valid JSON (no markdown, no explanation):
{{
  "candidates": [
    {{"ticker": "NVDA", "score": 8.5, "catalyst": "reason", "signal_type": "momentum", "urgency": "high"}}
  ],
  "market_regime": "bullish|bearish|neutral",
  "scan_summary": "Brief summary"
}}"""

        response = self.llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)

        # Extract JSON from response
        parsed = self._extract_json(text)
        if not parsed or "candidates" not in parsed:
            raise ValueError(f"LLM returned unparseable response: {text[:200]}")

        result_candidates = []
        for c in parsed["candidates"][:self.max_tickers]:
            result_candidates.append(ScanCandidate(
                ticker=c.get("ticker", ""),
                score=float(c.get("score", 0)),
                catalyst=c.get("catalyst", ""),
                signal_type=c.get("signal_type", "unknown"),
                urgency=c.get("urgency", "normal"),
            ))

        return ScanResult(
            timestamp=datetime.now().isoformat(),
            candidates=result_candidates,
            market_regime=parsed.get("market_regime", "neutral"),
            scan_summary=parsed.get("scan_summary", ""),
            total_screened=total_screened,
            total_pre_filtered=total_pre_filtered,
        )

    def _rule_based_rank(
        self,
        candidates: List[Dict],
        total_screened: int,
        total_pre_filtered: int,
    ) -> ScanResult:
        """Fallback ranking using simple scoring formula."""
        scored = []
        for c in candidates:
            pct = abs(c.get("pct_change", 0))
            vol = c.get("vol_ratio", 1.0)
            score = round(min(10, (pct * 0.5 + vol * 2.0)), 1)
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
            scan_summary=f"Rule-based ranking of {len(candidates)} candidates",
            total_screened=total_screened,
            total_pre_filtered=total_pre_filtered,
        )

    def _extract_json(self, text: str) -> Optional[Dict]:
        """Extract JSON from LLM response (handles markdown fences)."""
        # Try direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass

        # Try extracting from markdown code fence
        patterns = [
            r"```json\s*\n(.*?)```",
            r"```\s*\n(.*?)```",
            r"\{.*\}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1) if match.lastindex else match.group(0))
                except (json.JSONDecodeError, TypeError):
                    continue
        return None

    # ------------------------------------------------------------------
    # Main scan entry point
    # ------------------------------------------------------------------

    def scan(self) -> ScanResult:
        """Run the full scanner pipeline: collect → filter → rank."""
        logger.info("=== Starting ticker scan ===")

        # Phase 1: Collect
        market_data = self.collect_market_data()

        # Phase 2: Pre-filter
        candidates = self.pre_filter(market_data)

        # Phase 3: LLM rank
        result = self.rank_candidates(candidates, market_data)

        logger.info(
            "Scan complete: screened=%d, pre-filtered=%d, top=%d candidates",
            result.total_screened,
            result.total_pre_filtered,
            len(result.candidates),
        )

        return result
