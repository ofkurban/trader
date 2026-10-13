"""Early-signals screener: stocks showing signs of a move up before it's obvious.

Two stages, to keep Yahoo request counts sane:
  1. One batched price download for the whole universe -> volume, returns,
     relative strength. Drops illiquid names and anything up >30% in a month,
     then keeps the most interesting candidates.
  2. Per-candidate fundamentals (estimate revisions, insider buys, earnings
     date, revenue trend, valuation) fetched in parallel.

Each stock is scored on ten signals and ranked by how many line up. The
earnings-date catalyst counts half: nearly every stock reports within three
months, so it barely separates candidates.
"""

from __future__ import annotations

import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from backend.cache import ttl_cache
from backend.config import WATCHLIST
from backend.ladder import compute_ladder
from backend.levels import compute_levels

log = logging.getLogger("trader")

# Liquid US names across sectors. Yahoo's predefined screens add fresh names
# on top of this at scan time.
UNIVERSE = [
    # Semis / hardware
    "NVDA", "AMD", "AVGO", "QCOM", "TXN", "ADI", "MCHP", "ON", "NXPI", "LRCX",
    "AMAT", "KLAC", "TER", "ENTG", "MPWR", "SMCI", "ANET", "DELL", "HPE", "CSCO",
    "COHR", "LITE", "CIEN", "FN", "ALAB", "CRDO", "INTC", "GFS",
    # Software / internet
    "MSFT", "ORCL", "CRM", "ADBE", "NOW", "INTU", "PANW", "CRWD", "ZS", "FTNT",
    "NET", "DDOG", "SNOW", "MDB", "TEAM", "WDAY", "HUBS", "OKTA", "DOCU", "TWLO",
    "SHOP", "UBER", "DASH", "ABNB", "PINS", "SNAP", "RDDT", "META", "GOOGL", "AMZN",
    "APP", "TTD", "ROKU", "SPOT", "NFLX", "PYPL", "AFRM", "SOFI", "HOOD", "COIN",
    # Healthcare / biotech
    "LLY", "UNH", "ABBV", "MRK", "PFE", "BMY", "AMGN", "GILD", "VRTX", "REGN",
    "BIIB", "MRNA", "ISRG", "DXCM", "PODD", "BSX", "SYK", "EW", "HCA", "CVS",
    "ELV", "CI", "HUM", "ALNY", "INCY", "NBIX", "SRPT", "TGTX", "HIMS",
    # Financials
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "AXP", "COF", "BLK",
    "KKR", "APO", "BX", "ALLY", "PGR",
    # Consumer
    "COST", "WMT", "TGT", "HD", "LOW", "NKE", "LULU", "DECK", "ONON", "CMG",
    "SBUX", "MCD", "CAVA", "ELF", "EL", "ULTA", "TJX", "ROST", "BBY", "KMX",
    "F", "GM", "TSLA", "RIVN", "CELH", "MNST", "KO", "PEP",
    # Industrials / energy / materials
    "GE", "GEV", "CAT", "DE", "BA", "RTX", "LMT", "NOC", "HON", "ETN",
    "PWR", "URI", "FDX", "UPS", "XOM", "CVX", "COP", "OXY", "SLB",
    "HAL", "FCX", "NEM", "AA", "CEG", "VST", "NRG", "OKLO", "SMR", "FSLR",
    "ENPH", "RKLB", "AXON", "TT",
]

SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}
BENCHMARK = "SPY"

DYNAMIC_SCREENS = ("undervalued_growth_stocks", "growth_technology_stocks", "most_actives")

SIGNALS = [
    ("revisions", "Estimates rising"),
    ("insiders", "Insider buying"),
    ("volume", "Unusual volume"),
    ("catalyst", "Catalyst soon"),
    ("strength", "Relative strength"),
    ("value", "Growth at a discount"),
    ("accumulation", "Quiet accumulation"),
    ("upgrades", "Analyst upgrades"),
    ("beats", "Earnings-beat streak"),
    ("shorts", "Shorts covering"),
]
WEIGHTS = {"catalyst": 0.5}

