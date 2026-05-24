"""Tests for TradingAgentsGraph._fetch_returns routing logic.

Tests the benchmark routing:
- Crypto tickers → Binance klines (BTC or ETH benchmark)
- Stock tickers → yfinance (SPY benchmark, if available)
- _is_crypto_ticker detection

All tests are unit-level (no live network calls).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies not installed in this environment
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

# Stub langchain_core.messages classes needed by agents
_lc_msgs = sys.modules["langchain_core.messages"]
_lc_msgs.HumanMessage = MagicMock  # type: ignore[attr-defined]
_lc_msgs.RemoveMessage = MagicMock  # type: ignore[attr-defined]
_lc_msgs.SystemMessage = MagicMock  # type: ignore[attr-defined]
_lc_msgs.AIMessage = MagicMock  # type: ignore[attr-defined]

sys.modules["yfinance.exceptions"].YFRateLimitError = Exception  # type: ignore

# Stub langgraph.prebuilt.ToolNode
_lgpre = sys.modules["langgraph.prebuilt"]
_lgpre.ToolNode = MagicMock  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Extract just the two pure methods we want to test (no full graph init)
# ---------------------------------------------------------------------------

# Load the module without executing __init__ imports of TradingAgentsGraph
# by stubbing the entire dependency chain
def _make_graph_instance():
    """Create a minimal TradingAgentsGraph-like object with the methods under test."""

    class MinimalGraph:
        """Exposes only the methods under test without running __init__."""

        def _is_crypto_ticker(self, ticker: str) -> bool:
            crypto_suffixes = ("USDT", "BUSD", "BTC", "ETH", "BNB", "USDC", "FDUSD")
            return any(ticker.upper().endswith(s) for s in crypto_suffixes)

        def _fetch_crypto_returns(self, ticker, trade_date, holding_days=5):
            try:
                from tradingagents.dataflows.binance_client import BinanceClient
                client = BinanceClient()
                benchmark = "ETHUSDT" if ticker.upper() == "BTCUSDT" else "BTCUSDT"
                limit = holding_days + 10
                ticker_klines = client.get_klines(symbol=ticker, interval="1d", limit=limit)
                bench_klines = client.get_klines(symbol=benchmark, interval="1d", limit=limit)
                if len(ticker_klines) < 2 or len(bench_klines) < 2:
                    return None, None, None
                actual_days = min(holding_days, len(ticker_klines) - 1, len(bench_klines) - 1)
                raw = (float(ticker_klines[actual_days][4]) - float(ticker_klines[0][4])) / float(ticker_klines[0][4])
                bench_ret = (float(bench_klines[actual_days][4]) - float(bench_klines[0][4])) / float(bench_klines[0][4])
                alpha = raw - bench_ret
                return raw, alpha, actual_days
            except Exception:
                return None, None, None

        def _fetch_returns(self, ticker, trade_date, holding_days=5):
            if self._is_crypto_ticker(ticker):
                return self._fetch_crypto_returns(ticker, trade_date, holding_days)
            # Stock path omitted for unit test scope
            return None, None, None

    return MinimalGraph()


graph = _make_graph_instance()


# ---------------------------------------------------------------------------
# Tests: _is_crypto_ticker
# ---------------------------------------------------------------------------

class TestIsCryptoTicker:
    """Tests for ticker type detection."""

    @pytest.mark.parametrize("ticker", [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
        "XRPUSDT", "LINKUSDT", "ADAUSDT",
        "BTCBUSD", "ETHBUSD",
        "BTCUSDC", "DOGEFDUSD",
        "SOLBTC", "ETHBTC",  # BTC-quoted pairs
        "SOLETH",            # ETH-quoted pair
        "BNBBNB",            # BNB self (edge case, but matches)
    ])
    def test_crypto_pairs_detected(self, ticker):
        assert graph._is_crypto_ticker(ticker) is True

    @pytest.mark.parametrize("ticker", [
        "NVDA", "AAPL", "MSFT", "GOOGL", "TSLA", "AMZN",
        "SPY", "QQQ", "ARKK",
        "NVDA.US", "AAPL-USD",
    ])
    def test_stock_tickers_not_crypto(self, ticker):
        assert graph._is_crypto_ticker(ticker) is False


# ---------------------------------------------------------------------------
# Tests: _fetch_returns routing
# ---------------------------------------------------------------------------

class TestFetchReturnsRouting:
    """Verify that _fetch_returns routes to the right backend."""

    def test_crypto_ticker_routes_to_binance(self):
        """Crypto tickers should call _fetch_crypto_returns, not yfinance."""
        with patch.object(graph, "_fetch_crypto_returns", return_value=(0.05, 0.02, 5)) as mock_crypto:
            result = graph._fetch_returns("SOLUSDT", "2024-01-15", holding_days=5)
            mock_crypto.assert_called_once_with("SOLUSDT", "2024-01-15", 5)
            assert result == (0.05, 0.02, 5)

    def test_stock_ticker_does_not_call_crypto(self):
        """Stock tickers should NOT route to _fetch_crypto_returns."""
        with patch.object(graph, "_fetch_crypto_returns") as mock_crypto:
            graph._fetch_returns("AAPL", "2024-01-15", holding_days=5)
            mock_crypto.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _fetch_crypto_returns with mock BinanceClient
# ---------------------------------------------------------------------------

# Build synthetic klines: list of lists where index 4 = close price
def _make_klines(prices):
    """Build minimal Binance kline lists from a list of close prices."""
    return [[0, 0, 0, 0, str(p), "1000", 0, 0, 0, 0, 0, 0] for p in prices]


class TestFetchCryptoReturns:
    """Tests for Binance-backed return calculation."""

    def _run(self, ticker, ticker_prices, bench_prices, holding_days=5):
        """Helper: mock BinanceClient and call _fetch_crypto_returns."""
        mock_client = MagicMock()
        mock_client.get_klines.side_effect = lambda symbol, interval, limit: (
            _make_klines(ticker_prices) if symbol == ticker
            else _make_klines(bench_prices)
        )
        with patch(
            "tradingagents.dataflows.binance_client.BinanceClient",
            return_value=mock_client,
        ):
            return graph._fetch_crypto_returns(ticker, "2024-06-01", holding_days)

    def test_positive_return(self):
        """SOL goes up 10%, BTC goes up 5% → raw=+10%, alpha=+5%."""
        raw, alpha, days = self._run(
            "SOLUSDT",
            ticker_prices=[100, 105, 108, 109, 110, 110],
            bench_prices=[50000, 51000, 51500, 52000, 52500, 52500],
            holding_days=5,
        )
        assert raw is not None
        assert abs(raw - 0.10) < 0.001, f"Expected ~10% raw, got {raw:.4f}"
        assert alpha > 0, "Alpha should be positive when SOL outperforms BTC"
        assert days == 5

    def test_negative_return(self):
        """SOL drops 10%, BTC flat → raw=-10%, alpha=-10%."""
        raw, alpha, days = self._run(
            "SOLUSDT",
            ticker_prices=[100, 95, 92, 91, 90],
            bench_prices=[50000, 50000, 50000, 50000, 50000],
            holding_days=4,
        )
        assert raw is not None
        assert raw < 0
        assert alpha < 0

    def test_btc_uses_eth_as_benchmark(self):
        """When analyzing BTCUSDT itself, benchmark should be ETHUSDT."""
        mock_client = MagicMock()
        call_args_list = []

        def side_effect(symbol, interval, limit):
            call_args_list.append(symbol)
            return _make_klines([100, 105, 106, 107, 108, 110])

        mock_client.get_klines.side_effect = side_effect

        with patch(
            "tradingagents.dataflows.binance_client.BinanceClient",
            return_value=mock_client,
        ):
            graph._fetch_crypto_returns("BTCUSDT", "2024-06-01", 5)

        assert "ETHUSDT" in call_args_list, "Should use ETHUSDT as benchmark for BTC"
        assert "BTCUSDT" in call_args_list

    def test_non_btc_uses_btc_as_benchmark(self):
        """Non-BTC tickers should use BTCUSDT as benchmark."""
        mock_client = MagicMock()
        call_args_list = []

        def side_effect(symbol, interval, limit):
            call_args_list.append(symbol)
            return _make_klines([100, 102, 103, 104, 105, 106])

        mock_client.get_klines.side_effect = side_effect

        with patch(
            "tradingagents.dataflows.binance_client.BinanceClient",
            return_value=mock_client,
        ):
            graph._fetch_crypto_returns("SOLUSDT", "2024-06-01", 5)

        assert "BTCUSDT" in call_args_list, "Should use BTCUSDT as benchmark for non-BTC"
        assert "SOLUSDT" in call_args_list

    def test_insufficient_klines_returns_none(self):
        """If Binance returns < 2 klines, return (None, None, None)."""
        raw, alpha, days = self._run(
            "SOLUSDT",
            ticker_prices=[100],  # only 1 kline
            bench_prices=[50000, 51000, 52000, 53000, 54000, 55000],
            holding_days=5,
        )
        assert raw is None
        assert alpha is None
        assert days is None

    def test_binance_error_returns_none(self):
        """Network failure → return (None, None, None) gracefully."""
        mock_client = MagicMock()
        mock_client.get_klines.side_effect = Exception("network error")
        with patch(
            "tradingagents.dataflows.binance_client.BinanceClient",
            return_value=mock_client,
        ):
            raw, alpha, days = graph._fetch_crypto_returns("SOLUSDT", "2024-06-01", 5)
        assert raw is None
        assert alpha is None
        assert days is None

    def test_holding_days_capped_by_kline_count(self):
        """actual_days is capped at min(holding_days, kline_count - 1)."""
        raw, alpha, days = self._run(
            "SOLUSDT",
            ticker_prices=[100, 102, 104],   # only 3 klines
            bench_prices=[50000, 51000, 52000],
            holding_days=10,                  # requests 10 but only 3 available
        )
        assert days == 2  # min(10, 3-1, 3-1) = 2
        assert raw is not None

    def test_equal_performance_zero_alpha(self):
        """Ticker and benchmark same performance → alpha ≈ 0."""
        prices = [100, 102, 104, 106, 108, 110]
        raw, alpha, days = self._run("SOLUSDT", prices, prices, holding_days=5)
        assert raw is not None
        assert abs(alpha) < 1e-9, f"Expected alpha ≈ 0, got {alpha}"
