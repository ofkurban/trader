"""Semi / memory Greedy Risk Index (0–100), Fear & Greed style — educational only."""

from __future__ import annotations

from typing import Any


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _label(score: float) -> str:
    if score < 20:
        return "EXTREME FEAR"
    if score < 40:
        return "FEAR"
    if score < 60:
        return "NEUTRAL"
    if score < 80:
        return "GREED"
    return "EXTREME GREED"


def _hint(label: str) -> str:
    return {
        "EXTREME FEAR": "Panic zone — historically better for patient buyers, not chase shorts.",
        "FEAR": "Risk-off tone — wait for support / oversold; size carefully.",
        "NEUTRAL": "Balanced — trade levels, not emotion.",
        "GREED": "Hot tape — trail stops; avoid FOMO adds.",
        "EXTREME GREED": "Crowded long risk — consider taking profits / tighter risk.",
    }.get(label, "")


def _component(name: str, score: float, detail: str) -> dict:
    return {
        "name": name,
        "score": round(_clamp(score), 1),
        "detail": detail,
    }


def compute_greedy_risk_index(
    watchlist_items: list[dict],
    overnight: dict | None = None,
) -> dict[str, Any]:
    """
    Build 0–100 greed index from watchlist technicals + overnight Asia/news.

    Higher = more greed / risk-on. Lower = fear / risk-off.
    """
    overnight = overnight or {}
    components: list[dict] = []
    valid = [i for i in watchlist_items if not i.get("error") and i.get("price") is not None]

    # 1) Momentum — average 1D / 5D change
    d1 = [i["change_1d_pct"] for i in valid if i.get("change_1d_pct") is not None]
    d5 = [i["change_5d_pct"] for i in valid if i.get("change_5d_pct") is not None]
    avg_1d = sum(d1) / len(d1) if d1 else 0.0
    avg_5d = sum(d5) / len(d5) if d5 else 0.0
    # Map ~[-8% .. +8%] → 0..100
    mom_score = _clamp(50 + avg_1d * 4 + avg_5d * 1.5)
    components.append(
        _component(
            "Watchlist momentum",
            mom_score,
            f"Avg 1D {avg_1d:+.2f}% · 5D {avg_5d:+.2f}%",
        )
    )

    # 2) RSI — average RSI14
    rsis = [i["rsi14"] for i in valid if i.get("rsi14") is not None]
    avg_rsi = sum(rsis) / len(rsis) if rsis else 50.0
    components.append(
        _component("RSI (watchlist)", avg_rsi, f"Avg RSI14 {avg_rsi:.1f}")
    )

    # 3) Trend breadth — % above SMA20 / SMA50 / SMA200
    def breadth(key: str) -> float:
        vals = [i.get(key) for i in valid]
        known = [v for v in vals if v in ("above", "below")]
        if not known:
            return 50.0
        return 100.0 * sum(1 for v in known if v == "above") / len(known)

    b20 = breadth("trend_vs_sma20")
    b50 = breadth("trend_vs_sma50")
    b200 = breadth("trend_vs_sma200")
    breadth_score = 0.45 * b20 + 0.35 * b50 + 0.20 * b200
    components.append(
        _component(
            "Trend breadth",
            breadth_score,
            f"Above SMA20 {b20:.0f}% · SMA50 {b50:.0f}% · SMA200 {b200:.0f}%",
        )
    )

    # 4) Recommendation mix — BUY vs SELL
    recs = [
        (i.get("recommendation") or {}).get("action", "HOLD")
        for i in valid
    ]
    if recs:
        buy_n = sum(1 for r in recs if r == "BUY")
        sell_n = sum(1 for r in recs if r == "SELL")
        hold_n = len(recs) - buy_n - sell_n
        rec_score = _clamp(50 + (buy_n - sell_n) * (50 / max(len(recs), 1)))
        components.append(
            _component(
                "Buy/Sell mix",
                rec_score,
                f"BUY {buy_n} · HOLD {hold_n} · SELL {sell_n}",
            )
        )

    # 5) Overnight Asia bias score → 0..100
    ov_score_raw = overnight.get("score")
    if ov_score_raw is not None:
        # overnight score roughly in [-6 .. +6]
        ov_mapped = _clamp(50 + float(ov_score_raw) * 8)
        components.append(
            _component(
                "Overnight Asia",
                ov_mapped,
                f"{overnight.get('bias', 'MIXED')} (score {ov_score_raw})",
            )
        )

    # 6) News stress — chip sell-off / China memory lower greed
    chip = overnight.get("chip_news") or {}
    selloff_n = int(chip.get("selloff_count") or 0)
    china_n = int(chip.get("china_memory_count") or 0)
    news_score = _clamp(70 - selloff_n * 4 - china_n * 5)
    components.append(
        _component(
            "Chip news risk",
            news_score,
            f"Sell-off headlines {selloff_n} · China memory {china_n}",
        )
    )

    # 7) Volume heat — high volume on down days = fear; on up days = greed
    vol_points = []
    for i in valid:
        ratio = i.get("volume_ratio")
        d1i = i.get("change_1d_pct")
        if ratio is None or d1i is None:
            continue
        if ratio >= 1.2 and d1i < 0:
            vol_points.append(30)
        elif ratio >= 1.2 and d1i > 0:
            vol_points.append(75)
        else:
            vol_points.append(50)
    if vol_points:
        vol_score = sum(vol_points) / len(vol_points)
        components.append(
            _component("Volume heat", vol_score, "High volume up=greed, down=fear")
        )

    if not components:
        return {
            "score": 50,
            "label": "NEUTRAL",
            "hint": _hint("NEUTRAL"),
            "components": [],
        }

    # Weighted blend
    weights = {
        "Watchlist momentum": 0.22,
        "RSI (watchlist)": 0.18,
        "Trend breadth": 0.18,
        "Buy/Sell mix": 0.10,
        "Overnight Asia": 0.16,
        "Chip news risk": 0.10,
        "Volume heat": 0.06,
    }
    total_w = 0.0
    total = 0.0
    for c in components:
        w = weights.get(c["name"], 0.1)
        total += c["score"] * w
        total_w += w

    score = round(total / total_w if total_w else 50.0, 1)
    label = _label(score)

    return {
        "score": score,
        "label": label,
        "hint": _hint(label),
        "components": components,
        "disclaimer": "Educational composite for this semis watchlist — not CNN Fear & Greed and not financial advice.",
    }