MAX_MONTH_GAIN = 0.30
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 20_000_000
CANDIDATES = 45
TOP_N = 10


# ---- Stage 1: prices for everyone ----


def _dynamic_symbols() -> list[str]:
    found: list[str] = []
    for name in DYNAMIC_SCREENS:
        try:
            quotes = yf.screen(name, count=25).get("quotes", [])
        except Exception as exc:
            log.warning("Yahoo screen %s failed: %s", name, exc)
            continue
        found += [q["symbol"] for q in quotes if q.get("quoteType") == "EQUITY" and "." not in q["symbol"]]
    return found


def _download(symbols: list[str]) -> dict[str, pd.DataFrame]:
    raw = yf.download(
        symbols, period="1y", interval="1d", group_by="ticker",
        auto_adjust=True, threads=True, progress=False,
    )
    out = {}
    for symbol in symbols:
        try:
            frame = raw[symbol].dropna(subset=["Close"])
        except KeyError:
            continue
        if len(frame) >= 60:
            out[symbol] = frame
    return out


def _ret(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - days] - 1)


def price_metrics(frame: pd.DataFrame) -> dict:
    close, volume = frame["Close"], frame["Volume"]
    recent_vol = float(volume.iloc[-5:].mean())
    base_vol = float(volume.iloc[-55:-5].mean())
    return {
        "price": round(float(close.iloc[-1]), 2),
        "ret_10d": _ret(close, 10),
        "ret_1m": _ret(close, 21),
        "ret_3m": _ret(close, 63),
        "vol_ratio": recent_vol / base_vol if base_vol > 0 else None,
        "dollar_volume": float((close.iloc[-50:] * volume.iloc[-50:]).mean()),
        "sma200": float(close.iloc[-200:].mean()) if len(close) >= 200 else None,
        "updown_ratio": updown_volume_ratio(close, volume),
    }


def updown_volume_ratio(close: pd.Series, volume: pd.Series, days: int = 50) -> float | None:
    """Volume on up days / volume on down days — >1 means buyers are doing the work."""
    change = close.diff().iloc[-days:]
    vol = volume.iloc[-days:]
    up, down = float(vol[change > 0].sum()), float(vol[change < 0].sum())
    return up / down if down > 0 else None


def _prescore(m: dict, spy: dict) -> float:
    """Cheap ranking used only to pick which names get the expensive fetches."""
    score = 0.0
    if m["vol_ratio"]:
        score += min(m["vol_ratio"], 3.0)
    if m["ret_1m"] is not None and spy.get("ret_1m") is not None:
        score += max(-1.0, min(2.0, (m["ret_1m"] - spy["ret_1m"]) * 10))
    if m["ret_3m"] is not None and spy.get("ret_3m") is not None:
        score += max(-1.0, min(1.0, (m["ret_3m"] - spy["ret_3m"]) * 5))
    if m["updown_ratio"]:
        score += max(-1.0, min(1.5, (m["updown_ratio"] - 1) * 3))
    return score


# ---- Stage 2: per-stock fundamentals ----


def _safe(fn, default=None):
    try:
        value = fn()
    except Exception:
        return default
    return default if value is None else value


