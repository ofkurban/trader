import numpy as np
import pandas as pd
import pytest

import backend.levels as lv
from backend.levels import MARKET_TZ, _open_session_fraction, compute_levels, compute_recommendation


def make_hist(closes, volume=None, end="2026-09-30"):
    idx = pd.bdate_range(end=end, periods=len(closes), tz=MARKET_TZ)
    close = pd.Series(closes, index=idx, dtype=float)
    vol = np.full(len(close), 1e6) if volume is None else volume
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": vol},
        index=idx,
    )


def test_too_little_history_raises():
    with pytest.raises(ValueError):
        compute_levels(make_hist(np.linspace(100, 110, 10)))


def test_at_highs_projects_target_above_price():
    levels = compute_levels(make_hist(np.linspace(100, 200, 250)))
    assert levels["take_profit_basis"] == "atr_projection"
    assert levels["take_profit"] > levels["price"] + levels["atr14"]


def test_pullback_uses_resistance_target():
    closes = np.r_[np.linspace(100, 200, 200), np.linspace(200, 170, 50)]
    levels = compute_levels(make_hist(closes))
    assert levels["take_profit_basis"] == "resistance"
    assert levels["take_profit"] == levels["resistance_60d"]


def test_level_ordering():
    closes = np.r_[np.linspace(100, 200, 200), np.linspace(200, 170, 50)]
    levels = compute_levels(make_hist(closes))
    assert levels["stop_loss"] < levels["buy_low"] <= levels["buy_high"] < levels["take_profit"]


def test_risk_reward_now_measured_from_price():
    levels = compute_levels(make_hist(np.linspace(100, 200, 250)))
    expected = (levels["take_profit"] - levels["price"]) / (levels["price"] - levels["stop_loss"])
    assert levels["risk_reward_now"] == pytest.approx(expected, abs=0.01)


def test_session_fraction():
    today = pd.Timestamp("2026-10-08", tz=MARKET_TZ)
    at = lambda hhmm: pd.Timestamp(f"2026-10-08 {hhmm}", tz=MARKET_TZ)  # noqa: E731
    assert _open_session_fraction(today, at("12:45")) == pytest.approx(0.5)
    assert _open_session_fraction(today, at("08:00")) == 0.0
    assert _open_session_fraction(today, at("16:30")) is None
    assert _open_session_fraction(pd.Timestamp("2026-10-07", tz=MARKET_TZ), at("12:45")) is None


@pytest.mark.parametrize("fraction,expected_ratio", [(0.5, 1.0), (0.05, None)])
def test_partial_session_volume_is_projected(monkeypatch, fraction, expected_ratio):
    volume = np.full(250, 1e6)
    volume[-1] = 1e6 * fraction  # exactly on pace for a normal day
    monkeypatch.setattr(lv, "_open_session_fraction", lambda ts, now=None: fraction)
    levels = compute_levels(make_hist(np.linspace(100, 200, 250), volume=volume))
    assert levels["volume_partial_session"] is True
    assert levels["volume_ratio"] == expected_ratio


def test_completed_session_volume_ratio():
    volume = np.full(250, 1e6)
    volume[-1] = 2e6
    levels = compute_levels(make_hist(np.linspace(100, 200, 250), volume=volume))
    assert levels["volume_partial_session"] is False
    assert levels["volume_signal"] == "high"


BASE = dict(last=110, sma20=105, sma50=100, sma200=90, rsi14=50, volume_ratio=1.0,
            support=80, resistance=130, risk_reward=1.5)


@pytest.mark.parametrize("rs,delta", [(None, 0), (5, 0), (15, 2), (-15, -2)])
def test_relative_strength_points(rs, delta):
    base = compute_recommendation(**BASE)["score"]
    assert compute_recommendation(**BASE, rel_strength=rs)["score"] == base + delta


def test_no_oversold_credit_in_downtrend():
    down = {**BASE, "last": 85, "sma20": 90, "sma50": 95, "sma200": 100, "rsi14": 25}
    neutral = compute_recommendation(**{**down, "rsi14": 50})["score"]
    assert compute_recommendation(**down)["score"] == neutral
    up = {**BASE, "rsi14": 25}
    assert compute_recommendation(**up)["score"] == compute_recommendation(**BASE)["score"] + 2


def test_relative_strength_vs_benchmark():
    stock = make_hist(np.linspace(100, 200, 250))
    flat = make_hist(np.full(250, 50.0))["Close"]
    levels = compute_levels(stock, flat)
    expected = (stock["Close"].iloc[-1] / stock["Close"].iloc[-127] - 1) * 100
    assert levels["rel_strength_6m"] == pytest.approx(expected, abs=0.05)
    assert any("Beat SPY" in r for r in levels["recommendation"]["reasons"])
