"""Signal ladder + investment system: the right price, and the right day.

The ladder turns the computed levels into six price rungs, from TRIM MORE down
to BUY A LOT, and says which rung today's price sits on. The system then asks
two questions before acting on it:

  * Is today the right day?  (market, chasing, RSI, earnings, volume)
  * Is the ladder right?     (trend, stop, risk/reward, fresh data)

Pure functions over the dict compute_levels() returns — educational, not advice.
"""

from __future__ import annotations

from datetime import date

RUNGS = [
    ("trim_more", "TRIM MORE", "sell"),
    ("trim", "TRIM", "sell"),
    ("hold", "HOLD", "hold"),
    ("buy_little", "BUY A LITTLE", "buy"),
    ("buy", "BUY", "buy"),
    ("buy_lot", "BUY A LOT", "buy"),
]

# Days ahead an earnings report counts as "too close" to open a position
EARNINGS_BLACKOUT_DAYS = 5
CHASE_PCT = 3.0
# Rung width: this many ATRs, but never narrower than this share of the price
STEP_ATR = 2.0
MIN_STEP_PCT = 0.02


def compute_ladder(levels: dict) -> dict:
    """Six rungs with price ranges (top to bottom) and the rung price sits on.

    Rungs are centred on the trend (SMA50, else SMA20) and spaced by the stock's
    own volatility, so a calm stock and a wild one both get sensible bands:
    HOLD is within one step of the trend, each step further out moves one rung.
    """
    price = levels["price"]
    anchor = levels.get("sma50") or levels["sma20"]
    step = max(STEP_ATR * levels["atr14"], MIN_STEP_PCT * price)

    # Boundaries bottom to top: BUY A LOT | BUY | BUY A LITTLE | HOLD | TRIM | TRIM MORE
    bounds = [round(anchor + k * step, 2) for k in (-3, -2, -1, 1, 2)]
    edges = [None, *bounds, None]
    rungs = []
    for i, (key, label, side) in enumerate(reversed(RUNGS)):
        rungs.append({"key": key, "label": label, "side": side, "low": edges[i], "high": edges[i + 1]})
    rungs.reverse()  # top first, like the picture

    current = next(r for r in reversed(rungs) if r["high"] is None or price < r["high"])
    return {
        "rungs": rungs,
        "current": current["key"],
        "label": current["label"],
        "side": current["side"],
        "anchor": round(anchor, 2),
        "anchor_name": "SMA50" if levels.get("sma50") else "SMA20",
        "step": round(step, 2),
        "below_stop": price < levels["stop_loss"],
    }


def _check(ok: bool | None, label: str, detail: str) -> dict:
    return {"ok": ok, "label": label, "detail": detail}


def day_checks(
    levels: dict,
    side: str,
    market: dict | None,
    earnings_date: date | None,
    today: date,
) -> list[dict]:
    checks = []
    if market is not None:
        spy_up = market["price"] > (market.get("sma50") or market["price"])
        change = market.get("change_1d_pct")
        calm = change is None or change > -1.5
        checks.append(_check(
            spy_up and calm,
            "Market backdrop",
            f"SPY {'above' if spy_up else 'below'} its 50-day average"
            + (f", {change:+.1f}% today" if change is not None else ""),
        ))

    change = levels.get("change_1d_pct")
    if change is not None:
        if side == "sell":
            checks.append(_check(change >= 0, "Selling into strength", f"{change:+.1f}% today"))
        else:
            checks.append(_check(change < CHASE_PCT, "Not chasing a spike", f"{change:+.1f}% today"))

    rsi = levels.get("rsi14")
    if rsi is not None:
        if side == "sell":
            checks.append(_check(rsi >= 60, "RSI stretched", f"RSI {rsi:.0f} (trim best above 60)"))
        else:
            checks.append(_check(rsi < 70, "RSI not overbought", f"RSI {rsi:.0f}"))

    if earnings_date is not None:
        days = (earnings_date - today).days
        if 0 <= days <= EARNINGS_BLACKOUT_DAYS:
            checks.append(_check(False, "No earnings this week", f"Earnings in {days} day(s) — {earnings_date:%b %d}"))
        elif days > EARNINGS_BLACKOUT_DAYS:
            checks.append(_check(True, "No earnings this week", f"Next earnings {earnings_date:%b %d} (in {days} days)"))

    ratio = levels.get("volume_ratio")
    if ratio is not None and change is not None:
        distribution = ratio >= 1.5 and change < 0
        checks.append(_check(
            not distribution or side == "sell",
            "No heavy selling today",
            f"Volume {ratio:.1f}× average on a {change:+.1f}% day",
        ))
    return checks


def ladder_checks(levels: dict, ladder: dict, last_bar: date | None, today: date) -> list[dict]:
    checks = []
    if levels.get("sma200"):
        above = levels["price"] > levels["sma200"]
        checks.append(_check(
            above,
            "Long-term trend up",
            f"Price {'above' if above else 'below'} SMA200 ${levels['sma200']:,.2f}"
            + ("" if above else " — buy rungs may be catching a falling knife"),
        ))
    checks.append(_check(
        not ladder["below_stop"],
        "Above the stop",
        f"Stop ${levels['stop_loss']:,.2f}" + (" — price broke it, levels need to reset" if ladder["below_stop"] else ""),
    ))
    rr = levels.get("risk_reward")
    if rr is not None:
        checks.append(_check(rr >= 1.5, "Buy zone pays for the risk", f"Risk/reward {rr}:1 (want ≥ 1.5)"))
    if last_bar is not None:
        age = (today - last_bar).days
        checks.append(_check(age <= 4, "Fresh prices", f"Last bar {last_bar:%b %d}"))
    return checks


def _verdict(checks: list[dict], side: str) -> dict:
    failed = sum(1 for c in checks if c["ok"] is False)
    if side == "hold":
        return {"status": "hold", "text": "Hold zone — no action needed today"}
    action = "trim" if side == "sell" else "buy"
    if failed == 0:
        return {"status": "go", "text": f"Good day to {action}"}
    if failed == 1:
        return {"status": "caution", "text": f"OK to {action} a smaller amount"}
    return {"status": "wait", "text": f"Wait — {failed} checks failing"}


def compute_system(
    levels: dict,
    *,
    market: dict | None = None,
    earnings_date: date | None = None,
    last_bar: date | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    ladder = compute_ladder(levels)
    day = day_checks(levels, ladder["side"], market, earnings_date, today)
    trust = ladder_checks(levels, ladder, last_bar, today)
    trust_failed = sum(1 for c in trust if c["ok"] is False)
    return {
        "ladder": ladder,
        "day": {"checks": day, **_verdict(day, ladder["side"])},
        "ladder_check": {
            "checks": trust,
            "status": "go" if trust_failed == 0 else ("caution" if trust_failed == 1 else "wait"),
            "text": "Ladder looks right" if trust_failed == 0 else f"Review the ladder — {trust_failed} issue(s)",
        },
    }
