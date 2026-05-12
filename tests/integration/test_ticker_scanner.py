"""Tests for the Ticker Scanner Agent.

Tests cover:
- ScanCandidate / ScanResult data classes
- Pre-filtering logic (dedup, cooldown, price/volume)
- Rule-based ranking fallback
- LLM JSON extraction
- Scanner configuration
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from tradingagents.integrations.ticker_scanner import (
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
        scanner = TickerScanner()
        assert len(scanner.universe) > 30
        assert "NVDA" in scanner.universe
        assert "AAPL" in scanner.universe

    def test_custom_watchlist(self):
        scanner = TickerScanner(config={
            "scanner_universe": "custom",
            "scanner_custom_watchlist": ["AAPL", "MSFT"],
        })
        assert scanner.universe == ["AAPL", "MSFT"]

    def test_default_parameters(self):
        scanner = TickerScanner()
        assert scanner.min_price == 5.0
        assert scanner.min_volume == 500_000
        assert scanner.max_tickers == 3

    def test_custom_parameters(self):
        scanner = TickerScanner(config={
            "scanner_min_price": 10.0,
            "scanner_min_volume": 1_000_000,
            "max_tickers_per_scan": 5,
        })
        assert scanner.min_price == 10.0
        assert scanner.min_volume == 1_000_000
        assert scanner.max_tickers == 5


class TestPreFilter:
    """Test rule-based pre-filtering."""

    def test_deduplication(self):
        scanner = TickerScanner()
        market_data = {
            "gainers": [
                {"ticker": "NVDA", "price": 120, "pct_change": 5.0, "vol_ratio": 2.0},
            ],
            "losers": [],
            "volume_spikes": [
                {"ticker": "NVDA", "price": 120, "pct_change": 5.0, "vol_ratio": 2.0},
            ],
        }
        result = scanner.pre_filter(market_data)
        tickers = [c["ticker"] for c in result]
        assert tickers.count("NVDA") == 1

    def test_cooldown_filters(self):
        scanner = TickerScanner(analyzed_today={"NVDA"})
        market_data = {
            "gainers": [
                {"ticker": "NVDA", "price": 120, "pct_change": 5.0, "vol_ratio": 2.0},
                {"ticker": "AAPL", "price": 180, "pct_change": 3.0, "vol_ratio": 1.5},
            ],
            "losers": [],
            "volume_spikes": [],
        }
        result = scanner.pre_filter(market_data)
        tickers = [c["ticker"] for c in result]
        assert "NVDA" not in tickers
        assert "AAPL" in tickers

    def test_empty_market_data(self):
        scanner = TickerScanner()
        result = scanner.pre_filter({"gainers": [], "losers": [], "volume_spikes": []})
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
    """Test the default stock universe."""

    def test_universe_has_major_stocks(self):
        majors = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
        for stock in majors:
            assert stock in DEFAULT_UNIVERSE, f"{stock} missing from universe"

    def test_universe_size(self):
        assert len(DEFAULT_UNIVERSE) >= 40
