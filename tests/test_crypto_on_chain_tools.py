"""Tests for tradingagents/agents/utils/crypto_on_chain_tools.py.

Covers:
- _binance_symbol_to_coingecko: mapping known and unknown symbols
- get_crypto_fundamentals: correct output format, graceful failure handling
- get_crypto_market_sentiment: correct output format, funding rate logic, graceful failure
- _safe_urlopen: network error handling
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import types
import sys
from unittest.mock import MagicMock, patch, call
from io import BytesIO

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies (langchain not installed in test env)
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

_lc_msgs = sys.modules["langchain_core.messages"]
_lc_msgs.HumanMessage = MagicMock  # type: ignore[attr-defined]
_lc_msgs.RemoveMessage = MagicMock  # type: ignore[attr-defined]
sys.modules["yfinance.exceptions"].YFRateLimitError = Exception  # type: ignore

# Stub tradingagents.agents.utils.rating (needed by agent_utils chain)
_rating_stub = types.ModuleType("tradingagents.agents.utils.rating")
_rating_stub.parse_rating = lambda text, scale=10: 5.0  # type: ignore
sys.modules.setdefault("tradingagents.agents.utils.rating", _rating_stub)

# ---------------------------------------------------------------------------
# Import the module under test directly (bypasses agents/__init__ chain)
# ---------------------------------------------------------------------------

_root = pathlib.Path(__file__).parent.parent
_tools_path = _root / "tradingagents" / "agents" / "utils" / "crypto_on_chain_tools.py"
_spec = importlib.util.spec_from_file_location("tradingagents.agents.utils.crypto_on_chain_tools", _tools_path)
_tools_mod = importlib.util.module_from_spec(_spec)  # type: ignore
_spec.loader.exec_module(_tools_mod)  # type: ignore
sys.modules["tradingagents.agents.utils.crypto_on_chain_tools"] = _tools_mod

_binance_symbol_to_coingecko = _tools_mod._binance_symbol_to_coingecko
_safe_urlopen = _tools_mod._safe_urlopen
get_crypto_fundamentals = _tools_mod.get_crypto_fundamentals
get_crypto_market_sentiment = _tools_mod.get_crypto_market_sentiment
_SYMBOL_TO_COINGECKO = _tools_mod._SYMBOL_TO_COINGECKO



# ---------------------------------------------------------------------------
# Helpers for building fake HTTP responses
# ---------------------------------------------------------------------------

def _fake_response(data: dict | list) -> MagicMock:
    """Return a context-manager mock that reads JSON data."""
    body = json.dumps(data).encode()
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read = MagicMock(return_value=body)
    return mock


# ---------------------------------------------------------------------------
# Tests: _binance_symbol_to_coingecko
# ---------------------------------------------------------------------------

class TestBinanceSymbolToCoingecko:
    """Test symbol → CoinGecko ID mapping."""

    @pytest.mark.parametrize("ticker,expected_id", [
        ("BTCUSDT", "bitcoin"),
        ("ETHUSDT", "ethereum"),
        ("SOLUSDT", "solana"),
        ("BNBUSDT", "binancecoin"),
        ("XRPUSDT", "ripple"),
        ("LINKUSDT", "chainlink"),
        ("ADAUSDT", "cardano"),
        ("UNIUSDT", "uniswap"),
        ("DOTUSDT", "polkadot"),
        ("ARBUSDT", "arbitrum"),
    ])
    def test_known_mappings(self, ticker, expected_id):
        result = _binance_symbol_to_coingecko(ticker)
        assert result == expected_id, f"{ticker} should map to {expected_id}, got {result}"

    @pytest.mark.parametrize("ticker", [
        "UNKNOWNUSDT", "FAKEUSDT", "XXXXUSDT",
    ])
    def test_unknown_symbols_return_none(self, ticker):
        result = _binance_symbol_to_coingecko(ticker)
        assert result is None

    def test_all_known_symbols_in_mapping(self):
        """All symbols in _SYMBOL_TO_COINGECKO have non-empty ID values."""
        for symbol, cg_id in _SYMBOL_TO_COINGECKO.items():
            assert cg_id, f"CoinGecko ID for {symbol} should not be empty"
            assert isinstance(cg_id, str)


# ---------------------------------------------------------------------------
# Tests: _safe_urlopen
# ---------------------------------------------------------------------------

class TestSafeUrlopen:
    """Test the HTTP fetch utility."""

    def test_returns_parsed_json_on_success(self):
        payload = {"key": "value", "num": 42}
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            result = _safe_urlopen("https://example.com/api")
        assert result == payload

    def test_returns_none_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            result = _safe_urlopen("https://example.com/api")
        assert result is None

    def test_returns_none_on_invalid_json(self):
        mock = MagicMock()
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        mock.read = MagicMock(return_value=b"not valid json {{{")
        with patch("urllib.request.urlopen", return_value=mock):
            result = _safe_urlopen("https://example.com/api")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: get_crypto_fundamentals
# ---------------------------------------------------------------------------

_COINGECKO_COIN_RESPONSE = {
    "name": "Solana",
    "symbol": "sol",
    "market_cap_rank": 5,
    "description": {"en": "Solana is a high-performance blockchain." + "x" * 400},
    "market_data": {
        "current_price": {"usd": 150.0},
        "price_change_percentage_24h": 5.25,
        "price_change_percentage_7d": -2.1,
        "market_cap": {"usd": 65_000_000_000},
        "fully_diluted_valuation": {"usd": 80_000_000_000},
        "total_volume": {"usd": 2_500_000_000},
        "circulating_supply": 430_000_000,
        "total_supply": 575_000_000,
        "max_supply": None,
        "ath": {"usd": 260.0},
        "ath_change_percentage": {"usd": -42.3},
    },
    "community_data": {},
    "sentiment_votes_up_percentage": 82.5,
}


class TestGetCryptoFundamentals:
    """Tests for get_crypto_fundamentals."""

    def _call_with_mock(self, ticker, response=None):
        with patch.object(_tools_mod, "_safe_urlopen", return_value=response or _COINGECKO_COIN_RESPONSE):
            return get_crypto_fundamentals(ticker)

    def test_returns_string(self):
        result = self._call_with_mock("SOLUSDT")
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_ticker_name(self):
        result = self._call_with_mock("SOLUSDT")
        assert "Solana" in result

    def test_contains_price(self):
        result = self._call_with_mock("SOLUSDT")
        assert "150" in result

    def test_contains_market_cap(self):
        result = self._call_with_mock("SOLUSDT")
        # 65B should be formatted as $65.00B or similar
        assert "65" in result

    def test_contains_circulating_supply(self):
        result = self._call_with_mock("SOLUSDT")
        assert "430" in result

    def test_description_truncated_at_300(self):
        """Long descriptions should be truncated to ~300 chars."""
        result = self._call_with_mock("SOLUSDT")
        # The original description has 400+ chars of padding; truncated to 300+...
        assert "..." in result

    def test_unknown_ticker_without_fallback_returns_error(self):
        """Unknown ticker + no CoinGecko search result → error string."""
        with patch.object(_tools_mod, "_safe_urlopen", return_value=None):
            result = get_crypto_fundamentals("UNKNOWNUSDT")
        assert isinstance(result, str)
        assert "not found" in result.lower() or "unavailable" in result.lower() or "failed" in result.lower()

    def test_network_failure_returns_error_string(self):
        """Network failure → error string, not exception."""
        with patch.object(_tools_mod, "_safe_urlopen", return_value=None):
            result = get_crypto_fundamentals("BTCUSDT")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Tests: get_crypto_market_sentiment
# ---------------------------------------------------------------------------

_FNG_RESPONSE = {
    "data": [
        {"value": "72", "value_classification": "Greed"},
        {"value": "65", "value_classification": "Greed"},
        {"value": "58", "value_classification": "Neutral"},
        {"value": "61", "value_classification": "Greed"},
        {"value": "70", "value_classification": "Greed"},
        {"value": "75", "value_classification": "Extreme Greed"},
        {"value": "80", "value_classification": "Extreme Greed"},
    ]
}

_FUNDING_RESPONSE = [
    {"fundingRate": "0.001", "fundingTime": "1700000000"}
]

_PREMIUM_RESPONSE = {"lastFundingRate": "0.0008", "symbol": "BTCUSDT"}


class TestGetCryptoMarketSentiment:
    """Tests for get_crypto_market_sentiment."""

    def _call(self, ticker, fng=_FNG_RESPONSE, funding=_FUNDING_RESPONSE, premium=None, cg=None):
        def side_effect(url, *args, **kwargs):
            if "alternative.me" in url:
                return fng
            if "fundingRate" in url:
                return funding
            if "premiumIndex" in url:
                return premium
            if "coingecko.com" in url:
                return cg
            return None

        with patch.object(_tools_mod, "_safe_urlopen", side_effect=side_effect):
            return get_crypto_market_sentiment(ticker)

    def test_returns_string(self):
        result = self._call("BTCUSDT")
        assert isinstance(result, str)
        assert len(result) > 20

    def test_contains_fear_greed_value(self):
        result = self._call("BTCUSDT")
        assert "72" in result  # current F&G value
        assert "Greed" in result

    def test_contains_funding_rate(self):
        result = self._call("SOLUSDT")
        # Funding rate 0.001 * 100 = 0.10%
        assert "0.1" in result or "0.10" in result

    def test_bullish_bias_label_for_positive_rate(self):
        """Rate > 0.05% should be labeled as bullish bias."""
        result = self._call("SOLUSDT")  # funding=0.1%
        assert "bullish" in result.lower()

    def test_bearish_bias_label_for_negative_rate(self):
        """Negative funding rate should be labeled as bearish bias."""
        negative_funding = [{"fundingRate": "-0.0005"}]
        result = self._call("SOLUSDT", funding=negative_funding)
        assert "bearish" in result.lower()

    def test_neutral_funding_rate(self):
        """Near-zero funding rate → neutral label."""
        neutral_funding = [{"fundingRate": "0.00001"}]
        result = self._call("SOLUSDT", funding=neutral_funding)
        assert "neutral" in result.lower()

    def test_sentiment_trend_improving_when_current_higher(self):
        """When today > 7 days ago → trend is 'improving'."""
        fng = {"data": [
            {"value": "80", "value_classification": "Extreme Greed"},  # now
            {"value": "60", "value_classification": "Greed"},
            {"value": "55", "value_classification": "Neutral"},
            {"value": "50", "value_classification": "Neutral"},
            {"value": "45", "value_classification": "Fear"},
            {"value": "40", "value_classification": "Fear"},
            {"value": "50", "value_classification": "Neutral"},  # 7 days ago
        ]}
        result = self._call("BTCUSDT", fng=fng)
        assert "improving" in result.lower()

    def test_sentiment_trend_declining_when_current_lower(self):
        """When today < 7 days ago → trend is 'declining'."""
        fng = {"data": [
            {"value": "30", "value_classification": "Fear"},   # now
            {"value": "40", "value_classification": "Fear"},
            {"value": "45", "value_classification": "Fear"},
            {"value": "50", "value_classification": "Neutral"},
            {"value": "55", "value_classification": "Neutral"},
            {"value": "60", "value_classification": "Greed"},
            {"value": "70", "value_classification": "Greed"},  # 7 days ago
        ]}
        result = self._call("BTCUSDT", fng=fng)
        assert "declining" in result.lower()

    def test_fng_unavailable_still_returns_string(self):
        """F&G fetch failure → graceful fallback, still returns string."""
        result = self._call("BTCUSDT", fng=None)
        assert isinstance(result, str)
        assert "unavailable" in result.lower() or len(result) > 0

    def test_funding_unavailable_falls_back_to_premium(self):
        """Funding rate endpoint unavailable → try premiumIndex."""
        result = self._call("BTCUSDT", funding=None, premium=_PREMIUM_RESPONSE)
        assert isinstance(result, str)
        # Should contain funding rate from premium endpoint (0.0008 * 100 = 0.08%)
        assert "0.08" in result or "funding" in result.lower()

    def test_no_network_returns_non_empty_string(self):
        """Complete network failure → returns string with partial info."""
        with patch.object(_tools_mod, "_safe_urlopen", return_value=None):
            result = get_crypto_market_sentiment("SOLUSDT")
        assert isinstance(result, str)
        assert "SOLUSDT" in result
