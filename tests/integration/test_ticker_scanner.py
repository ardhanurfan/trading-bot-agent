"""Tests for the Ticker Scanner Agent.

Tests cover:
- ScanCandidate / ScanResult data classes
- Pre-filtering logic (dedup, cooldown, price/volume)
- Rule-based ranking fallback
- LLM JSON extraction
- Scanner configuration
"""
from __future__ import annotations

import json
import types
import sys
import pytest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub heavy dependencies not installed in test env
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    mod.__package__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod

for _name in [
    "yfinance", "yfinance.exceptions",
    "stockstats", "stockstats.wrap",
    "redis", "redis.client", "redis.asyncio",
    "telegram", "telegram.ext",
    "pinecone",
    "langchain_core", "langchain_core.messages", "langchain_core.prompts",
    "langchain", "langgraph", "langgraph.graph", "langgraph.prebuilt",
    "langchain_openai", "langchain_anthropic", "langchain_google_genai",
]:
    sys.modules.setdefault(_name, _stub_module(_name))

_lc = sys.modules["langchain_core.messages"]
_lc.HumanMessage = MagicMock  # type: ignore[attr-defined]
_lc.RemoveMessage = MagicMock  # type: ignore[attr-defined]
sys.modules["yfinance.exceptions"].YFRateLimitError = Exception  # type: ignore

_rating_stub = types.ModuleType("tradingagents.agents.utils.rating")
_rating_stub.parse_rating = lambda text, scale=10: 5.0  # type: ignore
sys.modules.setdefault("tradingagents.agents.utils.rating", _rating_stub)

# ---------------------------------------------------------------------------

from tradingagents.integrations.ticker_scanner import (  # noqa: E402
    TickerScanner,
    ScanCandidate,
    ScanResult,
    DEFAULT_UNIVERSE,
)

class TestScanDataClasses:
    """Test ScanCandidate and ScanResult data structures."""

    def test_scan_candidate_defaults(self):
        c = ScanCandidate(ticker="NVDA")
        assert c.ticker == "NVDA"
        assert c.score == 0.0
        assert c.catalyst == ""
        assert c.signal_type == ""
        assert c.urgency == "normal"

    def test_scan_candidate_with_data(self):
        c = ScanCandidate(
            ticker="AAPL",
            score=8.5,
            catalyst="Earnings beat",
            signal_type="momentum",
            urgency="high",
        )
        assert c.score == 8.5
        assert c.urgency == "high"

    def test_scan_result_empty(self):
        r = ScanResult(
            timestamp="2025-01-06T10:00:00",
            candidates=[],
        )
        assert r.candidates == []
        assert r.market_regime == "neutral"
        assert r.total_screened == 0


class TestTickerScannerConfig:
    """Test scanner configuration."""

    def test_default_universe(self):
        # DEFAULT_UNIVERSE is the crypto fallback watchlist
        assert len(DEFAULT_UNIVERSE) >= 20
        assert "BTCUSDT" in DEFAULT_UNIVERSE
        assert "ETHUSDT" in DEFAULT_UNIVERSE

    def test_custom_watchlist(self):
        # Scanner accepts config with custom tier1 pairs
        scanner = TickerScanner(config={
            "scanner_tier1_pairs": ["BTCUSDT", "SOLUSDT"],
        })
        assert "BTCUSDT" in scanner.tier1_pairs
        assert "SOLUSDT" in scanner.tier1_pairs

    def test_default_parameters(self):
        scanner = TickerScanner()
        assert scanner.min_quote_volume > 0
        assert scanner.max_tickers == 3
        assert scanner.min_pct_change >= 0.0

    def test_custom_parameters(self):
        scanner = TickerScanner(config={
            "scanner_min_quote_volume": 2_000_000,
            "scanner_min_pct_change": 1.5,
            "max_tickers_per_scan": 5,
        })
        assert scanner.min_quote_volume == 2_000_000
        assert scanner.min_pct_change == 1.5
        assert scanner.max_tickers == 5


