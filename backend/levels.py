from __future__ import annotations

import pandas as pd

MARKET_TZ = "America/New_York"
SESSION_OPEN = (9, 30)
SESSION_CLOSE = (16, 0)
# Below this share of the session, projected volume is too noisy to report
MIN_SESSION_FRACTION = 0.1

# Take profit: if resistance is within TP_MIN_ATR_ROOM ATRs of price, project
# TP_PROJECTION_ATR ATRs above price instead.
TP_MIN_ATR_ROOM = 1.0
TP_PROJECTION_ATR = 3.0


def _open_session_fraction(bar_ts, now: pd.Timestamp | None = None) -> float | None:
    """Share of the regular session elapsed if bar_ts is today's still-open bar, else None."""
    now = now or pd.Timestamp.now(tz=MARKET_TZ)
    ts = pd.Timestamp(bar_ts)
    ts = ts.tz_localize(MARKET_TZ) if ts.tzinfo is None else ts.tz_convert(MARKET_TZ)
    if ts.date() != now.date():
        return None
    open_ = now.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1], second=0, microsecond=0)
    close_ = now.replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1], second=0, microsecond=0)
    if now >= close_:
        return None
    return max(0.0, (now - open_) / (close_ - open_))


def compute_rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    # avg_loss == 0 → RSI 100 (all gains); both 0 → neutral 50
    rsi = 100 - (100 / (1 + avg_gain / avg_loss.replace(0, pd.NA)))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return rsi.astype(float)


def rsi_signal(rsi: float) -> str:
    if rsi < 30:
        return "oversold"
    if rsi > 70:
        return "overbought"
    return "neutral"


def volume_signal(ratio: float) -> str:
    if ratio >= 1.2:
        return "high"
    if ratio <= 0.8:
        return "low"
    return "normal"


def compute_recommendation(
    *,
    last: float,
    sma20: float,
    sma50: float | None,
    sma200: float | None,
    rsi14: float | None,
    volume_ratio: float | None,
    support: float,
    resistance: float,
    risk_reward: float | None,
) -> dict:
    """Score-based BUY / HOLD / SELL from technicals (educational, not advice)."""
    score = 0
    reasons: list[str] = []

    # Trend vs moving averages
    if last > sma20:
        score += 1
        reasons.append("Price above SMA20 (short-term strength)")
    else:
        score -= 1
        reasons.append("Price below SMA20 (short-term weakness)")

    if sma50 is not None:
        if last > sma50:
            score += 1
            reasons.append("Price above SMA50")
        else:
            score -= 1
            reasons.append("Price below SMA50")

    if sma200 is not None:
        if last > sma200:
            score += 2
            reasons.append("Price above SMA200 (long-term uptrend)")
        else:
            score -= 2
            reasons.append("Price below SMA200 (long-term downtrend)")

    # RSI
    if rsi14 is not None:
        if rsi14 < 30:
            score += 2
            reasons.append(f"RSI {rsi14} oversold — bounce setup possible")
        elif rsi14 < 45:
            score += 1
            reasons.append(f"RSI {rsi14} leaning oversold")
        elif rsi14 > 70:
            score -= 2
            reasons.append(f"RSI {rsi14} overbought — caution on new buys")
        elif rsi14 > 55:
            score -= 1
            reasons.append(f"RSI {rsi14} elevated")
        else:
            reasons.append(f"RSI {rsi14} neutral")

    # Position in range (near support vs resistance)
    span = resistance - support
    if span > 0:
        pos = (last - support) / span
        if pos <= 0.25:
            score += 1
            reasons.append("Price near 60-day support")
        elif pos >= 0.85:
            score -= 1
            reasons.append("Price near 60-day resistance")

    # Volume confirmation
    if volume_ratio is not None:
        if volume_ratio >= 1.2 and last < sma20:
            score -= 1
            reasons.append("High volume on weakness")
        elif volume_ratio >= 1.2 and last > sma20:
            score += 1
            reasons.append("High volume on strength")

    # Risk/reward quality for new entries
    if risk_reward is not None:
        if risk_reward >= 2:
            score += 1
            reasons.append(f"Risk/reward from current price {risk_reward}:1 is favorable")
        elif risk_reward < 1.2:
            score -= 1
            reasons.append(f"Risk/reward from current price {risk_reward}:1 is weak")

    if score >= 3:
        action = "BUY"
        confidence = "high" if score >= 5 else "medium"
        summary = "Technical setup leans buy — prefer entries near support / buy zone."
    elif score <= -3:
        action = "SELL"
        confidence = "high" if score <= -5 else "medium"
        summary = "Technical setup leans sell / reduce — weakness vs averages or overbought near resistance."
    else:
        action = "HOLD"
        confidence = "medium" if abs(score) <= 1 else "low"
        summary = "Mixed signals — hold existing, wait for clearer setup before new trades."

    return {
        "action": action,
        "confidence": confidence,
        "score": score,
        "summary": summary,
        "reasons": reasons[:6],
    }


