"""Analyst track records: which firms' past calls on a stock were right.

Yahoo lists every rating change and price target since ~2012 but no accuracy,
so we grade each call that is at least a year old against what the stock did
next:

- correct: a Buy-type call beat SPY over the next 12 months, a Sell-type call
  lagged it. Measuring against SPY keeps a bull market from making every
  bullish firm look right. Hold-type calls have no direction and aren't graded.
- target hit: the price reached the firm's target within those 12 months.

Firms are scored per stock (a firm good on MU may be poor on PLTR), and the
current call and target come from each firm's latest rating.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON_DAYS = 365
MIN_ACCURACY = 0.8
MIN_CALLS = 5
# A firm's latest rating older than this isn't treated as a current view
ACTIVE_DAYS = 365

BULLISH = {
    "buy", "strong buy", "outperform", "overweight", "positive", "accumulate",
    "sector outperform", "market outperform", "add", "top pick", "long-term buy",
    "speculative buy", "conviction buy",
}
BEARISH = {
    "sell", "strong sell", "underperform", "underweight", "reduce", "negative",
    "sector underperform", "market underperform",
}


def _naive_days(index) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        index = index.tz_localize(None)
    return index.normalize()


def side_of(grade: str) -> int:
    """+1 bullish, -1 bearish, 0 neutral/unknown."""
    grade = (grade or "").strip().lower()
    return 1 if grade in BULLISH else -1 if grade in BEARISH else 0


def prepare_ratings(ratings: pd.DataFrame, splits: pd.Series | None = None) -> pd.DataFrame:
    """Oldest first, one row per firm per day, targets in today's split-adjusted dollars."""
    # Sort on the full timestamp so "last of the day" is the latest call
    df = ratings.sort_index(kind="stable")
    df.index = _naive_days(df.index)
    df = df.reset_index(names="date").drop_duplicates(["date", "Firm"], keep="last")

    target = pd.to_numeric(df.get("currentPriceTarget"), errors="coerce")
    target = target.where(target > 0)
    if splits is not None and len(splits):
        split_days = _naive_days(splits.index)
        for day, ratio in zip(split_days, splits.to_numpy(dtype=float)):
            if ratio > 0:
                target = target.where(df["date"] >= day, target / ratio)
    df["target"] = target
    df["side"] = [side_of(g) for g in df["ToGrade"].astype(str)]
    return df.reset_index(drop=True)


def grade_calls(
    ratings: pd.DataFrame, hist: pd.DataFrame, spy_close: pd.Series, horizon_days: int = HORIZON_DAYS
) -> pd.DataFrame:
    """One row per call whose 12-month window has fully played out."""
    days = _naive_days(hist.index)
    close = hist["Close"].to_numpy(dtype=float)
    high = hist["High"].to_numpy(dtype=float)
    low = hist["Low"].to_numpy(dtype=float)
    spy = pd.Series(spy_close.to_numpy(dtype=float), index=_naive_days(spy_close.index))
    spy = spy[~spy.index.duplicated(keep="last")].reindex(days, method="ffill").to_numpy()

    entry = days.searchsorted(ratings["date"].to_numpy())
    exit_ = days.searchsorted((ratings["date"] + pd.Timedelta(days=horizon_days)).to_numpy())
    ok = exit_ < len(days)
    calls = ratings[ok].copy()
    i, j = entry[ok], exit_[ok]

    calls["entry_price"] = close[i]
    calls["ret"] = close[j] / close[i] - 1
    with np.errstate(invalid="ignore", divide="ignore"):
        calls["spy_ret"] = spy[j] / spy[i] - 1
    calls["excess"] = calls["side"] * (calls["ret"] - calls["spy_ret"])
    calls["correct"] = np.where(
        (calls["side"] != 0) & calls["spy_ret"].notna(), (calls["excess"] > 0).astype(float), np.nan
    )

    hit = np.full(len(calls), np.nan)
    targets = calls["target"].to_numpy(dtype=float)
    for k, (a, b, t, p0) in enumerate(zip(i, j, targets, calls["entry_price"].to_numpy())):
        if np.isnan(t):
            continue
        hit[k] = float(high[a : b + 1].max() >= t) if t >= p0 else float(low[a : b + 1].min() <= t)
    calls["target_hit"] = hit
    return calls