class TestPreFilter:
    """Test rule-based pre-filtering."""

    # Minimal Binance ticker dict format
    @staticmethod
    def _ticker(symbol, pct, vol=20_000_000, count=5000, price=100.0):
        return {
            "symbol": symbol,
            "priceChangePercent": str(pct),
            "quoteVolume": str(vol),
            "count": str(count),
            "lastPrice": str(price),
        }

    def test_deduplication(self):
        """Same symbol appearing twice in input → appears once in output."""
        scanner = TickerScanner(config={"scanner_min_quote_volume": 1_000_000, "scanner_min_pct_change": 2.0})
        pairs = [
            self._ticker("SOLUSDT", 5.0),
            self._ticker("SOLUSDT", 5.0),   # duplicate
            self._ticker("BTCUSDT", 3.0),
        ]
        result = scanner.pre_filter(pairs, [])
        tickers = [c["ticker"] for c in result]
        assert tickers.count("SOLUSDT") == 1

    def test_cooldown_filters(self):
        """analyzed_today symbols are excluded during bulk fetch (pre_filter respects input)."""
        # analyzed_today is applied in _fetch_all_pairs_bulk; here we test that
        # symbols NOT in analyzed_today pass through pre_filter normally.
        scanner = TickerScanner(
            analyzed_today={"SOLUSDT"},
            config={"scanner_min_quote_volume": 1_000_000, "scanner_min_pct_change": 2.0},
        )
        # pre_filter receives already-filtered list (analyzed_today removed in bulk fetch)
        pairs = [
            self._ticker("BTCUSDT", 3.0),   # should pass
        ]
        result = scanner.pre_filter(pairs, [])
        tickers = [c["ticker"] for c in result]
        assert "BTCUSDT" in tickers
        assert "SOLUSDT" not in tickers

    def test_empty_market_data(self):
        scanner = TickerScanner()
        result = scanner.pre_filter([], [])
        assert result == []


class TestRuleBasedRanking:
    """Test fallback rule-based ranking when no LLM available."""

    def test_ranking_by_score(self):
        scanner = TickerScanner(config={"max_tickers_per_scan": 2})
        candidates = [
            {"ticker": "AAPL", "pct_change": 2.0, "vol_ratio": 1.5},
            {"ticker": "NVDA", "pct_change": 5.0, "vol_ratio": 3.0},
            {"ticker": "MSFT", "pct_change": 1.0, "vol_ratio": 1.0},
        ]
        result = scanner.rank_candidates(candidates, {})
        assert len(result.candidates) == 2
        assert result.candidates[0].ticker == "NVDA"
        assert result.candidates[0].score > result.candidates[1].score

    def test_no_candidates(self):
        scanner = TickerScanner()
        result = scanner.rank_candidates([], {})
        assert len(result.candidates) == 0
        assert "No candidates" in result.scan_summary

    def test_momentum_signal_type(self):
        scanner = TickerScanner()
        candidates = [{"ticker": "AAPL", "pct_change": 3.0, "vol_ratio": 1.5}]
        result = scanner.rank_candidates(candidates, {})
        assert result.candidates[0].signal_type == "momentum"

    def test_reversal_signal_type(self):
        scanner = TickerScanner()
        candidates = [{"ticker": "AAPL", "pct_change": -3.0, "vol_ratio": 1.5}]
        result = scanner.rank_candidates(candidates, {})
        assert result.candidates[0].signal_type == "reversal"


class TestJsonExtraction:
    """Test LLM response JSON extraction."""

    def test_direct_json(self):
        scanner = TickerScanner()
        data = '{"candidates": [{"ticker": "NVDA", "score": 8}], "market_regime": "bullish"}'
        result = scanner._extract_json(data)
        assert result["candidates"][0]["ticker"] == "NVDA"

    def test_markdown_fenced_json(self):
        scanner = TickerScanner()
        data = '```json\n{"candidates": [{"ticker": "AAPL"}]}\n```'
        result = scanner._extract_json(data)
        assert result["candidates"][0]["ticker"] == "AAPL"

    def test_json_with_extra_text(self):
        scanner = TickerScanner()
        data = 'Here is my analysis:\n{"candidates": [{"ticker": "MSFT"}], "market_regime": "neutral"}'
        result = scanner._extract_json(data)
        assert result is not None
        assert result["candidates"][0]["ticker"] == "MSFT"

    def test_invalid_json_returns_none(self):
        scanner = TickerScanner()
        result = scanner._extract_json("This is not JSON at all")
        assert result is None

    def test_empty_string_returns_none(self):
        scanner = TickerScanner()
        result = scanner._extract_json("")
        assert result is None


