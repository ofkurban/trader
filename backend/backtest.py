"""Backtest the app's BUY/HOLD/SELL score and suggested levels on daily bars.

For every past day the same indicators as compute_levels() are rebuilt from
data up to that day's close (no look-ahead), the same compute_recommendation()
scores them, and trades are simulated from the signal:

  * entry "buy_zone": a limit order at buy_high, valid for `entry_window`
    days after the signal; a gap below the limit fills at the open, a gap
    below the stop cancels the order.
  * entry "next_open": buy at the next day's open.

Each trade exits at the stop, the take profit, or after `max_hold` days at the
close. With exit "trail" there is no take profit: after each close the stop
rises to the highest close since entry minus `trail_atr` ATRs (never lower),
so winners run until the trend breaks. When stop and target are both inside one day's range the stop is
assumed to hit first, and on the fill day only the stop is checked —
both pessimistic, because daily bars hide the intraday order.

Results are in R (profit / initial risk) so different stocks compare fairly.
The universe is today's list of stocks, so delisted losers are missing
(survivorship bias) — treat results as optimistic.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pandas as pd

from backend.levels import (
    TRAIL_ATR,
    TP_MIN_ATR_ROOM,
    TP_PROJECTION_ATR,
    _open_session_fraction,
    compute_recommendation,
    compute_rsi_series,
    relative_strength_series,
)

EntryMode = Literal["buy_zone", "next_open"]
ExitMode = Literal["target", "trail"]

WARMUP = 200  # bars before the first signal, so SMA200 exists like in the app
FORWARD_DAYS = (5, 20, 60)


def indicator_frame(hist: pd.DataFrame, benchmark: pd.Series | None = None) -> pd.DataFrame:
    """compute_levels() for every bar at once, using only data up to that bar."""
    close, high, low, volume = hist["Close"], hist["High"], hist["Low"], hist["Volume"]

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi = compute_rsi_series(close)
    vol_avg20 = volume.rolling(20).mean()
    support = low.rolling(60, min_periods=1).min()
    resistance = high.rolling(60, min_periods=1).max()
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(14).mean()

    buy_low = np.minimum(support, sma20 * 0.98).round(2)
    buy_high = sma20.round(2)
    entry_mid = (buy_low + buy_high) / 2
    near_high = (resistance - close) < TP_MIN_ATR_ROOM * atr
    take_profit = pd.Series(
        np.where(near_high, close + TP_PROJECTION_ATR * atr, resistance), index=hist.index
    ).round(2)
    stop_loss = np.minimum(entry_mid - 1.5 * atr, support - 0.5 * atr).round(2)
    risk_now = close - stop_loss
    rr_now = ((take_profit - close) / risk_now).where(risk_now > 0).round(2)

    return pd.DataFrame(
        {
            "open": hist["Open"],
            "high": high,
            "low": low,
            "close": close,
            "sma20": sma20,
            "sma50": sma50,
            "sma200": sma200,
            "rsi14": rsi.round(1),
            "volume_ratio": (volume / vol_avg20).where(vol_avg20 > 0).round(2),
            "support": support,
            "resistance": resistance,
            "atr14": atr,
            "buy_low": buy_low,
            "buy_high": buy_high,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "risk_reward_now": rr_now,
            "rel_strength": (
                relative_strength_series(close, benchmark).round(1)
                if benchmark is not None
                else np.nan
            ),
        },
        index=hist.index,
    )


def _num(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def score_frame(frame: pd.DataFrame, recommend=compute_recommendation) -> pd.DataFrame:
    """Add action/score columns from the app's own compute_recommendation()."""
    actions, scores = [], []
    for row in frame.itertuples():
        if pd.isna(row.sma20) or pd.isna(row.atr14):
            actions.append(None)
            scores.append(None)
            continue
        rec = recommend(
            last=row.close,
            sma20=row.sma20,
            sma50=_num(row.sma50),
            sma200=_num(row.sma200),
            rsi14=_num(row.rsi14),
            volume_ratio=_num(row.volume_ratio),
            support=row.support,
            resistance=row.resistance,
            risk_reward=_num(row.risk_reward_now),
            rel_strength=_num(row.rel_strength),
        )
        actions.append(rec["action"])
        scores.append(rec["score"])
    return frame.assign(action=actions, score=scores)


def _drop_open_session(hist: pd.DataFrame) -> pd.DataFrame:
    """Today's unfinished bar would be scored on partial volume; leave it out."""
    if len(hist) and _open_session_fraction(hist.index[-1]) is not None:
        return hist.iloc[:-1]
    return hist


