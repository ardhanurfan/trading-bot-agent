"""Binance Spot data functions — mirrors the yfinance output schema.

All functions return the same string/DataFrame formats as their counterparts
in ``y_finance.py`` so that agents can switch vendors transparently via
``interface.py``'s ``route_to_vendor`` mechanism.

OHLCV columns returned: Date, Open, High, Low, Close, Volume
(same as yfinance so stockstats indicators work unchanged)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Annotated, Dict, Optional

import pandas as pd

from .binance_client import BinanceClient, BinanceAPIError
from .config import get_config
from .stockstats_utils import _clean_dataframe
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level client (lazy init, testnet by default)
# ---------------------------------------------------------------------------

_client: Optional[BinanceClient] = None


def _get_client() -> BinanceClient:
    global _client
    if _client is None:
        _client = BinanceClient()
    return _client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _date_to_ms(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' to Unix milliseconds (UTC midnight)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _klines_to_dataframe(klines: list, symbol: str) -> pd.DataFrame:
    """Convert Binance klines list to a normalised OHLCV DataFrame.

    Output columns match yfinance so that stockstats indicators work
    without modification: Date, Open, High, Low, Close, Volume.
    """
    if not klines:
        return pd.DataFrame()

    rows = []
    for k in klines:
        rows.append({
            "Date": pd.to_datetime(int(k[0]), unit="ms", utc=True).tz_localize(None),
            "Open": float(k[1]),
            "High": float(k[2]),
            "Low": float(k[3]),
            "Close": float(k[4]),
            "Volume": float(k[5]),
        })

    df = pd.DataFrame(rows)
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    return df


def _fetch_full_range(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch OHLCV for an arbitrary date range using paginated klines calls."""
    client = _get_client()
    start_ms = _date_to_ms(start_date)
    end_ms = _date_to_ms(end_date)

    all_klines = []
    current_start = start_ms
    limit = 1000  # Binance max per request

    while current_start < end_ms:
        batch = client.get_klines(
            symbol=symbol,
            interval="1d",
            limit=limit,
            start_time_ms=current_start,
            end_time_ms=end_ms,
        )
        if not batch:
            break
        all_klines.extend(batch)
        # Move start to last candle's close_time + 1ms to avoid overlap
        last_close_time = int(batch[-1][6])
        current_start = last_close_time + 1
        if len(batch) < limit:
            break
        time.sleep(0.1)  # Respect rate limits between paginated calls

    return _klines_to_dataframe(all_klines, symbol)