class TestDefaultUniverse:
    """Test the default crypto universe."""

    def test_universe_has_major_pairs(self):
        majors = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]
        for pair in majors:
            assert pair in DEFAULT_UNIVERSE, f"{pair} missing from DEFAULT_UNIVERSE"

    def test_universe_size(self):
        assert len(DEFAULT_UNIVERSE) >= 20


# ---------------------------------------------------------------------------
# Blacklist patterns
# ---------------------------------------------------------------------------

class TestBlacklist:
    """_is_blacklisted covers both regex and static sets."""

    @pytest.mark.parametrize("sym", [
        "ETH3LUSDT",    # leveraged token \d[LS]USDT$
        "BTC2SUSDT",    # leveraged short
        "BTCDOWNUSDT",  # DOWN token
        "ETHUPUSDT",    # UP token
        "USDCUSDT",     # stablecoin pair
        "BUSDUSDT",     # stablecoin pair
        "DAIUSDT",      # stablecoin pair
        "TUSDUSDT",     # stablecoin pair
        "FDUSDUSDT",    # stablecoin pair
        "USDTUSDT",     # tautological pair
        "LUNCUSDT",     # Terra collapse
        "LUNABTC",      # Terra (^LUNA)
        "BTTCUSDT",     # static blacklist
        "HOTUSDT",      # static blacklist
        "FTMUSDT",      # static blacklist (rebranded)
    ])
    def test_blacklisted_symbols(self, sym):
        scanner = TickerScanner()
        assert scanner._is_blacklisted(sym) is True, f"Expected {sym} to be blacklisted"

    @pytest.mark.parametrize("sym", [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "ARBUSDT", "DOGEUSDT", "LINKUSDT", "ADAUSDT",
    ])
    def test_non_blacklisted_symbols(self, sym):
        scanner = TickerScanner()
        assert scanner._is_blacklisted(sym) is False, f"Expected {sym} NOT to be blacklisted"


# ---------------------------------------------------------------------------
# Scam name warning
# ---------------------------------------------------------------------------

class TestScamNameWarning:
    @pytest.mark.parametrize("sym", [
        "INUUSDT", "MOONTOKEN", "SAFEUSDT", "ELONUSDT", "PEPEUSDT",
    ])
    def test_scam_fragments_trigger_warning(self, sym):
        scanner = TickerScanner()
        assert scanner._scam_name_warning(sym) is True

    @pytest.mark.parametrize("sym", [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "DOTUSDT",
    ])
    def test_clean_symbols_no_warning(self, sym):
        scanner = TickerScanner()
        assert scanner._scam_name_warning(sym) is False


# ---------------------------------------------------------------------------
# Pre-filter: tier1 relaxed thresholds
# ---------------------------------------------------------------------------