def compute_levels(hist: pd.DataFrame) -> dict:
    """Compute support/resistance and suggested buy, sell, stop levels."""
    if hist.empty or len(hist) < 20:
        raise ValueError("Not enough history to compute levels")

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]
    last = float(close.iloc[-1])

    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    rsi_series = compute_rsi_series(close)
    rsi14 = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None

    last_volume = float(volume.iloc[-1])
    session_fraction = _open_session_fraction(hist.index[-1])
    if session_fraction is None:
        # Last bar is a completed session
        vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
        compare_volume = last_volume
    else:
        # Last bar is today's unfinished session: average the 20 prior complete bars
        # and project today's volume to a full session (linear, so it overstates a
        # little early in the day when volume is front-loaded).
        vol_avg20 = float(volume.iloc[-21:-1].mean())
        compare_volume = (
            last_volume / session_fraction if session_fraction >= MIN_SESSION_FRACTION else None
        )
    volume_ratio = (
        round(compare_volume / vol_avg20, 2)
        if compare_volume is not None and vol_avg20 > 0
        else None
    )

    recent = hist.tail(60)
    support = float(recent["Low"].min())
    resistance = float(recent["High"].max())

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])

    buy_low = round(min(support, sma20 * 0.98), 2)
    buy_high = round(sma20, 2)
    entry_mid = (buy_low + buy_high) / 2

    # Near the 60d high, resistance is ~today's price and makes a useless target —
    # project an ATR-based target above the current price instead.
    if resistance - last < TP_MIN_ATR_ROOM * atr14:
        take_profit = round(last + TP_PROJECTION_ATR * atr14, 2)
        take_profit_basis = "atr_projection"
    else:
        take_profit = round(resistance, 2)
        take_profit_basis = "resistance"
    stop_loss = round(min(entry_mid - 1.5 * atr14, support - 0.5 * atr14), 2)

    risk = entry_mid - stop_loss
    reward = take_profit - entry_mid
    risk_reward = round(reward / risk, 2) if risk > 0 else None

    # Same trade entered at the current price instead of the buy zone
    risk_now = last - stop_loss
    reward_now = take_profit - last
    risk_reward_now = round(reward_now / risk_now, 2) if risk_now > 0 else None

    recommendation = compute_recommendation(
        last=last,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        rsi14=round(rsi14, 1) if rsi14 is not None else None,
        volume_ratio=volume_ratio,
        support=support,
        resistance=resistance,
        risk_reward=risk_reward_now,
    )

    return {
        "price": round(last, 2),
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "rsi14": round(rsi14, 1) if rsi14 is not None else None,
        "rsi_signal": rsi_signal(rsi14) if rsi14 is not None else "n/a",
        "volume_avg20": int(vol_avg20),
        "volume_last": int(last_volume),
        "volume_ratio": volume_ratio,
        "volume_signal": volume_signal(volume_ratio) if volume_ratio is not None else "n/a",
        "support_60d": round(support, 2),
        "resistance_60d": round(resistance, 2),
        "buy_low": buy_low,
        "buy_high": buy_high,
        "entry_mid": round(entry_mid, 2),
        "take_profit": take_profit,
        "take_profit_basis": take_profit_basis,
        "stop_loss": stop_loss,
        "atr14": round(atr14, 2),
        "risk_reward": risk_reward,
        "risk_reward_now": risk_reward_now,
        "volume_partial_session": session_fraction is not None,
        "trend_vs_sma20": "above" if last > sma20 else "below",
        "trend_vs_sma50": (
            "above" if sma50 and last > sma50 else ("below" if sma50 else "n/a")
        ),
        "trend_vs_sma200": (
            "above" if sma200 and last > sma200 else ("below" if sma200 else "n/a")
        ),
        "recommendation": recommendation,
        **compute_price_stats(hist),
    }


def history_to_records(hist: pd.DataFrame) -> list[dict]:
    rsi_series = compute_rsi_series(hist["Close"])
    records = []
    for idx, row in hist.iterrows():
        rsi_val = rsi_series.loc[idx]
        records.append(
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
                "rsi": round(float(rsi_val), 1) if not pd.isna(rsi_val) else None,
            }
        )
    return records


def compute_price_stats(hist: pd.DataFrame) -> dict:
    close = hist["Close"]
    last = float(close.iloc[-1])

    def pct_change(trading_days: int) -> float | None:
        if len(close) <= trading_days:
            return None
        old = float(close.iloc[-(trading_days + 1)])
        if old == 0:
            return None
        return round((last - old) / old * 100, 2)

    def dollar_change(trading_days: int) -> float | None:
        if len(close) <= trading_days:
            return None
        old = float(close.iloc[-(trading_days + 1)])
        return round(last - old, 2)

    return {
        "change_1d_pct": pct_change(1),
        "change_1d": dollar_change(1),
        "change_5d_pct": pct_change(5),
        "change_5d": dollar_change(5),
        "change_1mo_pct": pct_change(21),
        "change_1mo": dollar_change(21),
        "change_3mo_pct": pct_change(63),
        "change_3mo": dollar_change(63),
        "change_6mo_pct": pct_change(126),
        "change_6mo": dollar_change(126),
        "change_1y_pct": pct_change(252) if len(close) > 252 else pct_change(max(len(close) - 1, 1)),
        "change_1y": dollar_change(252) if len(close) > 252 else dollar_change(max(len(close) - 1, 1)),
        "high_1y": round(float(hist["High"].max()), 2),
        "low_1y": round(float(hist["Low"].min()), 2),
    }