def _load_ohlcv_binance(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch and cache OHLCV data, filtered to prevent look-ahead bias.

    Cache filename mirrors yfinance cache pattern so cache dirs are shared.
    """
    safe_symbol = safe_ticker_component(symbol)
    config = get_config()

    today = pd.Timestamp.today()
    start_date = (today - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    cache_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-Binance-data-{start_date}-{end_date}.csv",
    )

    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file, on_bad_lines="skip", encoding="utf-8")
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    else:
        df_raw = _fetch_full_range(symbol, start_date, end_date)
        if df_raw.empty:
            return pd.DataFrame()
        df_raw.index.name = "Date"
        df_raw.reset_index(inplace=True)
        df_raw.to_csv(cache_file, index=False)
        df = df_raw.copy()
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # Look-ahead bias prevention: drop rows after curr_date
    curr_dt = pd.to_datetime(curr_date)
    df = df[df["Date"] <= curr_dt]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public API — mirrors y_finance.py signatures
# ---------------------------------------------------------------------------


def get_binance_ohlcv(
    symbol: Annotated[str, "Binance pair symbol, e.g. BTCUSDT"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Fetch Binance OHLCV data and return as a CSV string.

    Output format matches ``get_YFin_data_online`` so existing agents work
    without modification.
    """
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    client = _get_client()
    try:
        df = _fetch_full_range(symbol.upper(), start_date, end_date)
    except BinanceAPIError as exc:
        return f"No data found for symbol '{symbol}' between {start_date} and {end_date}: {exc}"

    if df.empty:
        return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

    # Round for cleaner output
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col].round(2)

    csv_string = df.reset_index().to_csv(index=False)
    header = (
        f"# Crypto OHLCV data for {symbol.upper()} from {start_date} to {end_date} (Binance)\n"
        f"# Total records: {len(df)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv_string


def get_binance_indicators(
    symbol: Annotated[str, "Binance pair symbol, e.g. BTCUSDT"],
    indicator: Annotated[str, "Technical indicator name (same as yfinance version)"],
    curr_date: Annotated[str, "Current trading date YYYY-MM-DD"],
    look_back_days: Annotated[int, "Number of days to look back"] = 30,
) -> str:
    """Compute technical indicators from Binance OHLCV data.

    Uses the same ``stockstats`` pipeline as the yfinance path — only
    the data source changes.  Indicator names are identical.
    """
    from datetime import datetime as _dt
    from dateutil.relativedelta import relativedelta
    try:
        from stockstats import wrap
    except ImportError:
        return f"stockstats not installed — cannot compute indicators for {symbol}."

    df = _load_ohlcv_binance(symbol, curr_date)
    if df.empty:
        return f"No data available for {symbol} up to {curr_date}."

    df = _clean_dataframe(df)
    if df.empty:
        return f"Data for {symbol} could not be cleaned."

    try:
        ss = wrap(df)
        ss["Date"] = ss["Date"].dt.strftime("%Y-%m-%d")
        ss[indicator]  # trigger stockstats calculation

        # Build date → value dict
        indicator_data: Dict[str, str] = {}
        for _, row in ss.iterrows():
            val = row[indicator]
            indicator_data[row["Date"]] = "N/A" if pd.isna(val) else str(val)

    except Exception as exc:
        return f"Error computing {indicator} for {symbol}: {exc}"

    curr_dt = _dt.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - relativedelta(days=look_back_days)

    ind_string = ""
    ptr = curr_dt
    while ptr >= before:
        ds = ptr.strftime("%Y-%m-%d")
        ind_string += f"{ds}: {indicator_data.get(ds, 'N/A: Not a trading day')}\n"
        ptr -= relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date} "
        f"({symbol}, Binance):\n\n{ind_string}"
    )


def get_crypto_news(
    symbol: Annotated[str, "Crypto symbol without quote pair, e.g. BTC or BTCUSDT"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"],
    look_back_days: Annotated[int, "Days to look back for news"] = 7,
) -> str:
    """Fetch recent news for a crypto asset via CoinGecko (free, no key).

    Returns a formatted string of news headlines and summaries.
    Falls back gracefully if the request fails.
    """
    # Normalise symbol: BTCUSDT → BTC
    base = symbol.upper().replace("USDT", "").replace("BUSD", "").replace("USDC", "")

    # CoinGecko coin ID map for common assets
    _CG_ID_MAP: Dict[str, str] = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "BNB": "binancecoin",
        "SOL": "solana",
        "ADA": "cardano",
        "XRP": "ripple",
        "DOT": "polkadot",
        "AVAX": "avalanche-2",
        "MATIC": "matic-network",
        "LINK": "chainlink",
        "UNI": "uniswap",
        "ATOM": "cosmos",
        "LTC": "litecoin",
        "DOGE": "dogecoin",
        "SHIB": "shiba-inu",
    }
    coin_id = _CG_ID_MAP.get(base, base.lower())

    try:
        import urllib.request
        import json

        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/news?per_page=10&page=1"
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        articles = data.get("data", data) if isinstance(data, dict) else data
        if not articles:
            return f"No recent news found for {base}."

        lines = [f"# Recent news for {base} (CoinGecko)\n"]
        for i, article in enumerate(articles[:10], 1):
            title = article.get("title", "")
            author = article.get("author", "")
            published = article.get("updated_at", article.get("created_at", ""))
            description = article.get("description", "")
            lines.append(f"{i}. **{title}**")
            if author:
                lines.append(f"   Author: {author}")
            if published:
                lines.append(f"   Date: {published}")
            if description:
                lines.append(f"   {description[:200]}")
            lines.append("")
        return "\n".join(lines)

    except Exception as exc:
        logger.warning("CoinGecko news fetch failed for %s: %s", base, exc)
        return f"News unavailable for {base} (CoinGecko request failed: {exc})"