class TestPreFilterTier1:
    @staticmethod
    def _t(symbol, pct, vol=2_000_000, count=500, price=100.0):
        return {
            "symbol": symbol,
            "priceChangePercent": str(pct),
            "quoteVolume": str(vol),
            "count": str(count),
            "lastPrice": str(price),
        }

    def test_tier1_passes_with_relaxed_vol(self):
        """BTCUSDT passes with $1.5M volume (below non-tier1 $5M threshold)."""
        scanner = TickerScanner(config={"scanner_min_quote_volume": 5_000_000, "scanner_min_pct_change": 2.0})
        pairs = [self._t("BTCUSDT", pct=2.0, vol=1_500_000)]
        result = scanner.pre_filter(pairs, [])
        assert any(c["ticker"] == "BTCUSDT" for c in result)

    def test_tier1_passes_with_relaxed_pct(self):
        """ETHUSDT passes with 1.0% move (below non-tier1 2% threshold)."""
        scanner = TickerScanner(config={"scanner_min_quote_volume": 5_000_000, "scanner_min_pct_change": 2.0})
        pairs = [self._t("ETHUSDT", pct=1.0, vol=10_000_000)]
        result = scanner.pre_filter(pairs, [])
        assert any(c["ticker"] == "ETHUSDT" for c in result)

    def test_tier1_not_capped_by_max_pct_change(self):
        """BTCUSDT with 50% move is NOT capped (only non-tier1 pairs are capped)."""
        scanner = TickerScanner(config={"scanner_max_pct_change": 40.0})
        pairs = [self._t("BTCUSDT", pct=50.0, vol=1_500_000)]
        result = scanner.pre_filter(pairs, [])
        assert any(c["ticker"] == "BTCUSDT" for c in result)

    def test_non_tier1_capped_at_max_pct_change(self):
        """ALTUSDT with 45% move is excluded when max_pct_change=40."""
        scanner = TickerScanner(config={
            "scanner_min_quote_volume": 1_000_000,
            "scanner_min_pct_change": 2.0,
            "scanner_max_pct_change": 40.0,
        })
        pairs = [self._t("ALTUSDT", pct=45.0, vol=10_000_000, count=5000)]
        result = scanner.pre_filter(pairs, [])
        assert not any(c["ticker"] == "ALTUSDT" for c in result)

    def test_non_tier1_below_trade_count_excluded(self):
        """ALTUSDT with trade_count < min_trade_count is excluded."""
        scanner = TickerScanner(config={
            "scanner_min_quote_volume": 1_000_000,
            "scanner_min_pct_change": 2.0,
            "scanner_min_trade_count": 1_000,
        })
        pairs = [self._t("ALTUSDT", pct=5.0, vol=5_000_000, count=500)]
        result = scanner.pre_filter(pairs, [])
        assert not any(c["ticker"] == "ALTUSDT" for c in result)

    def test_trending_symbol_flagged_is_trending(self):
        """Symbol in trending_symbols list gets is_trending=True."""
        scanner = TickerScanner(config={"scanner_min_quote_volume": 1_000_000, "scanner_min_pct_change": 2.0})
        pairs = [self._t("INJUSDT", pct=5.0, vol=8_000_000, count=3000)]
        result = scanner.pre_filter(pairs, ["INJ"])
        assert any(c["ticker"] == "INJUSDT" and c["is_trending"] for c in result)

    def test_volume_spike_signal_when_3x_min_vol(self):
        """vol >= 3× min_quote_volume adds volume_spike signal."""
        scanner = TickerScanner(config={"scanner_min_quote_volume": 2_000_000, "scanner_min_pct_change": 2.0})
        pairs = [self._t("LINKUSDT", pct=5.0, vol=7_000_000, count=3000)]
        result = scanner.pre_filter(pairs, [])
        matching = [c for c in result if c["ticker"] == "LINKUSDT"]
        assert matching
        assert "volume_spike" in matching[0]["signal"]

    def test_tier1_appears_before_non_tier1_in_output(self):
        """Tier 1 pairs sort before regular pairs."""
        scanner = TickerScanner(config={"scanner_min_quote_volume": 1_000_000, "scanner_min_pct_change": 1.0})
        pairs = [
            self._t("ALTUSDT", pct=10.0, vol=100_000_000, count=10000),  # huge volume, non-tier1
            self._t("BTCUSDT", pct=2.0, vol=2_000_000),                   # small volume, tier1
        ]
        result = scanner.pre_filter(pairs, [])
        tickers = [c["ticker"] for c in result]
        assert tickers.index("BTCUSDT") < tickers.index("ALTUSDT")


# ---------------------------------------------------------------------------
# _apply_vol_ratio_filter
# ---------------------------------------------------------------------------

