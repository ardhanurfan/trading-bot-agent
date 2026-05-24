"""Unit tests for tradingagents/dataflows/binance_client.py and binance_spot_data.py."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Stub heavy dependencies unavailable in this test environment
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    mod.__package__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


_STUBS: dict = {
    "yfinance": _stub_module("yfinance"),
    "yfinance.exceptions": _stub_module("yfinance.exceptions", YFRateLimitError=Exception),
    "stockstats": _stub_module("stockstats", wrap=lambda df: df),
    "stockstats.wrap": _stub_module("stockstats.wrap"),
    "pandas_ta": _stub_module("pandas_ta"),
    "fredapi": _stub_module("fredapi"),
    "alpha_vantage": _stub_module("alpha_vantage"),
}

for _name, _mod in _STUBS.items():
    sys.modules.setdefault(_name, _mod)

# Stub yfinance Ticker
_yf_ticker = MagicMock()
sys.modules["yfinance"].Ticker = _yf_ticker  # type: ignore


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_kline(open_time_ms: int = 1_700_000_000_000) -> list:
    """Return a minimal Binance kline row (all 12 fields)."""
    close_time_ms = open_time_ms + 86_400_000 - 1  # 1-day candle
    return [
        open_time_ms,   # open time
        "42000.00",     # open
        "43000.00",     # high
        "41000.00",     # low
        "42500.00",     # close
        "1200.50",      # volume
        close_time_ms,  # close time
        "50400000.00",  # quote asset volume
        120,            # number of trades
        "600.25",       # taker buy base asset volume
        "25200000.00",  # taker buy quote asset volume
        "0",            # ignore
    ]


def _make_ticker_24hr(symbol: str = "BTCUSDT") -> dict:
    return {
        "symbol": symbol,
        "priceChangePercent": "3.50",
        "lastPrice": "42500.00",
        "quoteVolume": "500000000.00",
    }


# ---------------------------------------------------------------------------
# BinanceClient unit tests
# ---------------------------------------------------------------------------

class TestBinanceClientInit:
    def test_testnet_default(self):
        from tradingagents.dataflows.binance_client import BinanceClient, _TESTNET_BASE_URL

        with patch.dict("os.environ", {"BINANCE_TESTNET": "true"}):
            client = BinanceClient()
        assert client.base_url == _TESTNET_BASE_URL

    def test_live_when_env_false(self):
        from tradingagents.dataflows.binance_client import BinanceClient, _LIVE_BASE_URL

        with patch.dict("os.environ", {"BINANCE_TESTNET": "false"}):
            client = BinanceClient()
        assert client.base_url == _LIVE_BASE_URL


class TestBinanceClientGet:
    def test_get_klines_returns_list(self):
        """get_klines() should return a list of kline rows."""
        from tradingagents.dataflows.binance_client import BinanceClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [_make_kline(1_700_000_000_000)]

        with patch("requests.Session.get", return_value=mock_resp):
            client = BinanceClient()
            result = client.get_klines("BTCUSDT", "1d", 1)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0][4] == "42500.00"  # close price

    def test_get_klines_empty_response(self):
        """get_klines() should return an empty list when Binance returns []."""
        from tradingagents.dataflows.binance_client import BinanceClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        with patch("requests.Session.get", return_value=mock_resp):
            client = BinanceClient()
            result = client.get_klines("XYZUSDT", "1d", 100)

        assert result == []

    def test_api_error_raises_exception(self):
        """Non-2xx response should raise BinanceAPIError."""
        from tradingagents.dataflows.binance_client import BinanceClient, BinanceAPIError

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"code": -1100, "msg": "Illegal characters found"}

        with patch("requests.Session.get", return_value=mock_resp):
            client = BinanceClient()
            with pytest.raises(BinanceAPIError) as exc_info:
                client.get_klines("INVALID", "1d", 1)

        assert exc_info.value.code == -1100

    def test_get_ticker_24hr_returns_list(self):
        """get_ticker_24hr() with no symbol should return list of all tickers."""
        from tradingagents.dataflows.binance_client import BinanceClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            _make_ticker_24hr("BTCUSDT"),
            _make_ticker_24hr("ETHUSDT"),
        ]

        with patch("requests.Session.get", return_value=mock_resp):
            client = BinanceClient()
            result = client.get_ticker_24hr()

        assert len(result) == 2
        assert result[0]["symbol"] == "BTCUSDT"

    def test_get_top_volume_pairs_filters_correctly(self):
        """get_top_volume_pairs() should sort by quoteVolume and return top-N."""
        from tradingagents.dataflows.binance_client import BinanceClient

        tickers = [
            {"symbol": "BTCUSDT", "quoteVolume": "900000000", "priceChangePercent": "5.0", "lastPrice": "42000"},
            {"symbol": "ETHUSDT", "quoteVolume": "400000000", "priceChangePercent": "4.0", "lastPrice": "2500"},
            {"symbol": "SOLUSDT", "quoteVolume": "100000000", "priceChangePercent": "3.5", "lastPrice": "100"},
        ]

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = tickers

        with patch("requests.Session.get", return_value=mock_resp):
            client = BinanceClient()
            result = client.get_top_volume_pairs(quote_asset="USDT", limit=2)

        assert len(result) == 2
        # Highest volume pair should be first
        assert result[0]["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# HMAC signing tests
# ---------------------------------------------------------------------------

class TestBinanceClientSigning:
    def test_sign_produces_non_empty_hex(self):
        """_sign() should return a dict with a non-empty hex 'signature' key."""
        from tradingagents.dataflows.binance_client import BinanceClient

        with patch.dict("os.environ", {
            "BINANCE_API_KEY": "test_key",
            "BINANCE_SECRET_KEY": "test_secret",
        }):
            client = BinanceClient()
            params = {"symbol": "BTCUSDT", "side": "BUY"}
            signed = client._sign(params)

        assert "signature" in signed
        assert isinstance(signed["signature"], str)
        assert len(signed["signature"]) == 64  # SHA-256 hex digest is always 64 chars


# ---------------------------------------------------------------------------
# _klines_to_dataframe unit tests
# ---------------------------------------------------------------------------

class TestKlinesToDataframe:
    def test_empty_klines_returns_empty_df(self):
        from tradingagents.dataflows.binance_spot_data import _klines_to_dataframe

        df = _klines_to_dataframe([], "BTCUSDT")
        assert df.empty

    def test_columns_match_yfinance_schema(self):
        """Output columns must match the yfinance schema (Open, High, Low, Close, Volume)."""
        from tradingagents.dataflows.binance_spot_data import _klines_to_dataframe

        klines = [_make_kline(1_700_000_000_000), _make_kline(1_700_000_000_000 + 86_400_000)]
        df = _klines_to_dataframe(klines, "BTCUSDT")

        assert set(df.columns) == {"Open", "High", "Low", "Close", "Volume"}
        assert df.index.name == "Date"

    def test_values_are_floats(self):
        from tradingagents.dataflows.binance_spot_data import _klines_to_dataframe

        klines = [_make_kline()]
        df = _klines_to_dataframe(klines, "BTCUSDT")

        assert df["Close"].dtype == float
        assert df["Volume"].dtype == float
        assert df["Close"].iloc[0] == pytest.approx(42500.0)

    def test_sorted_ascending_by_date(self):
        """Rows should be sorted by date ascending (oldest first)."""
        from tradingagents.dataflows.binance_spot_data import _klines_to_dataframe

        t1 = 1_700_000_000_000
        t2 = t1 + 86_400_000
        klines = [_make_kline(t2), _make_kline(t1)]  # reversed order
        df = _klines_to_dataframe(klines, "BTCUSDT")

        assert df.index[0] < df.index[1]

    def test_timestamp_parsed_correctly(self):
        """Timestamp in millis should map to correct UTC date."""
        from tradingagents.dataflows.binance_spot_data import _klines_to_dataframe

        # 2024-01-15 00:00:00 UTC in ms
        ts_ms = int(pd.Timestamp("2024-01-15", tz="UTC").timestamp() * 1000)
        klines = [_make_kline(ts_ms)]
        df = _klines_to_dataframe(klines, "BTCUSDT")

        assert df.index[0].strftime("%Y-%m-%d") == "2024-01-15"


# ---------------------------------------------------------------------------
# _fetch_full_range pagination tests
# ---------------------------------------------------------------------------

class TestFetchFullRange:
    def test_single_page_no_pagination(self):
        """Should stop after one call when batch is smaller than limit."""
        from tradingagents.dataflows.binance_spot_data import _fetch_full_range

        kline_batch = [_make_kline(1_700_000_000_000)]

        mock_client = MagicMock()
        mock_client.get_klines.return_value = kline_batch

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            df = _fetch_full_range("BTCUSDT", "2023-11-01", "2023-11-15")

        assert mock_client.get_klines.call_count == 1
        assert len(df) == 1

    def test_empty_batch_stops_loop(self):
        """Empty klines response should stop pagination immediately."""
        from tradingagents.dataflows.binance_spot_data import _fetch_full_range

        mock_client = MagicMock()
        mock_client.get_klines.return_value = []

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            df = _fetch_full_range("BTCUSDT", "2023-11-01", "2023-11-15")

        assert df.empty
        assert mock_client.get_klines.call_count == 1


# ---------------------------------------------------------------------------
# _date_to_ms
# ---------------------------------------------------------------------------

class TestDateToMs:
    def test_known_date(self):
        """2024-01-15 00:00 UTC == 1705276800000 ms."""
        from tradingagents.dataflows.binance_spot_data import _date_to_ms

        ms = _date_to_ms("2024-01-15")
        # pd.Timestamp for the same date
        expected = int(pd.Timestamp("2024-01-15", tz="UTC").timestamp() * 1000)
        assert ms == expected

    def test_epoch(self):
        from tradingagents.dataflows.binance_spot_data import _date_to_ms

        ms = _date_to_ms("1970-01-01")
        assert ms == 0


# ---------------------------------------------------------------------------
# get_binance_ohlcv
# ---------------------------------------------------------------------------

class TestGetBinanceOHLCV:
    def _klines(self):
        return [
            _make_kline(int(pd.Timestamp("2024-01-10", tz="UTC").timestamp() * 1000)),
            _make_kline(int(pd.Timestamp("2024-01-11", tz="UTC").timestamp() * 1000)),
            _make_kline(int(pd.Timestamp("2024-01-12", tz="UTC").timestamp() * 1000)),
        ]

    def test_returns_string(self):
        from tradingagents.dataflows.binance_spot_data import get_binance_ohlcv

        mock_client = MagicMock()
        mock_client.get_klines.return_value = self._klines()

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            result = get_binance_ohlcv("BTCUSDT", "2024-01-10", "2024-01-12")

        assert isinstance(result, str)
        assert len(result) > 50

    def test_csv_header_present(self):
        from tradingagents.dataflows.binance_spot_data import get_binance_ohlcv

        mock_client = MagicMock()
        mock_client.get_klines.return_value = self._klines()

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            result = get_binance_ohlcv("BTCUSDT", "2024-01-10", "2024-01-12")

        assert "BTCUSDT" in result
        assert "2024-01-10" in result

    def test_csv_contains_ohlcv_columns(self):
        from tradingagents.dataflows.binance_spot_data import get_binance_ohlcv

        mock_client = MagicMock()
        mock_client.get_klines.return_value = self._klines()

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            result = get_binance_ohlcv("BTCUSDT", "2024-01-10", "2024-01-12")

        # CSV header row should contain these columns
        assert "Open" in result
        assert "High" in result
        assert "Low" in result
        assert "Close" in result
        assert "Volume" in result

    def test_no_data_returns_error_string(self):
        from tradingagents.dataflows.binance_spot_data import get_binance_ohlcv

        mock_client = MagicMock()
        mock_client.get_klines.return_value = []

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            result = get_binance_ohlcv("FAKEUSDT", "2024-01-10", "2024-01-12")

        assert "No data found" in result
        assert "FAKEUSDT" in result

    def test_binance_api_error_returns_error_string(self):
        from tradingagents.dataflows.binance_spot_data import get_binance_ohlcv
        from tradingagents.dataflows.binance_client import BinanceAPIError

        mock_client = MagicMock()
        mock_client.get_klines.side_effect = BinanceAPIError(400, -1121, "Invalid symbol")

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            result = get_binance_ohlcv("INVALID", "2024-01-10", "2024-01-12")

        assert "No data found" in result or "Invalid symbol" in result

    def test_symbol_uppercased_in_output(self):
        from tradingagents.dataflows.binance_spot_data import get_binance_ohlcv

        mock_client = MagicMock()
        mock_client.get_klines.return_value = self._klines()

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client):
            result = get_binance_ohlcv("btcusdt", "2024-01-10", "2024-01-12")

        assert "BTCUSDT" in result


# ---------------------------------------------------------------------------
# _load_ohlcv_binance — look-ahead bias prevention
# ---------------------------------------------------------------------------

class TestLoadOHLCVBinance:
    def test_look_ahead_bias_prevention(self, tmp_path):
        """Rows after curr_date must be dropped from the DataFrame."""
        from tradingagents.dataflows.binance_spot_data import _load_ohlcv_binance

        # Create klines including future dates
        t1 = int(pd.Timestamp("2024-01-10", tz="UTC").timestamp() * 1000)
        t2 = int(pd.Timestamp("2024-01-20", tz="UTC").timestamp() * 1000)
        t3 = int(pd.Timestamp("2024-01-30", tz="UTC").timestamp() * 1000)

        mock_client = MagicMock()
        mock_client.get_klines.return_value = [_make_kline(t1), _make_kline(t2), _make_kline(t3)]

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client), \
             patch("tradingagents.dataflows.binance_spot_data.get_config",
                   return_value={"data_cache_dir": str(tmp_path)}):
            df = _load_ohlcv_binance("BTCUSDT", "2024-01-20")

        # Only rows up to 2024-01-20 should be present
        assert df["Date"].max() <= pd.Timestamp("2024-01-20")
        # 2024-01-30 row should be absent
        assert not (df["Date"] == pd.Timestamp("2024-01-30")).any()

    def test_returns_empty_on_no_data(self, tmp_path):
        from tradingagents.dataflows.binance_spot_data import _load_ohlcv_binance

        mock_client = MagicMock()
        mock_client.get_klines.return_value = []

        with patch("tradingagents.dataflows.binance_spot_data._get_client", return_value=mock_client), \
             patch("tradingagents.dataflows.binance_spot_data.get_config",
                   return_value={"data_cache_dir": str(tmp_path)}):
            df = _load_ohlcv_binance("FAKEUSDT", "2024-01-20")

        assert df.empty


# ---------------------------------------------------------------------------
# get_crypto_news
# ---------------------------------------------------------------------------

class TestGetCryptoNews:
    def test_network_failure_returns_fallback_string(self):
        """Network error → error string, not exception."""
        from tradingagents.dataflows.binance_spot_data import get_crypto_news

        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            result = get_crypto_news("BTCUSDT", "2024-01-15")

        assert isinstance(result, str)
        assert "BTC" in result or "unavailable" in result.lower()

    def test_normalises_symbol_strip_usdt(self):
        """BTCUSDT → BTC for CoinGecko lookup."""
        from tradingagents.dataflows.binance_spot_data import get_crypto_news

        # Simulate successful response with mock data
        mock_data = {
            "data": [
                {
                    "title": "Bitcoin hits new high",
                    "author": "CryptoReporter",
                    "updated_at": "2024-01-15",
                    "description": "Bitcoin rallied strongly today.",
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = __import__("json").dumps(mock_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = get_crypto_news("BTCUSDT", "2024-01-15")

        assert "Bitcoin hits new high" in result
        assert "BTC" in result

    def test_empty_articles_returns_no_news_message(self):
        """Empty data list → 'No recent news found' message."""
        from tradingagents.dataflows.binance_spot_data import get_crypto_news

        mock_resp = MagicMock()
        mock_resp.read.return_value = __import__("json").dumps({"data": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = get_crypto_news("ETHUSDT", "2024-01-15")

        assert "No recent news" in result


# ---------------------------------------------------------------------------
# get_crypto_fundamentals (binance_spot_data version)
# ---------------------------------------------------------------------------

class TestGetCryptoFundamentalsSpotData:
    def _make_coin_data(self):
        return {
            "name": "Bitcoin",
            "market_data": {
                "current_price": {"usd": 42000},
                "market_cap": {"usd": 820_000_000_000},
                "fully_diluted_valuation": {"usd": 882_000_000_000},
                "total_volume": {"usd": 15_000_000_000},
                "circulating_supply": 19_500_000,
                "total_supply": 19_500_000,
                "ath": {"usd": 69000},
                "ath_change_percentage": {"usd": -39.1},
                "price_change_percentage_30d": 8.5,
                "price_change_percentage_1y": 120.0,
            },
            "developer_data": {"stars": 72000, "commit_count_4_weeks": 23},
            "community_data": {"twitter_followers": 6_500_000},
        }

    def test_network_failure_returns_fallback_string(self):
        from tradingagents.dataflows.binance_spot_data import get_crypto_fundamentals as gcf_spot

        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            result = gcf_spot("BTCUSDT")

        assert isinstance(result, str)
        assert "unavailable" in result.lower() or "failed" in result.lower()

    def test_returns_price(self):
        from tradingagents.dataflows.binance_spot_data import get_crypto_fundamentals as gcf_spot

        mock_resp = MagicMock()
        mock_resp.read.return_value = __import__("json").dumps(self._make_coin_data()).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = gcf_spot("BTCUSDT")

        assert "42" in result  # price $42,000
        assert "Bitcoin" in result

    def test_returns_market_cap(self):
        from tradingagents.dataflows.binance_spot_data import get_crypto_fundamentals as gcf_spot

        mock_resp = MagicMock()
        mock_resp.read.return_value = __import__("json").dumps(self._make_coin_data()).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = gcf_spot("BTCUSDT")

        assert "820" in result  # market cap $820B

    def test_returns_developer_stats(self):
        from tradingagents.dataflows.binance_spot_data import get_crypto_fundamentals as gcf_spot

        mock_resp = MagicMock()
        mock_resp.read.return_value = __import__("json").dumps(self._make_coin_data()).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = gcf_spot("BTCUSDT")

        assert "72000" in result  # GitHub stars