def get_crypto_fundamentals(
    symbol: Annotated[str, "Crypto symbol, e.g. BTCUSDT or BTC"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"] = "",
) -> str:
    """Fetch on-chain/market fundamentals from CoinGecko (free tier).

    Returns market cap, circulating supply, total supply, FDV,
    24h volume, all-time high/low, developer activity score, and
    community stats.

    This replaces ``get_fundamentals`` from the yfinance path for crypto.
    """
    base = symbol.upper().replace("USDT", "").replace("BUSD", "").replace("USDC", "")

    _CG_ID_MAP: Dict[str, str] = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "SOL": "solana", "ADA": "cardano", "XRP": "ripple",
        "DOT": "polkadot", "AVAX": "avalanche-2", "MATIC": "matic-network",
        "LINK": "chainlink", "UNI": "uniswap", "ATOM": "cosmos",
        "LTC": "litecoin", "DOGE": "dogecoin", "SHIB": "shiba-inu",
    }
    coin_id = _CG_ID_MAP.get(base, base.lower())

    try:
        import urllib.request
        import json

        url = (
            f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            "?localization=false&tickers=false&market_data=true"
            "&community_data=true&developer_data=true&sparkline=false"
        )
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            coin = json.loads(resp.read())

        md = coin.get("market_data", {})
        dd = coin.get("developer_data", {})
        cd = coin.get("community_data", {})

        price_usd = md.get("current_price", {}).get("usd", "N/A")
        market_cap = md.get("market_cap", {}).get("usd", "N/A")
        fdv = md.get("fully_diluted_valuation", {}).get("usd", "N/A")
        vol_24h = md.get("total_volume", {}).get("usd", "N/A")
        circ_supply = md.get("circulating_supply", "N/A")
        total_supply = md.get("total_supply", "N/A")
        ath = md.get("ath", {}).get("usd", "N/A")
        ath_change = md.get("ath_change_percentage", {}).get("usd", "N/A")
        price_change_30d = md.get("price_change_percentage_30d", "N/A")
        price_change_1y = md.get("price_change_percentage_1y", "N/A")
        github_stars = dd.get("stars", "N/A")
        github_commits_4w = dd.get("commit_count_4_weeks", "N/A")
        twitter_followers = cd.get("twitter_followers", "N/A")

        def _fmt(n):
            if isinstance(n, (int, float)):
                if n >= 1_000_000_000:
                    return f"${n/1_000_000_000:.2f}B"
                if n >= 1_000_000:
                    return f"${n/1_000_000:.2f}M"
                return f"${n:,.2f}"
            return str(n)

        lines = [
            f"# Crypto Fundamentals: {coin.get('name', base)} ({base})",
            f"",
            f"**Current Price**: {_fmt(price_usd)}",
            f"**Market Cap**: {_fmt(market_cap)}",
            f"**Fully Diluted Valuation**: {_fmt(fdv)}",
            f"**24h Volume**: {_fmt(vol_24h)}",
            f"**Circulating Supply**: {circ_supply:,}" if isinstance(circ_supply, (int, float)) else f"**Circulating Supply**: {circ_supply}",
            f"**Total Supply**: {total_supply}",
            f"**All-Time High**: {_fmt(ath)} ({ath_change:.1f}% from ATH)" if isinstance(ath_change, float) else f"**All-Time High**: {_fmt(ath)}",
            f"",
            f"**Price Change (30d)**: {price_change_30d:.1f}%" if isinstance(price_change_30d, float) else "",
            f"**Price Change (1y)**: {price_change_1y:.1f}%" if isinstance(price_change_1y, float) else "",
            f"",
            f"**Developer Activity**:",
            f"  - GitHub Stars: {github_stars}",
            f"  - Commits (last 4 weeks): {github_commits_4w}",
            f"",
            f"**Community**:",
            f"  - Twitter Followers: {twitter_followers:,}" if isinstance(twitter_followers, int) else f"  - Twitter Followers: {twitter_followers}",
        ]
        return "\n".join(l for l in lines if l is not None)

    except Exception as exc:
        logger.warning("CoinGecko fundamentals fetch failed for %s: %s", base, exc)
        return f"Fundamentals unavailable for {base} (CoinGecko request failed: {exc})"
