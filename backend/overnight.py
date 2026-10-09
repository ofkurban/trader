"""Overnight Asia / Korea signals that often lead US semis at the open."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yfinance as yf

from backend.cache import ttl_cache
from backend.news import fetch_chip_news

# KOSDAQ = Korea tech-heavy market (what people often call "kosiq")
# KOSPI  = broad Korea market
ASIA_PEERS = [
    {"id": "kosdaq", "symbol": "^KQ11", "label": "KOSDAQ", "note": "Korea tech index"},
    {"id": "kospi", "symbol": "^KS11", "label": "KOSPI", "note": "Korea broad market"},
    {"id": "sk_hynix", "symbol": "000660.KS", "label": "SK Hynix", "note": "MU peer (memory)"},
    {"id": "samsung", "symbol": "005930.KS", "label": "Samsung", "note": "Korea chip giant"},
    {
        "id": "cxmt",
        "symbol": "688825.SS",
        "label": "CXMT",
        "note": "China memory (Shanghai) — supply/ASP risk",
    },
    {"id": "sox", "symbol": "^SOX", "label": "SOX", "note": "US semiconductor index"},
    {"id": "nq_fut", "symbol": "NQ=F", "label": "Nasdaq fut", "note": "US tech overnight tone"},
]


def _safe_float(value: Any, digits: int = 2) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, digits)


@ttl_cache(seconds=120)
def _one_day_change(symbol: str) -> dict:
    hist = yf.Ticker(symbol).history(period="10d")
    if hist.empty or len(hist) < 2:
        return {"error": "no data"}

    close = hist["Close"].dropna()
    if len(close) < 2:
        return {"error": "no data"}

    last = _safe_float(close.iloc[-1])
    prev = _safe_float(close.iloc[-2])
    if last is None or prev is None or prev == 0:
        return {"error": "invalid price"}

    change_pct = _safe_float((last - prev) / prev * 100)
    change = _safe_float(last - prev)

    return {
        "price": last,
        "change_1d_pct": change_pct,
        "change_1d": change,
        "as_of": hist.index[-1].strftime("%Y-%m-%d"),
    }


def _score_bias(items: list[dict]) -> dict:
    """Simple overnight bias from Korea peers + futures (educational)."""
    score = 0
    reasons: list[str] = []

    weights = {
        "sk_hynix": 2,
        "samsung": 1,
        "cxmt": 1.5,
        "kosdaq": 1,
        "kospi": 0.5,
        "sox": 1.5,
        "nq_fut": 1.5,
    }

    for item in items:
        if item.get("error") or item.get("change_1d_pct") is None:
            continue
        chg = item["change_1d_pct"]
        w = weights.get(item["id"], 1)
        if chg <= -2:
            score -= w
            reasons.append(f"{item['label']} weak ({chg:+.1f}%)")
        elif chg >= 2:
            score += w
            reasons.append(f"{item['label']} strong ({chg:+.1f}%)")
        elif chg <= -0.5:
            score -= 0.5 * w
        elif chg >= 0.5:
            score += 0.5 * w

    if score >= 2:
        bias = "RISK-ON"
        summary = "Overnight Asia/tech tone leans bullish for US semis at the open."
    elif score <= -2:
        bias = "RISK-OFF"
        summary = "Overnight Asia/tech tone leans bearish — watch for a weak US semi open."
    else:
        bias = "MIXED"
        summary = "Overnight signals are mixed — wait for US open / premarket confirmation."

    return {
        "bias": bias,
        "score": round(score, 2),
        "summary": summary,
        "reasons": reasons[:5],
    }


def fetch_overnight_signals() -> dict:
    def peer_row(peer: dict) -> dict:
        row = {
            "id": peer["id"],
            "symbol": peer["symbol"],
            "label": peer["label"],
            "note": peer["note"],
        }
        try:
            row.update(_one_day_change(peer["symbol"]))
        except Exception as exc:
            row["error"] = str(exc)
        return row

    with ThreadPoolExecutor(max_workers=len(ASIA_PEERS)) as pool:
        items = list(pool.map(peer_row, ASIA_PEERS))

    bias = _score_bias(items)
    chip_news = fetch_chip_news(limit=10)

    # China memory-supply headlines (CXMT etc.) = ASP pressure risk for MU
    if chip_news.get("china_memory_count", 0) >= 1:
        extra = (
            " China/CXMT memory-supply headlines are active — watch ASP pressure on MU."
        )
        if bias["bias"] == "RISK-ON":
            bias = {
                **bias,
                "bias": "MIXED",
                "summary": "Firm price tone, but China memory-supply news raises ASP risk." + extra,
            }
        else:
            bias = {**bias, "summary": bias["summary"] + extra}

    # If lots of sell-off headlines, nudge bias toward risk-off
    if chip_news.get("selloff_count", 0) >= 4 and bias["bias"] == "MIXED":
        bias = {
            **bias,
            "bias": "RISK-OFF",
            "summary": (
                bias["summary"]
                + " Chip sell-off headlines are elevated — extra caution for MU/semis."
            ),
        }
    elif chip_news.get("tone") == "CHIP STRESS" and bias["bias"] == "RISK-ON":
        bias = {
            **bias,
            "bias": "MIXED",
            "summary": (
                "Price tone is firm but chip sell-off headlines are loud — treat as mixed."
            ),
        }

    return {
        "items": items,
        **bias,
        "chip_news": chip_news,
        "tip": (
            "Watch KOSDAQ/SK Hynix and China memory (CXMT 688825.SS): expanding Chinese "
            "DRAM supply can pressure chip prices and weigh on MU. Not a prediction."
        ),
    }