def simulate(
    frame: pd.DataFrame,
    *,
    entry: EntryMode = "buy_zone",
    min_score: int = 3,
    max_hold: int = 250,
    entry_window: int = 10,
    cost_bps: float = 5.0,
    exit: ExitMode = "trail",
    trail_atr: float = TRAIL_ATR,
    start: int = WARMUP,
) -> list[dict]:
    """Walk the scored frame day by day and return the simulated trades."""
    cost = cost_bps / 10_000
    o, h, l, c = (frame[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = frame["atr14"].to_numpy() if "atr14" in frame else np.full(len(frame), np.nan)
    dates = [d.strftime("%Y-%m-%d") for d in frame.index]
    n = len(frame)

    trades: list[dict] = []
    order: dict | None = None  # pending signal waiting to fill
    pos: dict | None = None

    def close_trade(i: int, price: float, reason: str) -> None:
        nonlocal pos
        fill_net = pos["entry"] * (1 + cost)
        exit_net = price * (1 - cost)
        risk = pos["entry"] - pos["init_stop"]
        if reason.startswith("stop") and pos["stop"] > pos["init_stop"]:
            reason = "trail_stop"
        trades.append(
            {
                **{k: pos[k] for k in ("signal_date", "entry_date", "score")},
                "stop": pos["init_stop"],
                "target": None if exit == "trail" else pos["target"],
                "entry": round(pos["entry"], 2),
                "exit_date": dates[i],
                "exit": round(price, 2),
                "reason": reason,
                "days": i - pos["entry_i"],
                "r": round((exit_net - fill_net) / risk, 3),
                "return_pct": round((exit_net / fill_net - 1) * 100, 2),
            }
        )
        pos = None

    def open_trade(i: int, price: float) -> None:
        nonlocal pos, order
        pos = {**order, "entry": price, "entry_date": dates[i], "entry_i": i,
               "init_stop": order["stop"], "peak": price}
        if exit == "trail":
            pos["target"] = math.inf
        order = None

    for i in range(start, n):
        # 1. Manage an open position (not on the day it was opened)
        if pos is not None and pos["entry_i"] < i:
            stop, target = pos["stop"], pos["target"]
            if o[i] <= stop:
                close_trade(i, o[i], "stop_gap")
            elif o[i] >= target:
                close_trade(i, o[i], "target_gap")
            elif l[i] <= stop:
                close_trade(i, stop, "stop")
            elif h[i] >= target:
                close_trade(i, target, "target")
            elif i - pos["entry_i"] >= max_hold:
                close_trade(i, c[i], "time")

        # 2. Try to fill a pending order
        if pos is None and order is not None:
            if i > order["expires_i"]:
                order = None
            elif entry == "next_open":
                if order["stop"] < o[i] < order["target"]:
                    open_trade(i, o[i])
                else:
                    order = None
            elif o[i] <= order["stop"]:
                order = None  # gapped through the stop: a trader would cancel
            elif l[i] <= order["limit"]:
                open_trade(i, min(o[i], order["limit"]))
            if pos is not None and l[i] <= pos["stop"]:
                close_trade(i, pos["stop"], "stop")

        # Raise the trailing stop from today's close (used from tomorrow)
        if pos is not None and exit == "trail" and not np.isnan(atr[i]):
            pos["peak"] = max(pos["peak"], c[i])
            pos["stop"] = max(pos["stop"], pos["peak"] - trail_atr * atr[i])

        # 3. Today's close produces a new signal for tomorrow
        if pos is None and i < n - 1:
            row = frame.iloc[i]
            if row["action"] == "BUY" and row["score"] >= min_score:
                limit = row["buy_high"] if entry == "buy_zone" else None
                stop, target = row["stop_loss"], row["take_profit"]
                if stop < (limit or row["close"]) < target:
                    order = {
                        "signal_date": dates[i],
                        "score": int(row["score"]),
                        "limit": limit,
                        "stop": float(stop),
                        "target": float(target),
                        "expires_i": i + (entry_window if entry == "buy_zone" else 1),
                    }

    if pos is not None:
        close_trade(n - 1, c[n - 1], "open")
    return trades


def summarize(trades: list[dict]) -> dict:
    """Stats over closed trades (a still-open trade is excluded)."""
    closed = sorted((t for t in trades if t["reason"] != "open"), key=lambda t: t["exit_date"])
    if not closed:
        return {"trades": 0}
    r = np.array([t["r"] for t in closed])
    wins, losses = r[r > 0], r[r <= 0]
    equity = np.cumsum(r)
    drawdown = float((np.maximum.accumulate(np.r_[0.0, equity]) - np.r_[0.0, equity]).max())
    growth = float(np.prod(1 + 0.01 * r))  # risking 1% of the account per trade
    reasons: dict[str, int] = {}
    for t in closed:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "trades": len(closed),
        "win_rate": round(len(wins) / len(r) * 100, 1),
        "expectancy_r": round(float(r.mean()), 3),
        "total_r": round(float(r.sum()), 2),
        "avg_win_r": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss_r": round(float(losses.mean()), 2) if len(losses) else None,
        "profit_factor": (
            round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None
        ),
        "max_drawdown_r": round(drawdown, 2),
        "return_at_1pct_risk": round((growth - 1) * 100, 1),
        "avg_days": round(float(np.mean([t["days"] for t in closed])), 1),
        "exits": reasons,
    }


def signal_study(frame: pd.DataFrame, start: int = WARMUP) -> dict:
    """Average forward return after each action / score — does the score predict anything?"""
    close = frame["close"]
    fwd = {d: (close.shift(-d) / close - 1) * 100 for d in FORWARD_DAYS}
    scored = frame.iloc[start:].dropna(subset=["action"])
    out = {"by_action": {}, "by_score": {}}
    for key, groups in (("by_action", scored.groupby("action")), ("by_score", scored.groupby("score"))):
        for name, group in groups:
            stats = {"days": len(group)}
            for d in FORWARD_DAYS:
                vals = fwd[d].loc[group.index].dropna()
                stats[f"fwd_{d}d"] = list(vals) if len(vals) else []
            out[key][str(int(name)) if key == "by_score" else name] = stats
    return out


def _merge_study(studies: list[dict]) -> dict:
    """Combine per-symbol forward returns into mean / % positive per bucket."""
    merged: dict = {"by_action": {}, "by_score": {}}
    for study in studies:
        for key in merged:
            for name, stats in study[key].items():
                bucket = merged[key].setdefault(name, {"days": 0, **{f"fwd_{d}d": [] for d in FORWARD_DAYS}})
                bucket["days"] += stats["days"]
                for d in FORWARD_DAYS:
                    bucket[f"fwd_{d}d"] += stats[f"fwd_{d}d"]
    for key in merged:
        for bucket in merged[key].values():
            for d in FORWARD_DAYS:
                vals = bucket.pop(f"fwd_{d}d")
                bucket[f"avg_{d}d"] = round(float(np.mean(vals)), 2) if vals else None
                bucket[f"pos_{d}d"] = round(float(np.mean(np.array(vals) > 0)) * 100, 1) if vals else None
    merged["by_score"] = dict(sorted(merged["by_score"].items(), key=lambda kv: int(kv[0])))
    return merged


def _buy_hold_pct(frame: pd.DataFrame, start: int) -> float | None:
    if len(frame) <= start:
        return None
    first, last = frame["close"].iloc[start], frame["close"].iloc[-1]
    return round((last / first - 1) * 100, 2) if first > 0 else None


def backtest_symbol(hist: pd.DataFrame, benchmark: pd.Series | None = None, **params) -> dict:
    hist = _drop_open_session(hist)
    if len(hist) < WARMUP + 20:
        raise ValueError(f"Need at least {WARMUP + 20} daily bars, got {len(hist)}")
    frame = score_frame(indicator_frame(hist, benchmark))
    trades = simulate(frame, **params)
    closed = [t for t in trades if t["reason"] != "open"]
    strategy = math.prod(1 + t["return_pct"] / 100 for t in closed) - 1 if closed else 0.0
    return {
        "from": frame.index[WARMUP].strftime("%Y-%m-%d"),
        "to": frame.index[-1].strftime("%Y-%m-%d"),
        "summary": summarize(trades),
        "strategy_return_pct": round(strategy * 100, 2),
        "buy_hold_pct": _buy_hold_pct(frame, WARMUP),
        "trades": trades,
        "_study": signal_study(frame),
    }


def run_backtest(histories: dict[str, pd.DataFrame], benchmark: pd.DataFrame | None = None, **params) -> dict:
    """Backtest each symbol and pool the trades into one overall summary."""
    per_symbol, errors, studies, all_trades = [], {}, [], []
    bench_close = _drop_open_session(benchmark)["Close"] if benchmark is not None else None
    for symbol, hist in histories.items():
        try:
            result = backtest_symbol(hist, bench_close, **params)
        except ValueError as exc:
            errors[symbol] = str(exc)
            continue
        studies.append(result.pop("_study"))
        for t in result["trades"]:
            all_trades.append({"symbol": symbol, **t})
        per_symbol.append({"symbol": symbol, **result})

    bench = None
    if benchmark is not None and per_symbol:
        # Buy & hold the benchmark over the same window as the earliest symbol
        close = bench_close
        close = close[close.index.strftime("%Y-%m-%d") >= min(s["from"] for s in per_symbol)]
        if len(close) > 1 and close.iloc[0] > 0:
            bench = round((close.iloc[-1] / close.iloc[0] - 1) * 100, 2)

    all_trades.sort(key=lambda t: t["entry_date"])
    return {
        "params": params,
        "overall": summarize(all_trades),
        "benchmark_buy_hold_pct": bench,
        "signal_study": _merge_study(studies),
        "symbols": [{k: v for k, v in s.items() if k != "trades"} for s in per_symbol],
        "trades": all_trades,
        "errors": errors,
    }
