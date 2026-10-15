"""Alpaca Market Data client (free Basic plan) — US stocks/ETFs bars, news, asset list.

Enabled when ALPACA_API_KEY and ALPACA_API_SECRET are set. Callers fall back to
Yahoo for symbols Alpaca doesn't list (foreign listings, indices, futures) and
on any AlpacaError.

Free plan notes: full-market (SIP) bars are allowed except the most recent 15
minutes, so requests end 16 minutes ago; IEX-only data is used if SIP is refused.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.cache import ttl_cache

DATA_URL = "https://data.alpaca.markets"
MARKET_TZ = "America/New_York"
SIP_DELAY = timedelta(minutes=16)

# Calendar days of history per Yahoo-style period (a little extra for holidays)
PERIOD_DAYS = {"3mo": 95, "6mo": 186, "1y": 370, "2y": 735, "5y": 1830}
HISTORY_START = datetime(2016, 1, 1, tzinfo=timezone.utc)  # Alpaca data begins 2016


class AlpacaError(RuntimeError):
    pass


# Accept the names Alpaca's own docs and SDKs use, not just ours
KEY_VARS = ("ALPACA_API_KEY", "ALPACA_API_KEY_ID", "APCA_API_KEY_ID")
SECRET_VARS = ("ALPACA_API_SECRET", "ALPACA_SECRET_KEY", "ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip().strip("\"'")
        if value:
            return value
    return ""


def _credentials() -> tuple[str, str] | None:
    key, secret = _first_env(KEY_VARS), _first_env(SECRET_VARS)
    return (key, secret) if key and secret else None


def enabled() -> bool:
    return _credentials() is not None


def _trading_url() -> str:
    # Paper keys start with "PK"; the asset list lives on the trading API
    creds = _credentials()
    paper = creds is None or creds[0].startswith("PK")
    return "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"


def _request(
    method: str, url: str, params: dict | None = None, body: dict | None = None, timeout: float = 15
) -> dict | list | None:
    creds = _credentials()
    if not creds:
        raise AlpacaError("Alpaca keys not configured")
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    headers = {
        "APCA-API-KEY-ID": creds[0],
        "APCA-API-SECRET-KEY": creds[1],
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        raise AlpacaError(f"HTTP {exc.code}: {body}") from exc
    except (OSError, ValueError) as exc:
        raise AlpacaError(str(exc)) from exc


def _get(url: str, params: dict | None = None, timeout: float = 15) -> dict | list:
    return _request("GET", url, params=params, timeout=timeout)


def to_alpaca_symbol(symbol: str) -> str:
    # Yahoo writes share classes as BRK-B; Alpaca as BRK.B
    return symbol.replace("-", ".")


# ---- Asset list (for routing, names and search) ----

_NAME_NOISE = re.compile(
    r"\s+(?:Class [A-Z] )?(?:Common Stock|Ordinary Shares|American Depositary Shares?"
    r"|Depositary Shares?|Common Shares|Units?)\b.*$",
    re.IGNORECASE,
)


def clean_name(name: str) -> str:
    """'NVIDIA Corporation Common Stock' -> 'NVIDIA Corporation'."""
    return _NAME_NOISE.sub("", name).strip() or name


@ttl_cache(seconds=86400, maxsize=1)
def us_assets() -> dict[str, str]:
    """Active, tradable US equities/ETFs: Alpaca symbol -> display name."""
    raw = _get(
        f"{_trading_url()}/v2/assets",
        {"status": "active", "asset_class": "us_equity"},
        timeout=30,
    )
    return {
        a["symbol"]: clean_name(a.get("name") or a["symbol"])
        for a in raw
        if a.get("tradable") and a.get("symbol")
    }


_ASSETS_RETRY = timedelta(minutes=5)
_assets_failed_at: datetime | None = None


def _assets_or_none() -> dict[str, str] | None:
    """Asset list, or None if Alpaca is off or failed recently (don't retry every call)."""
    global _assets_failed_at
    if not enabled():
        return None
    now = datetime.now(timezone.utc)
    if _assets_failed_at and now - _assets_failed_at < _ASSETS_RETRY:
        return None
    try:
        return us_assets()
    except AlpacaError:
        _assets_failed_at = now
        return None


def lists(symbol: str) -> bool:
    """True when Alpaca covers this (Yahoo-style) symbol."""
    assets = _assets_or_none()
    return bool(assets) and to_alpaca_symbol(symbol) in assets


def asset_name(symbol: str) -> str | None:
    assets = _assets_or_none()
    return assets.get(to_alpaca_symbol(symbol)) if assets else None


def search_assets(query: str, limit: int = 8) -> list[dict]:
    """Rank: exact symbol, symbol prefix, name word prefix, name substring."""
    q = query.strip().upper()
    assets = _assets_or_none()
    if not q or not assets:
        return []
    word_re = re.compile(r"\b" + re.escape(q), re.IGNORECASE)
    scored = []
    for symbol, name in assets.items():
        if symbol == q:
            rank = 0
        elif symbol.startswith(q):
            rank = 1
        elif word_re.search(name):
            rank = 2
        elif q in name.upper():
            rank = 3
        else:
            continue
        scored.append((rank, len(symbol), symbol, name))
    scored.sort()
    return [
        {
            "symbol": s.replace(".", "-"),
            "name": n,
            "exchange": "US",
            "type": "Stock / ETF",
            "symbol_match": rank <= 1,
        }
        for rank, _, s, n in scored[:limit]
    ]


# ---- Daily bars ----


def _period_start(period: str, now: datetime) -> datetime:
    if period == "max":
        return HISTORY_START
    if period == "ytd":
        return datetime(now.year, 1, 1, tzinfo=timezone.utc)
    return max(HISTORY_START, now - timedelta(days=PERIOD_DAYS.get(period, 370)))


def _fetch_bars(symbol: str, start: datetime, end: datetime, feed: str) -> list[dict]:
    bars: list[dict] = []
    token = None
    while True:
        page = _get(
            f"{DATA_URL}/v2/stocks/{urllib.parse.quote(symbol, safe='')}/bars",
            {
                "timeframe": "1Day",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "adjustment": "all",  # split/dividend adjusted, like Yahoo's default
                "feed": feed,
                "limit": 10000,
                "page_token": token,
            },
        )
        bars.extend(page.get("bars") or [])
        token = page.get("next_page_token")
        if not token:
            return bars


def history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Daily OHLCV shaped like yfinance's history(): NY-tz index, Open..Volume."""
    now = datetime.now(timezone.utc)
    start = _period_start(period, now)
    alpaca_symbol = to_alpaca_symbol(symbol)
    try:
        bars = _fetch_bars(alpaca_symbol, start, now - SIP_DELAY, "sip")
    except AlpacaError as exc:
        # Some accounts/plans refuse SIP; IEX is always free (but IEX-only volume)
        if "403" not in str(exc) and "subscription" not in str(exc).lower():
            raise
        bars = _fetch_bars(alpaca_symbol, start, now, "iex")

    if not bars:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    index = pd.DatetimeIndex(
        pd.to_datetime([b["t"] for b in bars], utc=True).tz_convert(MARKET_TZ).normalize(),
        name="Date",
    )
    return pd.DataFrame(
        {
            "Open": [float(b["o"]) for b in bars],
            "High": [float(b["h"]) for b in bars],
            "Low": [float(b["l"]) for b in bars],
            "Close": [float(b["c"]) for b in bars],
            "Volume": [float(b["v"]) for b in bars],
        },
        index=index,
    )


def latest_trade_price(symbol: str) -> float | None:
    """Last trade price (IEX feed, which the free plan allows in real time)."""
    data = _get(
        f"{DATA_URL}/v2/stocks/{urllib.parse.quote(to_alpaca_symbol(symbol), safe='')}/trades/latest",
        {"feed": "iex"},
    )
    price = (data.get("trade") or {}).get("p")
    return float(price) if price else None


# ---- News (Benzinga via Alpaca) ----


def news(symbol: str, limit: int = 10) -> list[dict]:
    data = _get(
        f"{DATA_URL}/v1beta1/news",
        {"symbols": to_alpaca_symbol(symbol), "limit": min(max(limit, 1), 50), "sort": "desc"},
    )
    return data.get("news") or []