def _num(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(value) else value


def revisions_signal(eps_trend: pd.DataFrame | None, eps_revisions: pd.DataFrame | None) -> dict:
    up = down = 0
    if eps_revisions is not None and not eps_revisions.empty:
        up = int(sum(_num(v) or 0 for v in eps_revisions.get("upLast30days", [])))
        down = int(sum(_num(v) or 0 for v in eps_revisions.get("downLast30days", [])))
    change = None
    if eps_trend is not None and not eps_trend.empty:
        for period in ("0y", "+1y"):
            if period in eps_trend.index:
                now = _num(eps_trend.at[period, "current"])
                before = _num(eps_trend.at[period, "30daysAgo"])
                if now is not None and before and before > 0:
                    change = now / before - 1
                    break
    hit = (up >= 3 and up >= 2 * max(down, 1) and (change is None or change >= 0)) or (
        change is not None and change >= 0.02 and up > down
    )
    parts = []
    if change is not None:
        parts.append(f"FY EPS estimate {change * 100:+.1f}% in 30d")
    if up or down:
        parts.append(f"{up} up / {down} down revisions")
    return {
        "hit": hit,
        "detail": " · ".join(parts) or "No estimate data",
        "strength": (change or 0) * 10 + min(up, 40) / 40,
        "cuts": down > up,
    }


def insider_signal(transactions: pd.DataFrame | None, today: date) -> dict:
    empty = {"hit": False, "detail": "No open-market buys in 90d", "strength": 0.0, "sales": 0}
    if transactions is None or transactions.empty:
        return empty
    cutoff = pd.Timestamp(today - timedelta(days=90))
    df = transactions.copy()
    df["when"] = pd.to_datetime(df.get("Start Date"), errors="coerce")
    df = df[df["when"] >= cutoff]
    text = df.get("Text", pd.Series(dtype=str)).fillna("").str.lower()
    buys = df[text.str.startswith("purchase")]
    sales = int(text.str.startswith("sale").sum())
    if buys.empty:
        return {**empty, "sales": sales}
    insiders = buys["Insider"].dropna().unique().tolist()
    value = float(pd.to_numeric(buys.get("Value"), errors="coerce").fillna(0).sum())
    names = ", ".join(n.title() for n in insiders[:3]) + (" …" if len(insiders) > 3 else "")
    return {
        "hit": len(insiders) >= 2,
        "detail": f"{len(insiders)} insider(s) bought ${value / 1e6:,.2f}M in 90d ({names})",
        "strength": len(insiders) + min(value / 1e6, 5) / 5,
        "sales": sales,
    }


def volume_signal(m: dict) -> dict:
    ratio, move = m.get("vol_ratio"), m.get("ret_10d")
    if ratio is None or move is None:
        return {"hit": False, "detail": "No volume data", "strength": 0.0}
    hit = ratio >= 1.5 and abs(move) <= 0.05
    return {
        "hit": hit,
        "detail": f"5-day volume {ratio:.1f}× normal, price {move * 100:+.1f}% over 10d",
        "strength": ratio if hit else 0.0,
    }


def catalyst_signal(calendar: dict | None, today: date) -> dict:
    dates = (calendar or {}).get("Earnings Date") or []
    upcoming = sorted(d for d in dates if isinstance(d, date) and d >= today)
    if not upcoming:
        return {"hit": False, "detail": "No earnings date found", "date": None, "days": None, "strength": 0.0}
    when = upcoming[0]
    days = (when - today).days
    return {
        "hit": 1 <= days <= 92,
        "detail": f"Earnings {when:%b %d, %Y} (in {days} days)",
        "date": when.isoformat(),
        "days": days,
        "strength": 0.5,
    }


def strength_signal(m: dict, sector_ret: float | None, spy_ret: float | None, sector_etf: str | None) -> dict:
    ret = m.get("ret_1m")
    if ret is None:
        return {"hit": False, "detail": "Not enough history", "strength": 0.0}
    benchmark = sector_ret if sector_ret is not None else spy_ret
    label = sector_etf if sector_ret is not None else BENCHMARK
    if benchmark is None:
        return {"hit": False, "detail": f"1-month {ret * 100:+.1f}%", "strength": 0.0}
    gap = ret - benchmark
    hit = (gap >= 0.03 and (spy_ret is None or ret >= spy_ret)) or (benchmark < 0 and ret >= 0)
    return {
        "hit": hit,
        "detail": f"1-month {ret * 100:+.1f}% vs {label} {benchmark * 100:+.1f}%",
        "strength": max(gap, 0) * 10,
    }


def accumulation_signal(m: dict) -> dict:
    ratio = m.get("updown_ratio")
    if ratio is None:
        return {"hit": False, "detail": "No volume data", "strength": 0.0}
    hit = ratio >= 1.3
    return {
        "hit": hit,
        "detail": f"Up-day volume {ratio:.2f}× down-day volume over 50 days",
        "strength": ratio - 1 if hit else 0.0,
    }


def upgrades_signal(actions: pd.DataFrame | None, today: date, price: float) -> dict:
    empty = {"hit": False, "detail": "No analyst actions in 30d", "strength": 0.0}
    if actions is None or actions.empty:
        return empty
    recent = actions[pd.to_datetime(actions.index, errors="coerce") >= pd.Timestamp(today - timedelta(days=30))]
    if recent.empty:
        return empty
    action = recent.get("Action", pd.Series("", index=recent.index)).fillna("").str.lower()
    target = recent.get("priceTargetAction", pd.Series("", index=recent.index)).fillna("").str.lower()
    positive = recent[(action == "up") | (target == "raises")]
    negative = recent[(action == "down") | (target == "lowers")]
    firms = positive.get("Firm", pd.Series(dtype=str)).dropna().unique().tolist()
    parts = [f"{len(positive)} upgrade(s) / target raise(s), {len(negative)} cut(s) in 30d"]
    if firms:
        parts[0] += f" ({', '.join(firms[:3])}{' …' if len(firms) > 3 else ''})"
    targets = pd.to_numeric(recent.get("currentPriceTarget"), errors="coerce")
    targets = targets[targets > 0]
    if not targets.empty and price:
        avg = float(targets.mean())
        parts.append(f"avg target ${avg:,.0f} ({(avg / price - 1) * 100:+.0f}%)")
    hit = len(positive) >= 2 and len(positive) > len(negative)
    return {"hit": hit, "detail": " · ".join(parts), "strength": len(positive) / 5 if hit else 0.0}


def beats_signal(history: pd.DataFrame | None) -> dict:
    if history is None or history.empty or "surprisePercent" not in history:
        return {"hit": False, "detail": "No earnings history", "strength": 0.0}
    surprises = pd.to_numeric(history.sort_index()["surprisePercent"], errors="coerce").dropna().iloc[-4:]
    if surprises.empty:
        return {"hit": False, "detail": "No earnings history", "strength": 0.0}
    beats = int((surprises > 0).sum())
    hit = len(surprises) >= 4 and beats == len(surprises)
    return {
        "hit": hit,
        "detail": f"Beat EPS estimates {beats} of last {len(surprises)} quarters "
        f"(avg surprise {surprises.mean() * 100:+.1f}%)",
        "strength": min(float(surprises.mean()), 0.3) * 3 if hit else 0.0,
    }


def shorts_signal(info: dict) -> dict:
    now, before = _num(info.get("sharesShort")), _num(info.get("sharesShortPriorMonth"))
    if not now or not before:
        return {"hit": False, "detail": "No short-interest data", "strength": 0.0}
    change = now / before - 1
    detail = f"Short interest {change * 100:+.0f}% vs prior month"
    pct = _num(info.get("shortPercentOfFloat"))
    if pct is not None:
        detail += f" ({pct * 100:.1f}% of float)"
    hit = change <= -0.10
    return {"hit": hit, "detail": detail, "strength": -change if hit else 0.0}


def revenue_growth(income: pd.DataFrame | None, estimate: pd.DataFrame | None) -> tuple[float | None, float | None]:
    """(last reported quarter YoY, analysts' expected YoY for the current quarter).

    Year-over-year, not sequential, so seasonal businesses don't look like
    they're accelerating every holiday quarter.
    """
    last = None
    if income is not None and not income.empty and "Total Revenue" in income.index:
        revenue = income.loc["Total Revenue"].sort_index(ascending=False).astype(float)
        if len(revenue) >= 5 and revenue.iloc[4] > 0 and not pd.isna(revenue.iloc[0]):
            last = float(revenue.iloc[0] / revenue.iloc[4] - 1)
    expected = None
    if estimate is not None and not estimate.empty and "0q" in estimate.index:
        expected = _num(estimate.at["0q", "growth"])
    return last, expected


def value_signal(growth: tuple[float | None, float | None], forward_pe: float | None, peer_pe: float | None) -> dict:
    last, expected = growth
    accelerating = last is not None and expected is not None and expected > last > 0
    cheap = forward_pe is not None and forward_pe > 0 and peer_pe is not None and forward_pe < peer_pe
    parts = []
    if last is not None:
        parts.append(f"Revenue YoY {last * 100:+.1f}% last qtr")
        if expected is not None:
            parts[-1] += f" → {expected * 100:+.1f}% expected this qtr"
    if forward_pe and forward_pe > 0:
        peer = f" vs peers {peer_pe:.0f}" if peer_pe else ""
        parts.append(f"fwd P/E {forward_pe:.1f}{peer}")
    return {
        "hit": accelerating and cheap,
        "detail": " · ".join(parts) or "No fundamentals data",
        "strength": (expected - last) * 10 if accelerating and cheap else 0.0,
    }


def _fetch_fundamentals(symbol: str) -> dict:
    t = yf.Ticker(symbol)
    info = _safe(lambda: t.info, {})
    return {
        "info": info,
        "eps_trend": _safe(lambda: t.eps_trend),
        "eps_revisions": _safe(lambda: t.eps_revisions),
        "insiders": _safe(lambda: t.insider_transactions),
        "calendar": _safe(lambda: t.calendar, {}),
        "growth": revenue_growth(
            _safe(lambda: t.quarterly_income_stmt), _safe(lambda: t.revenue_estimate)
        ),
        "upgrades": _safe(lambda: t.upgrades_downgrades),
        "earnings_history": _safe(lambda: t.earnings_history),
    }


def biggest_risk(m: dict, f: dict, signals: dict) -> str:
    info = f["info"]
    days = signals["catalyst"].get("days")
    short = _num(info.get("shortPercentOfFloat"))
    forward_pe = _num(info.get("forwardPE"))
    beta = _num(info.get("beta"))
    if days is not None and 0 <= days <= 14:
        return f"Earnings in {days} days — the report can gap the stock either way."
    if signals["revisions"].get("cuts"):
        return "More analysts are cutting estimates than raising them."
    if signals["insiders"].get("sales", 0) >= 5 and not signals["insiders"]["hit"]:
        return f"Heavy insider selling: {signals['insiders']['sales']} sales in 90 days."
    if short is not None and short >= 0.15:
        return f"High short interest ({short * 100:.0f}% of float) — volatile in both directions."
    if m.get("sma200") and m["price"] < m["sma200"]:
        return f"Still below its 200-day average (${m['sma200']:.2f}) — long-term trend is down."
    if forward_pe is not None and forward_pe > 40:
        return f"Rich valuation (forward P/E {forward_pe:.0f}) leaves little room for a miss."
    if beta is not None and beta >= 1.8:
        return f"High beta ({beta:.1f}) — a market sell-off would hit it hard."
    if m.get("ret_1m") is not None and m["ret_1m"] > 0.15:
        return f"Already up {m['ret_1m'] * 100:.0f}% this month — some of the move may be priced in."
    return "Signals are early and can fade; a broad market pullback would override them."


def _ladder_label(frame: pd.DataFrame) -> dict | None:
    try:
        ladder = compute_ladder(compute_levels(frame))
    except Exception:
        return None
    return {"label": ladder["label"], "side": ladder["side"]}


# ---- Scan ----


@ttl_cache(seconds=3600, maxsize=1)
def scan() -> dict:
    today = datetime.now(timezone.utc).date()
    stocks = list(dict.fromkeys(UNIVERSE + WATCHLIST + _dynamic_symbols()))
    benchmarks = [BENCHMARK, *SECTOR_ETFS.values()]
    frames = _download(stocks + benchmarks)

    bench = {s: price_metrics(frames[s]) for s in benchmarks if s in frames}
    spy = bench.get(BENCHMARK, {})

    metrics, excluded_hot = {}, []
    for symbol in stocks:
        if symbol not in frames:
            continue
        m = price_metrics(frames[symbol])
        if m["price"] < MIN_PRICE or m["dollar_volume"] < MIN_DOLLAR_VOLUME:
            continue
        if m["ret_1m"] is not None and m["ret_1m"] > MAX_MONTH_GAIN:
            excluded_hot.append(symbol)
            continue
        metrics[symbol] = m

    candidates = sorted(metrics, key=lambda s: _prescore(metrics[s], spy), reverse=True)[:CANDIDATES]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fundamentals = dict(zip(candidates, pool.map(_fetch_fundamentals, candidates)))

    # Peer P/E: median forward P/E per sector among the candidates
    by_sector: dict[str, list[float]] = {}
    for f in fundamentals.values():
        pe = _num(f["info"].get("forwardPE"))
        if pe and 0 < pe < 200:
            by_sector.setdefault(f["info"].get("sector") or "", []).append(pe)
    all_pe = [pe for pes in by_sector.values() for pe in pes]
    overall_pe = statistics.median(all_pe) if all_pe else None

    results = []
    for symbol in candidates:
        m, f = metrics[symbol], fundamentals[symbol]
        info = f["info"]
        sector = info.get("sector")
        etf = SECTOR_ETFS.get(sector or "")
        peers = by_sector.get(sector or "", [])
        peer_pe = statistics.median(peers) if len(peers) >= 3 else overall_pe
        signals = {
            "revisions": revisions_signal(f["eps_trend"], f["eps_revisions"]),
            "insiders": insider_signal(f["insiders"], today),
            "volume": volume_signal(m),
            "catalyst": catalyst_signal(f["calendar"], today),
            "strength": strength_signal(m, bench.get(etf, {}).get("ret_1m"), spy.get("ret_1m"), etf),
            "value": value_signal(f["growth"], _num(info.get("forwardPE")), peer_pe),
            "accumulation": accumulation_signal(m),
            "upgrades": upgrades_signal(f.get("upgrades"), today, m["price"]),
            "beats": beats_signal(f.get("earnings_history")),
            "shorts": shorts_signal(info),
        }
        count = sum(s["hit"] for s in signals.values())
        points = sum(WEIGHTS.get(k, 1.0) for k, s in signals.items() if s["hit"])
        results.append({
            "symbol": symbol,
            "name": info.get("shortName") or info.get("longName") or symbol,
            "sector": sector,
            "price": m["price"],
            "ret_1m": m["ret_1m"],
            "signal_count": count,
            "points": points,
            # Strength only breaks ties: capped below the catalyst's half point
            "score": round(points + min(sum(s["strength"] for s in signals.values() if s["hit"]) / 20, 0.49), 3),
            "signals": {k: {"hit": s["hit"], "detail": s["detail"]} for k, s in signals.items()},
            "catalyst": signals["catalyst"]["detail"] if signals["catalyst"]["hit"] else None,
            "catalyst_date": signals["catalyst"]["date"],
            "risk": biggest_risk(m, f, signals),
            "ladder": _ladder_label(frames[symbol]),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signals": [{"key": k, "label": label} for k, label in SIGNALS],
        "top": results[:TOP_N],
        "scanned": len(metrics) + len(excluded_hot),
        "deep_checked": len(candidates),
        "excluded_hot": sorted(excluded_hot),
    }