def _pct(x) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x) * 100, 1)


def _num(x, nd=2) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), nd)


def analyst_report(
    ratings: pd.DataFrame,
    hist: pd.DataFrame,
    spy_close: pd.Series,
    splits: pd.Series | None = None,
    *,
    min_accuracy: float = MIN_ACCURACY,
    min_calls: int = MIN_CALLS,
) -> dict:
    """Firms with >= min_accuracy correct calls (and >= min_calls graded), with their current view."""
    rated = prepare_ratings(ratings, splits)
    calls = grade_calls(rated, hist, spy_close)
    last_close = float(hist["Close"].iloc[-1])
    last_day = _naive_days(hist.index)[-1]

    graded = calls[calls["correct"].notna()]
    stats = graded.groupby("Firm").agg(
        calls=("correct", "size"), accuracy=("correct", "mean"), avg_excess=("excess", "mean")
    )
    targets = calls[calls["target_hit"].notna()].groupby("Firm")["target_hit"].agg(["size", "mean"])
    stats = stats.join(targets.rename(columns={"size": "target_calls", "mean": "target_hit_rate"}))

    latest = rated.groupby("Firm").tail(1).set_index("Firm")
    last_target = rated.dropna(subset=["target"]).groupby("Firm").tail(1).set_index("Firm")

    firms = []
    for firm, s in stats.iterrows():
        cur = latest.loc[firm]
        target_row = last_target.loc[firm] if firm in last_target.index else None
        target = target_row["target"] if target_row is not None else None
        age = (last_day - cur["date"]).days
        firms.append({
            "firm": firm,
            "calls": int(s["calls"]),
            "accuracy_pct": _pct(s["accuracy"]),
            "avg_excess_pct": _pct(s["avg_excess"]),
            "target_calls": int(s["target_calls"]) if pd.notna(s["target_calls"]) else 0,
            "target_hit_pct": _pct(s["target_hit_rate"]),
            "rating": cur["ToGrade"],
            "side": int(cur["side"]),
            "action": cur.get("Action"),
            "rating_date": cur["date"].date().isoformat(),
            "target": _num(target),
            "target_date": target_row["date"].date().isoformat() if target_row is not None else None,
            "upside_pct": _pct(target / last_close - 1) if target else None,
            "active": age <= ACTIVE_DAYS,
        })
    firms.sort(key=lambda f: (-f["accuracy_pct"], -f["calls"]))

    top = [
        f for f in firms
        if f["active"] and f["calls"] >= min_calls and f["accuracy_pct"] >= min_accuracy * 100
    ]
    top_targets = [f["target"] for f in top if f["target"]]
    avg_target = float(np.mean(top_targets)) if top_targets else None
    return {
        "price": _num(last_close),
        "min_accuracy_pct": _pct(min_accuracy),
        "min_calls": min_calls,
        "horizon_days": HORIZON_DAYS,
        "graded_calls": int(len(graded)),
        # How often a blind Buy on the same dates beat SPY: when this is high,
        # 80%+ firms may just have ridden the stock rather than shown skill
        "buy_base_rate_pct": _pct((graded["ret"] > graded["spy_ret"]).mean()) if len(graded) else None,
        "firms_graded": int((stats["calls"] >= min_calls).sum()),
        "top": top,
        "consensus": {
            "buy": sum(f["side"] > 0 for f in top),
            "hold": sum(f["side"] == 0 for f in top),
            "sell": sum(f["side"] < 0 for f in top),
            "avg_target": _num(avg_target),
            "upside_pct": _pct(avg_target / last_close - 1) if avg_target else None,
        },
        "others": [f for f in firms if f not in top and f["active"] and f["calls"] >= min_calls],
    }