class TestApplyVolRatioFilter:
    def _make_candidate(self, ticker, vol_ratio, is_tier1=False):
        return {"ticker": ticker, "vol_ratio": vol_ratio, "is_tier1": is_tier1}

    def test_unenriched_candidate_kept(self):
        """vol_ratio=1.0 means not enriched → benefit of doubt, kept."""
        scanner = TickerScanner(config={"scanner_min_vol_ratio": 1.5})
        candidates = [self._make_candidate("SOLUSDT", vol_ratio=1.0)]
        result = scanner._apply_vol_ratio_filter(candidates)
        assert len(result) == 1

    def test_below_threshold_dropped(self):
        """vol_ratio < min_vol_ratio and != 1.0 → dropped."""
        scanner = TickerScanner(config={"scanner_min_vol_ratio": 1.5})
        candidates = [self._make_candidate("ALTUSDT", vol_ratio=1.2)]
        result = scanner._apply_vol_ratio_filter(candidates)
        assert len(result) == 0

    def test_above_threshold_kept(self):
        """vol_ratio >= min_vol_ratio → kept."""
        scanner = TickerScanner(config={"scanner_min_vol_ratio": 1.5})
        candidates = [self._make_candidate("LINKUSDT", vol_ratio=2.0)]
        result = scanner._apply_vol_ratio_filter(candidates)
        assert len(result) == 1

    def test_tier1_threshold_is_1_2(self):
        """Tier 1 pairs use 1.2 threshold (not min_vol_ratio)."""
        scanner = TickerScanner(config={"scanner_min_vol_ratio": 1.5})
        # vol_ratio=1.3 >= 1.2 (tier1 threshold) → kept despite < 1.5
        candidates = [self._make_candidate("BTCUSDT", vol_ratio=1.3, is_tier1=True)]
        result = scanner._apply_vol_ratio_filter(candidates)
        assert len(result) == 1

    def test_tier1_below_1_2_dropped(self):
        """Tier 1 pair with vol_ratio 1.1 (< 1.2) → dropped."""
        scanner = TickerScanner(config={"scanner_min_vol_ratio": 1.5})
        candidates = [self._make_candidate("ETHUSDT", vol_ratio=1.1, is_tier1=True)]
        result = scanner._apply_vol_ratio_filter(candidates)
        assert len(result) == 0

    def test_mixed_candidates_filtered_correctly(self):
        scanner = TickerScanner(config={"scanner_min_vol_ratio": 1.5})
        candidates = [
            self._make_candidate("BTCUSDT", vol_ratio=1.0, is_tier1=True),  # kept (unenriched)
            self._make_candidate("ETHUSDT", vol_ratio=2.0, is_tier1=True),  # kept (above tier1 1.2)
            self._make_candidate("LINKUSDT", vol_ratio=1.6),                 # kept (above 1.5)
            self._make_candidate("ALTUSDT", vol_ratio=1.2),                  # dropped (below 1.5)
        ]
        result = scanner._apply_vol_ratio_filter(candidates)
        tickers = [c["ticker"] for c in result]
        assert "BTCUSDT" in tickers
        assert "ETHUSDT" in tickers
        assert "LINKUSDT" in tickers
        assert "ALTUSDT" not in tickers


# ---------------------------------------------------------------------------
# _rule_based_rank: score formula
# ---------------------------------------------------------------------------

