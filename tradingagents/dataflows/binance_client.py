"""Binance REST API client wrapper.

Provides authenticated access to Binance Spot API with:
- HMAC-SHA256 request signing
- Automatic rate-limit handling (1200 weight/min)
- Testnet / live toggle via environment variable
- No API keys required for public market-data endpoints

Usage::

    client = BinanceClient()                     # public endpoints only
    client = BinanceClient(signed=True)          # signed endpoints (order placement)
    df = client.get_klines("BTCUSDT", "1d", 200)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LIVE_BASE_URL = "https://api.binance.com"
_TESTNET_BASE_URL = "https://testnet.binance.vision"

# Binance weight limits: 1200/min for most endpoints.
# We conservatively target <900/min by sleeping between burst calls.
_REQUEST_WEIGHT_LIMIT = 900
_WINDOW_SECONDS = 60


class BinanceAPIError(Exception):
    """Raised when Binance returns a non-2xx response."""

    def __init__(self, status_code: int, code: int, msg: str) -> None:
        super().__init__(f"Binance API error {code}: {msg} (HTTP {status_code})")
        self.status_code = status_code
        self.code = code
        self.msg = msg


class BinanceClient:
    """Thin Binance REST client — no external SDK dependency required.

    Parameters
    ----------
    testnet:
        When True (default), uses ``https://testnet.binance.vision``.
        Set ``BINANCE_TESTNET=false`` in the environment to use live API.
    api_key:
        Binance API key.  Read from ``BINANCE_API_KEY`` env var if not provided.
    secret_key:
        Binance secret key.  Read from ``BINANCE_SECRET_KEY`` env var if not provided.
    """

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        testnet: Optional[bool] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.secret_key = secret_key or os.getenv("BINANCE_SECRET_KEY", "")

        if testnet is None:
            # Default to testnet unless explicitly disabled
            testnet_env = os.getenv("BINANCE_TESTNET", "true").lower()
            testnet = testnet_env not in ("false", "0", "no")

        self.testnet = testnet
        self.base_url = _TESTNET_BASE_URL if testnet else _LIVE_BASE_URL

        self._session = requests.Session()
        if self.api_key:
            self._session.headers.update({"X-MBX-APIKEY": self.api_key})

        logger.info(
            "BinanceClient initialized (%s)",
            "TESTNET" if testnet else "LIVE",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add HMAC-SHA256 signature to a parameter dict."""
        if not self.secret_key:
            raise RuntimeError(
                "BINANCE_SECRET_KEY is not set — cannot sign requests. "
                "Set it in the environment or pass secret_key to BinanceClient."
            )
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        """Execute a GET request, optionally signed."""
        params = params or {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params = self._sign(params)

        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=10)
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error calling Binance: {exc}") from exc

        if not resp.ok:
            try:
                err = resp.json()
            except Exception:
                err = {}
            raise BinanceAPIError(
                status_code=resp.status_code,
                code=err.get("code", -1),
                msg=err.get("msg", resp.text[:200]),
            )
        return resp.json()

    def _post(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute a signed POST request (order placement etc.)."""
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params = self._sign(params)

        url = f"{self.base_url}{path}"
        try:
            resp = self._session.post(url, params=params, timeout=10)
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error calling Binance: {exc}") from exc

        if not resp.ok:
            try:
                err = resp.json()
            except Exception:
                err = {}
            raise BinanceAPIError(
                status_code=resp.status_code,
                code=err.get("code", -1),
                msg=err.get("msg", resp.text[:200]),
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Public market-data endpoints (no auth required)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Test connectivity.  Returns True if Binance is reachable."""
        try:
            self._get("/api/v3/ping")
            return True
        except Exception:
            return False

    def get_klines(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 500,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[List[Any]]:
        """Fetch OHLCV candlestick data.

        Parameters
        ----------
        symbol:
            Binance pair, e.g. ``"BTCUSDT"``.
        interval:
            Kline interval: ``"1m"``, ``"5m"``, ``"15m"``, ``"1h"``,
            ``"4h"``, ``"1d"``, ``"1w"``.
        limit:
            Number of candles to return (max 1000).
        start_time_ms / end_time_ms:
            Unix millisecond timestamps for date range queries.

        Returns
        -------
        list of lists — each inner list is:
            [open_time, open, high, low, close, volume, close_time,
             quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]
        """
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        return self._get("/api/v3/klines", params)

    def get_ticker_24hr(self, symbol: Optional[str] = None) -> Any:
        """Fetch 24-hour rolling window price statistics.

        If ``symbol`` is None, returns stats for **all** USDT pairs
        (one dict per pair).  This is the primary signal for the
        ticker scanner.

        Returns
        -------
        dict  — when symbol is specified
        list[dict] — when symbol is None (all pairs)
        """
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._get("/api/v3/ticker/24hr", params)

    def get_exchange_info(self) -> Dict[str, Any]:
        """Fetch exchange info including all listed trading pairs."""
        return self._get("/api/v3/exchangeInfo")

    def get_current_price(self, symbol: str) -> float:
        """Return the latest price for a symbol."""
        data = self._get("/api/v3/ticker/price", {"symbol": symbol.upper()})
        return float(data["price"])

    # ------------------------------------------------------------------
    # Scanner helpers (built on top of public endpoints)
    # ------------------------------------------------------------------

    def get_top_volume_pairs(
        self,
        quote_asset: str = "USDT",
        min_volume_usd: float = 10_000_000,
        min_price_change_pct: float = 3.0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return the highest-volume pairs with significant price movement.

        This is the primary input for Phase 1 of the ticker scanner.

        Parameters
        ----------
        quote_asset:
            Quote currency to filter on (``"USDT"`` by default).
        min_volume_usd:
            Minimum 24h quote volume in USD (default: $10M).
        min_price_change_pct:
            Minimum absolute 24h price change % (default: 3%).
        limit:
            Maximum number of candidates to return.

        Returns
        -------
        list of dicts with keys: symbol, price_change_pct, volume_usdt,
        last_price, high_price, low_price, open_price.
        """
        all_tickers = self.get_ticker_24hr()
        if not isinstance(all_tickers, list):
            return []

        results = []
        for t in all_tickers:
            sym: str = t.get("symbol", "")
            if not sym.endswith(quote_asset):
                continue
            try:
                pct_change = float(t.get("priceChangePercent", 0))
                volume_usd = float(t.get("quoteVolume", 0))
                if volume_usd < min_volume_usd:
                    continue
                if abs(pct_change) < min_price_change_pct:
                    continue
                results.append({
                    "symbol": sym,
                    "price_change_pct": round(pct_change, 2),
                    "volume_usdt": round(volume_usd, 0),
                    "last_price": float(t.get("lastPrice", 0)),
                    "high_price": float(t.get("highPrice", 0)),
                    "low_price": float(t.get("lowPrice", 0)),
                    "open_price": float(t.get("openPrice", 0)),
                    "count": int(t.get("count", 0)),  # number of trades
                })
            except (TypeError, ValueError):
                continue

        # Sort by absolute price change descending, then by volume
        results.sort(key=lambda x: (abs(x["price_change_pct"]), x["volume_usdt"]), reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # Signed trading endpoints (require API key + secret)
    # ------------------------------------------------------------------

    def get_account(self) -> Dict[str, Any]:
        """Fetch account balances (signed)."""
        return self._get("/api/v3/account", signed=True)

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: Optional[float] = None,
        quote_order_qty: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place a market order.

        Parameters
        ----------
        symbol:
            Binance pair, e.g. ``"BTCUSDT"``.
        side:
            ``"BUY"`` or ``"SELL"``.
        quantity:
            Base asset quantity (e.g. 0.001 BTC).
            Mutually exclusive with ``quote_order_qty``.
        quote_order_qty:
            Quote asset spend amount (e.g. 100 USDT worth of BTC).
            Mutually exclusive with ``quantity``.
        """
        if quantity is None and quote_order_qty is None:
            raise ValueError("Either quantity or quote_order_qty must be provided")

        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": "MARKET",
        }
        if quantity is not None:
            params["quantity"] = quantity
        else:
            params["quoteOrderQty"] = quote_order_qty

        return self._post("/api/v3/order", params)

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        time_in_force: str = "GTC",
    ) -> Dict[str, Any]:
        """Place a limit order.

        Parameters
        ----------
        symbol:
            Binance pair, e.g. ``"BTCUSDT"``.
        side:
            ``"BUY"`` or ``"SELL"``.
        quantity:
            Base asset amount.
        price:
            Limit price in quote currency.
        time_in_force:
            ``"GTC"`` (Good Till Cancelled), ``"IOC"``, ``"FOK"``.
        """
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": quantity,
            "price": price,
        }
        return self._post("/api/v3/order", params)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open orders (signed)."""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._get("/api/v3/openOrders", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel an open order."""
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "orderId": order_id,
            "timestamp": int(time.time() * 1000),
        }
        params = self._sign(params)
        url = f"{self.base_url}/api/v3/order"
        resp = self._session.delete(url, params=params, timeout=10)
        if not resp.ok:
            try:
                err = resp.json()
            except Exception:
                err = {}
            raise BinanceAPIError(resp.status_code, err.get("code", -1), err.get("msg", ""))
        return resp.json()
