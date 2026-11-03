"""Daily-close setup radar. Rules are hypotheses, not calibrated probabilities."""
from __future__ import annotations

import pandas as pd

from backend.cache import ttl_cache


def completed_bars(frame: pd.DataFrame, now=None) -> pd.DataFrame:
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="America/New_York")
    now = now.tz_localize("America/New_York") if now.tzinfo is None else now.tz_convert("America/New_York")
    dates = pd.DatetimeIndex(frame.index)
    dates = dates.tz_localize("America/New_York") if dates.tz is None else dates.tz_convert("America/New_York")
    return frame[(dates.date < now.date()) | ((dates.date == now.date()) & (now.hour >= 16))].dropna(subset=["Close", "Volume"])


def compute_setup(frame: pd.DataFrame, strength: bool | None = None, now=None) -> dict:
    bars = completed_bars(frame, now)
    empty = {"state": "unavailable", "trigger": None, "invalidation": None,
             "distance_pct": None, "as_of": None, "stale": True,
             "checks": {}, "missing": ["At least 60 completed daily bars required"]}
    if len(bars) < 60:
        return empty
    close, volume = bars.Close.astype(float), bars.Volume.astype(float)
    high = bars.get("High", close).astype(float)
    low = bars.get("Low", close).astype(float)
    price = float(close.iloc[-1])
    if price <= 0:
        return empty
    # Search recent sessions for a breakout; retain that base through a retest
    # or failure instead of moving the trigger with each new daily high.
    anchor = len(bars) - 1
    breakout = False
    for i in range(max(55, len(bars) - 10), len(bars)):
        ceiling = float(high.iloc[i-20:i].max())
        floor = float(low.iloc[i-10:i].min())
        base_high = float(high.iloc[i-10:i].max())
        avg = float(volume.iloc[i-20:i].mean())
        trend = float(close.iloc[i-49:i+1].mean())
        old_trend = float(close.iloc[i-54:i-4].mean())
        if (ceiling > 0 and floor > 0 and (base_high / floor - 1) <= .08
                and float(close.iloc[i]) > ceiling and float(close.iloc[i]) > trend > old_trend
                and avg > 0 and float(volume.iloc[i]) >= 1.5 * avg):
            anchor, breakout = i, True
            break
    trigger = float(high.iloc[anchor-20:anchor].max())
    invalidation = float(low.iloc[anchor-10:anchor].min())
    base_high = float(high.iloc[anchor-10:anchor].max())
    avg_volume = float(volume.iloc[-21:-1].mean())
    ratio = float(volume.iloc[-1]) / avg_volume if avg_volume > 0 else None
    sma50 = float(close.iloc[-50:].mean())
    trend = price > sma50 > float(close.iloc[-55:-5].mean())
    tight = invalidation > 0 and base_high / invalidation - 1 <= .08
    distance = (trigger / price - 1) * 100
    checks = {"Above rising 50-day average": bool(trend), "Base range at most 8%": bool(tight),
              "Outperforming sector / SPY": strength, "Volume at least 1.5x normal": ratio >= 1.5 if ratio is not None else None}
    if breakout and (close.iloc[anchor:] < invalidation).any():
        state = "failed"
    elif price > trigger * 1.05 or price > sma50 * 1.15:
        state = "extended"
    elif breakout and price >= trigger and float(low.iloc[-1]) <= trigger * 1.02 and anchor < len(bars) - 1:
        state = "pullback"
    elif breakout and price >= trigger:
        state = "confirmed"
    elif breakout:
        state = "failed"
    elif trend and tight and strength is True and 0 <= distance <= 3:
        state = "near"
    elif trend and tight and price <= trigger:
        state = "forming"
    else:
        state = "watch"
    timestamp = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="America/New_York")
    as_of = pd.Timestamp(bars.index[-1]).date()
    return {"state": state, "trigger": round(trigger, 2), "invalidation": round(invalidation, 2),
            "distance_pct": round(distance, 2), "price": round(price, 2),
            "as_of": as_of.isoformat(), "stale": (timestamp.date() - as_of).days > 5,
            "volume_ratio": round(ratio, 2) if ratio is not None else None,
            "breakout_date": pd.Timestamp(bars.index[anchor]).date().isoformat() if breakout else None,
            "checks": checks, "missing": [k for k, v in checks.items() if v is not True]}


@ttl_cache(seconds=300, maxsize=64)
def monitor(symbols: tuple[str, ...]) -> dict:
    # Reuse the batched downloader, including symbols that left the radar.
    from backend.early_signals import _download, _ret
    frames = _download(list(dict.fromkeys([*symbols, "SPY"])))
    spy = completed_bars(frames["SPY"]) if "SPY" in frames else None
    benchmark = _ret(spy.Close, 21) if spy is not None else None
    results = {}
    for symbol in symbols:
        if symbol not in frames:
            results[symbol] = {"error": "Price data unavailable"}
            continue
        bars = completed_bars(frames[symbol])
        ret = _ret(bars.Close, 21)
        strength = ret - benchmark >= .03 if ret is not None and benchmark is not None else None
        results[symbol] = compute_setup(frames[symbol], strength)
        observations = []
        for i in range(max(20, len(bars) - 60), len(bars)):
            avg = float(bars.Volume.iloc[i-20:i].mean())
            observations.append({"as_of": pd.Timestamp(bars.index[i]).date().isoformat(),
                                 "price": round(float(bars.Close.iloc[i]), 2),
                                 "volume_ratio": float(bars.Volume.iloc[i]) / avg if avg > 0 else None})
        results[symbol]["observations"] = observations
    return {"setups": results}