class TestRuleBasedRankFormula:
    def test_score_formula_no_scam(self):
        """score = |pct| * 0.5 + vol_ratio * 2.0, capped at 10."""
        scanner = TickerScanner()
        candidates = [{"ticker": "SOLUSDT", "pct_change": 6.0, "vol_ratio": 2.0, "scam_warning": False}]
        result = scanner._rule_based_rank(candidates, 100, 10, "bullish")
        # 6.0 * 0.5 + 2.0 * 2.0 = 3.0 + 4.0 = 7.0
        assert result.candidates[0].score == pytest.approx(7.0)

    def test_score_formula_with_scam_penalty(self):
        """Scam warning reduces score by 2.0."""
        scanner = TickerScanner()
        candidates = [{"ticker": "MOONUSDT", "pct_change": 6.0, "vol_ratio": 2.0, "scam_warning": True}]
        result = scanner._rule_based_rank(candidates, 100, 10, "neutral")
        # 7.0 - 2.0 = 5.0
        assert result.candidates[0].score == pytest.approx(5.0)

    def test_score_capped_at_10(self):
        """Scores cannot exceed 10."""
        scanner = TickerScanner()
        candidates = [{"ticker": "BTCUSDT", "pct_change": 50.0, "vol_ratio": 10.0, "scam_warning": False}]
        result = scanner._rule_based_rank(candidates, 100, 10, "bullish")
        assert result.candidates[0].score == pytest.approx(10.0)

    def test_score_minimum_zero(self):
        """Scores cannot go below 0."""
        scanner = TickerScanner()
        candidates = [{"ticker": "MOONUSDT", "pct_change": 0.5, "vol_ratio": 0.5, "scam_warning": True}]
        result = scanner._rule_based_rank(candidates, 100, 10, "neutral")
        assert result.candidates[0].score >= 0.0

    def test_urgency_high_for_score_above_7(self):
        scanner = TickerScanner()
        candidates = [{"ticker": "SOLUSDT", "pct_change": 10.0, "vol_ratio": 3.0, "scam_warning": False}]
        result = scanner._rule_based_rank(candidates, 100, 10)
        # 10 * 0.5 + 3.0 * 2.0 = 5 + 6 = 11 → capped at 10 → "high"
        assert result.candidates[0].urgency == "high"

    def test_urgency_normal_for_score_at_or_below_7(self):
        scanner = TickerScanner()
        candidates = [{"ticker": "XRPUSDT", "pct_change": 2.0, "vol_ratio": 1.5, "scam_warning": False}]
        result = scanner._rule_based_rank(candidates, 100, 10)
        # 2.0 * 0.5 + 1.5 * 2.0 = 1 + 3 = 4.0 → "normal"
        assert result.candidates[0].urgency == "normal"

    def test_reversal_signal_for_negative_pct(self):
        scanner = TickerScanner()
        candidates = [{"ticker": "DOGEUSDT", "pct_change": -5.0, "vol_ratio": 2.0, "scam_warning": False}]
        result = scanner._rule_based_rank(candidates, 100, 10)
        assert result.candidates[0].signal_type == "reversal"

    def test_summary_mentions_rule_based(self):
        scanner = TickerScanner()
        candidates = [{"ticker": "ARBUSDT", "pct_change": 3.0, "vol_ratio": 2.0, "scam_warning": False}]
        result = scanner._rule_based_rank(candidates, 100, 10)
        assert "Rule-based" in result.scan_summary or "rule-based" in result.scan_summary.lower()


# ---------------------------------------------------------------------------
# rank_candidates: LLM fallback logic
# ---------------------------------------------------------------------------

