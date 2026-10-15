"""Trade journal review: live trailing stops for open trades, stats for closed ones.

The journal itself lives in the browser (the free Render plan has no disk);
the server only does the math, using the same rules as the backtest so real
results compare directly with it.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backend.backtest import _drop_open_session, indicator_frame, summarize
from backend.levels import MARKET_TZ, TRAIL_ATR

# A loss beyond this many R means the stop wasn't honored (allows for slippage/gaps)
BLOWN_STOP_R = -1.2


def _bar_dates(index: pd.DatetimeIndex) -> list[date]:
    if index.tz is not None:
        index = index.tz_convert(MARKET_TZ)
    return [ts.date() for ts in index]


def trail_status(
    hist: pd.DataFrame,
    *,
    entry_date: date,
    entry_price: float,
    initial_stop: float,
    trail_atr: float = TRAIL_ATR,
) -> dict:
    """Where the 4x ATR trailing stop stands now, replaying bars since entry.

    Mirrors backtest.simulate: the stop only moves up, is raised after each
    completed close (highest close since entry minus trail_atr ATRs), and a
    bar whose low reaches the stop in effect that day counts as a hit.
    Today's unfinished bar can trigger a hit but never raises the stop.
    """
    risk = entry_price - initial_stop
    if risk <= 0:
        raise ValueError("Stop must be below the entry price")
    complete = _drop_open_session(hist)
    atr = indicator_frame(hist)["atr14"].to_numpy()
    dates = _bar_dates(hist.index)
    lows, closes = hist["Low"].to_numpy(), hist["Close"].to_numpy()

    stop, peak, hit = initial_stop, entry_price, None
    for i, day in enumerate(dates):
        if day < entry_date:
            continue
        if day > entry_date and hit is None and lows[i] <= stop:
            hit = {"date": day.isoformat(), "stop": round(stop, 2)}
        if i < len(complete) and not np.isnan(atr[i]):
            peak = max(peak, float(closes[i]))
            stop = max(stop, peak - trail_atr * float(atr[i]))

    price = float(closes[-1])
    return {
        "price": round(price, 2),
        "trail_stop": round(stop, 2),
        "peak_close": round(peak, 2),
        "atr14": round(float(atr[-1]), 2) if not np.isnan(atr[-1]) else None,
        "open_r": round((price - entry_price) / risk, 2),
        "locked_r": round((stop - entry_price) / risk, 2),
        "stop_hit": hit,
    }


def _trade_r(t: dict) -> float:
    return (t["exit_price"] - t["entry_price"]) / (t["entry_price"] - t["initial_stop"])


def review_closed(closed: list[dict]) -> dict:
    """Backtest-style stats on closed trades, plus how well the rules were followed."""
    trades = [
        {
            "r": round(_trade_r(t), 3),
            "reason": "closed",
            "exit_date": t["exit_date"],
            "days": max((date.fromisoformat(t["exit_date"]) - date.fromisoformat(t["entry_date"])).days, 0),
            "pnl": (t["exit_price"] - t["entry_price"]) * t["shares"],
            "on_signal": t.get("signal_action") == "BUY",
        }
        for t in closed
    ]
    stats = summarize(trades)
    if trades:
        stats["avg_days"] = round(float(np.mean([t["days"] for t in trades])), 1)  # calendar days here
        stats["total_pnl"] = round(sum(t["pnl"] for t in trades), 2)
        stats.pop("exits", None)

    on = [t for t in trades if t["on_signal"]]
    off = [t for t in trades if not t["on_signal"]]
    blown = [t for t in trades if t["r"] < BLOWN_STOP_R]
    discipline = {
        "on_signal_pct": round(len(on) / len(trades) * 100, 1) if trades else None,
        "on_signal": summarize(on) if on else {"trades": 0},
        "off_signal": summarize(off) if off else {"trades": 0},
        "blown_stops": len(blown),
        "blown_stop_r": round(float(sum(t["r"] - -1 for t in blown)), 2) if blown else 0.0,
    }
    return {"stats": stats, "discipline": discipline}
