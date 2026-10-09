"""Ticker validation, display names and Yahoo symbol search."""

from __future__ import annotations

import re

import yfinance as yf
from fastapi import HTTPException

from backend import alpaca
from backend.cache import ttl_cache
from backend.config import SYMBOL_NAMES, WATCHLIST

# Yahoo symbols: AAPL, BRK-B, 005930.KS, ^SOX, NQ=F
SYMBOL_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")
MAX_WATCHLIST = 30

# Asset types worth charting; skips options, currencies' odd variants, etc.
SEARCHABLE_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND", "FUTURE", "CRYPTOCURRENCY"}


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=400, detail=f"Invalid ticker symbol: {symbol!r}")
    return symbol


def parse_symbols(raw: str | None) -> list[str]:
    """Comma-separated symbols from a query param; default watchlist when empty."""
    if not raw:
        return list(WATCHLIST)
    symbols: list[str] = []
    for part in raw.split(","):
        if part.strip():
            symbol = normalize_symbol(part)
            if symbol not in symbols:
                symbols.append(symbol)
    if len(symbols) > MAX_WATCHLIST:
        raise HTTPException(status_code=400, detail=f"At most {MAX_WATCHLIST} symbols")
    return symbols or list(WATCHLIST)


@ttl_cache(seconds=3600, maxsize=200)
def search_symbols(query: str, limit: int = 8) -> list[dict]:
    """Merge Alpaca's US list with Yahoo search.

    Order: Alpaca ticker matches ("TSL" -> TSLA), then Yahoo's relevance-ranked
    results (good at company names and foreign listings), then Alpaca name
    matches as a fallback when Yahoo is down or thin.
    """
    alpaca_hits = alpaca.search_assets(query, limit=limit)
    try:
        yahoo = _yahoo_search(query, limit)
    except Exception:
        if not alpaca_hits:
            raise
        yahoo = []

    ordered = (
        [r for r in alpaca_hits if r["symbol_match"]]
        + yahoo
        + [r for r in alpaca_hits if not r["symbol_match"]]
    )
    results, seen = [], set()
    for r in ordered:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        results.append({k: v for k, v in r.items() if k != "symbol_match"})
        if len(results) == limit:
            break
    return results


def _yahoo_search(query: str, limit: int) -> list[dict]:
    quotes = yf.Search(query, max_results=limit * 2, news_count=0).quotes or []
    results = []
    for q in quotes:
        if q.get("quoteType") not in SEARCHABLE_TYPES or not q.get("symbol"):
            continue
        results.append(
            {
                "symbol": q["symbol"],
                "name": q.get("shortname") or q.get("longname") or q["symbol"],
                "exchange": q.get("exchDisp") or q.get("exchange") or "",
                "type": q.get("typeDisp") or q.get("quoteType"),
            }
        )
    return results[:limit]


@ttl_cache(seconds=86400, maxsize=500)
def _yahoo_name(symbol: str) -> str:
    for result in _yahoo_search(symbol, 8):
        if result["symbol"].upper() == symbol:
            return result["name"]
    return symbol


def lookup_name(symbol: str) -> str:
    if symbol in SYMBOL_NAMES:
        return SYMBOL_NAMES[symbol]
    name = alpaca.asset_name(symbol)
    if name:
        return name
    try:
        return _yahoo_name(symbol)
    except Exception:
        return symbol