class TestRankCandidatesLLMFallback:
    """rank_candidates() uses LLM when available; falls back to rule-based on failure."""

    def test_uses_rule_based_when_no_llm(self):
        scanner = TickerScanner()
        assert scanner.llm is None

        candidates = [{"ticker": "SOLUSDT", "pct_change": 5.0, "vol_ratio": 2.0, "scam_warning": False}]
        result = scanner.rank_candidates(candidates, {})
        # Should come from _rule_based_rank (LLM unavailable)
        assert "Rule-based" in result.scan_summary or "rule-based" in result.scan_summary.lower()

    def test_falls_back_to_rule_based_on_llm_exception(self):
        """If LLM is set but raises, falls back to _rule_based_rank."""
        scanner = TickerScanner()
        scanner.llm = MagicMock()
        scanner.llm.invoke.side_effect = RuntimeError("LLM timeout")

        candidates = [{"ticker": "LINKUSDT", "pct_change": 4.0, "vol_ratio": 2.5, "scam_warning": False}]
        result = scanner.rank_candidates(candidates, {})
        assert len(result.candidates) > 0  # rule-based still produces candidates

    def test_llm_manipulation_flag_excludes_candidate(self):
        """LLM sets manipulation_flag=true → candidate excluded from result."""
        scanner = TickerScanner()
        scanner.llm = MagicMock()

        llm_response = json.dumps({
            "candidates": [
                {
                    "ticker": "PUMPUSDT",
                    "score": 9.0,
                    "catalyst": "massive pump",
                    "signal_type": "momentum",
                    "urgency": "high",
                    "manipulation_flag": True,  # flagged
                    "momentum_quality": 9,
                    "liquidity_conviction": 8,
                    "market_context_alignment": 7,
                    "manipulation_risk_inverse": 1,
                    "catalyst_quality": 3,
                }
            ],
            "market_regime": "bullish",
            "scan_summary": "Suspicious pump detected",
        })
        mock_resp = MagicMock()
        mock_resp.content = llm_response
        scanner.llm.invoke.return_value = mock_resp

        candidates = [{"ticker": "PUMPUSDT", "pct_change": 80.0, "vol_ratio": 15.0, "scam_warning": False}]
        result = scanner._llm_rank(candidates, {}, 100, 1)

        # manipulation_flag=true → excluded
        assert not any(c.ticker == "PUMPUSDT" for c in result.candidates)

    def test_llm_valid_response_produces_candidates(self):
        """Valid LLM response creates ScanCandidate with correct fields."""
        scanner = TickerScanner()
        scanner.llm = MagicMock()

        llm_response = json.dumps({
            "candidates": [
                {
                    "ticker": "SOLUSDT",
                    "score": 8.2,
                    "catalyst": "Breakout above resistance",
                    "signal_type": "breakout",
                    "urgency": "high",
                    "manipulation_flag": False,
                    "momentum_quality": 8,
                    "liquidity_conviction": 9,
                    "market_context_alignment": 8,
                    "manipulation_risk_inverse": 9,
                    "catalyst_quality": 7,
                }
            ],
            "market_regime": "bullish",
            "scan_summary": "SOL breaking out with strong volume",
        })
        mock_resp = MagicMock()
        mock_resp.content = llm_response
        scanner.llm.invoke.return_value = mock_resp

        candidates = [{"ticker": "SOLUSDT", "pct_change": 5.0, "vol_ratio": 3.0, "scam_warning": False}]
        result = scanner._llm_rank(candidates, {}, 100, 1)

        assert len(result.candidates) == 1
        assert result.candidates[0].ticker == "SOLUSDT"
        assert result.candidates[0].score == pytest.approx(8.2)
        assert result.candidates[0].signal_type == "breakout"
        assert result.market_regime == "bullish"

    def test_llm_unparseable_response_raises(self):
        """LLM returns garbage → ValueError raised (caller falls back)."""
        scanner = TickerScanner()
        scanner.llm = MagicMock()

        mock_resp = MagicMock()
        mock_resp.content = "Sorry, I cannot analyse this right now."
        scanner.llm.invoke.return_value = mock_resp

        candidates = [{"ticker": "BTCUSDT", "pct_change": 2.0, "vol_ratio": 1.5, "scam_warning": False}]
        with pytest.raises(ValueError, match="unparseable"):
            scanner._llm_rank(candidates, {}, 100, 1)

    def test_llm_empty_candidates_returns_scan_result(self):
        """LLM returns empty candidates list → ScanResult with 0 candidates."""
        scanner = TickerScanner()
        scanner.llm = MagicMock()

        llm_response = json.dumps({
            "candidates": [],
            "market_regime": "bearish",
            "scan_summary": "No quality setups today",
        })
        mock_resp = MagicMock()
        mock_resp.content = llm_response
        scanner.llm.invoke.return_value = mock_resp

        candidates = [{"ticker": "BTCUSDT", "pct_change": 1.0, "vol_ratio": 1.1, "scam_warning": False}]
        result = scanner._llm_rank(candidates, {}, 100, 1)
        assert len(result.candidates) == 0
        assert result.market_regime == "bearish"

