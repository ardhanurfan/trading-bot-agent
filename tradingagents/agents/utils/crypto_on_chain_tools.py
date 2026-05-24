"""Crypto on-chain and market fundamentals tools.

Provides LLM-callable tools for:
- Market cap, circulating supply, FDV, 24h volume from CoinGecko
- Fear & Greed Index + funding rate for sentiment
- No API key required (CoinGecko free tier + alternative.me)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CoinGecko coin ID mapping (Binance symbol → CoinGecko id)
# ---------------------------------------------------------------------------

_SYMBOL_TO_COINGECKO: Dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "LTC": "litecoin",
    "ARB": "arbitrum",
    "OP": "optimism",
    "APT": "aptos",
    "SUI": "sui",
    "INJ": "injective-protocol",
    "TIA": "celestia",
    "SEI": "sei-network",
    "JUP": "jupiter-ag",
    "WIF": "dogwifhat",
    "BLUR": "blur",
}


def _binance_symbol_to_coingecko(ticker: str) -> Optional[str]:
    """Extract base asset from Binance ticker and map to CoinGecko id."""
    base = (
        ticker.replace("USDT", "")
              .replace("BUSD", "")
              .replace("USDC", "")
              .replace("BTC", "", 1) if not ticker.endswith("BTC") else ticker[:-3]
    )
    # Handle USDT-quoted symbols
    for suffix in ("USDT", "BUSD", "USDC", "FDUSD", "BTC", "ETH", "BNB"):
        if ticker.endswith(suffix) and ticker != suffix:
            base = ticker[: -len(suffix)]
            break
    return _SYMBOL_TO_COINGECKO.get(base.upper())


def _safe_urlopen(url: str, timeout: int = 7) -> Optional[Any]:
    """GET url and parse JSON. Returns None on any failure."""
    try:
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("HTTP fetch failed [%s]: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def get_crypto_fundamentals(ticker: str) -> str:
    """Return market fundamentals for a crypto asset from CoinGecko.

    Args:
        ticker: Binance trading pair (e.g. "BTCUSDT", "SOLUSDT")

    Returns:
        Human-readable string with market cap, circulating supply, FDV,
        24h volume, ATH/ATL, and project description snippet.
        Returns a graceful error string if data is unavailable.
    """
    cg_id = _binance_symbol_to_coingecko(ticker)
    if not cg_id:
        # Attempt dynamic CoinGecko search as fallback
        base = ticker.replace("USDT", "").replace("BUSD", "")
        search = _safe_urlopen(
            f"https://api.coingecko.com/api/v3/search?query={base}"
        )
        if search and search.get("coins"):
            cg_id = search["coins"][0]["id"]
        else:
            return f"CoinGecko mapping not found for {ticker}. No fundamental data available."

    url = (
        f"https://api.coingecko.com/api/v3/coins/{cg_id}"
        "?localization=false&tickers=false&community_data=false"
        "&developer_data=false&sparkline=false"
    )
    data = _safe_urlopen(url)
    if not data:
        return f"Failed to fetch CoinGecko data for {cg_id} ({ticker})."

    md = data.get("market_data", {})

    def fmt_usd(val: Any) -> str:
        if val is None:
            return "N/A"
        try:
            v = float(val)
            if v >= 1e12:
                return f"${v/1e12:.2f}T"
            if v >= 1e9:
                return f"${v/1e9:.2f}B"
            if v >= 1e6:
                return f"${v/1e6:.2f}M"
            return f"${v:,.2f}"
        except (TypeError, ValueError):
            return "N/A"

    name = data.get("name", ticker)
    symbol = data.get("symbol", "").upper()
    current_price = fmt_usd(md.get("current_price", {}).get("usd"))
    market_cap = fmt_usd(md.get("market_cap", {}).get("usd"))
    fdv = fmt_usd(md.get("fully_diluted_valuation", {}).get("usd"))
    volume_24h = fmt_usd(md.get("total_volume", {}).get("usd"))
    circulating = md.get("circulating_supply")
    total_supply = md.get("total_supply")
    max_supply = md.get("max_supply")
    mc_rank = data.get("market_cap_rank", "N/A")
    pct_24h = md.get("price_change_percentage_24h")
    pct_7d = md.get("price_change_percentage_7d")
    ath = fmt_usd(md.get("ath", {}).get("usd"))
    ath_pct = md.get("ath_change_percentage", {}).get("usd")

    desc = data.get("description", {}).get("en", "")
    desc_snippet = desc[:300].rstrip() + "..." if len(desc) > 300 else desc

    supply_text = f"{circulating:,.0f}" if circulating else "N/A"
    total_text = f"{total_supply:,.0f}" if total_supply else "N/A"
    max_text = f"{max_supply:,.0f}" if max_supply else "∞"

    lines = [
        f"=== {name} ({symbol}) Crypto Fundamentals ===",
        f"Price: {current_price}",
        f"24h Change: {pct_24h:+.2f}%" if pct_24h is not None else "24h Change: N/A",
        f"7d Change: {pct_7d:+.2f}%" if pct_7d is not None else "7d Change: N/A",
        f"Market Cap: {market_cap} (Rank #{mc_rank})",
        f"Fully Diluted Valuation: {fdv}",
        f"24h Volume: {volume_24h}",
        f"Circulating Supply: {supply_text}",
        f"Total Supply: {total_text}",
        f"Max Supply: {max_text}",
        f"ATH: {ath}" + (f" ({ath_pct:+.1f}% from ATH)" if ath_pct is not None else ""),
        "",
        "Project Overview:",
        desc_snippet,
    ]
    return "\n".join(lines)


def get_crypto_market_sentiment(ticker: str) -> str:
    """Return sentiment indicators: Fear & Greed Index + funding rate.

    Args:
        ticker: Binance trading pair (e.g. "BTCUSDT")

    Returns:
        Human-readable string combining:
        - Crypto Fear & Greed Index (alternative.me)
        - Binance perpetual funding rate for the asset
    """
    lines: list[str] = [f"=== {ticker} Market Sentiment ==="]

    # Fear & Greed Index
    fng = _safe_urlopen("https://api.alternative.me/fng/?limit=7")
    if fng and fng.get("data"):
        entries = fng["data"]
        current = entries[0]
        lines.append(
            f"Fear & Greed Index (now): {current['value']}/100 "
            f"— {current['value_classification']}"
        )
        # 7-day trend
        vals = [int(e["value"]) for e in entries]
        trend = "improving" if vals[0] > vals[-1] else "declining" if vals[0] < vals[-1] else "stable"
        lines.append(f"  7-day sentiment trend: {trend} ({vals[-1]} → {vals[0]})")
    else:
        lines.append("Fear & Greed Index: unavailable")

    # Binance perpetual funding rate (FAPI — public, no auth)
    perp_symbol = ticker  # Most USDT perps have same symbol
    funding_url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={perp_symbol}&limit=1"
    funding_data = _safe_urlopen(funding_url)
    if funding_data and isinstance(funding_data, list) and funding_data:
        rate = float(funding_data[0].get("fundingRate", 0)) * 100
        lines.append(
            f"Perpetual Funding Rate (8h): {rate:+.4f}%"
            + (" [bullish bias — longs paying shorts]" if rate > 0.05 else
               " [bearish bias — shorts paying longs]" if rate < -0.01 else
               " [neutral]")
        )
    else:
        # Fallback: try the premium index endpoint
        premium_url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={perp_symbol}"
        premium_data = _safe_urlopen(premium_url)
        if premium_data:
            if isinstance(premium_data, list):
                premium_data = premium_data[0]
            rate = float(premium_data.get("lastFundingRate", 0)) * 100
            lines.append(
                f"Perpetual Funding Rate (8h): {rate:+.4f}%"
                + (" [bullish bias]" if rate > 0.05 else
                   " [bearish bias]" if rate < -0.01 else
                   " [neutral]")
            )
        else:
            lines.append("Funding Rate: unavailable (spot-only pair or API error)")

    # Open interest hint from CoinGecko derivatives (optional)
    cg_id = _binance_symbol_to_coingecko(ticker)
    if cg_id:
        cg_data = _safe_urlopen(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}"
            "?localization=false&tickers=false&community_data=true"
            "&developer_data=false&sparkline=false"
        )
        if cg_data:
            community = cg_data.get("community_data", {})
            twitter = community.get("twitter_followers")
            reddit = community.get("reddit_subscribers")
            if twitter:
                lines.append(f"Twitter followers: {twitter:,}")
            if reddit:
                lines.append(f"Reddit subscribers: {reddit:,}")
            sentiment_votes = cg_data.get("sentiment_votes_up_percentage")
            if sentiment_votes is not None:
                lines.append(f"CoinGecko community sentiment: {sentiment_votes:.1f}% bullish")

    return "\n".join(lines)
