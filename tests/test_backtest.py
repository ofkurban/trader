import numpy as np
import pandas as pd
import pytest

from backend.backtest import indicator_frame, run_backtest, score_frame, simulate, summarize
from backend.levels import MARKET_TZ, compute_levels


def make_hist(closes, end="2026-09-30"):
    idx = pd.bdate_range(end=end, periods=len(closes), tz=MARKET_TZ)
    close = pd.Series(closes, index=idx, dtype=float)
    rng = np.random.default_rng(0)
    vol = rng.uniform(0.5e6, 1.5e6, len(close))
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": vol},
        index=idx,
    )


def wavy(n=600):
    t = np.arange(n)
    return 100 + 0.1 * t + 12 * np.sin(t / 15)


def test_last_row_matches_compute_levels():
    hist = make_hist(wavy())
    row = score_frame(indicator_frame(hist)).iloc[-1]
    levels = compute_levels(hist)
    for key in ("buy_low", "buy_high", "take_profit", "stop_loss", "risk_reward_now", "rsi14", "volume_ratio"):
        assert row[key] == pytest.approx(levels[key], abs=0.011), key
    assert row["action"] == levels["recommendation"]["action"]
    assert row["score"] == levels["recommendation"]["score"]


def bars(rows, **signal):
    """Frame of (open, high, low, close) rows; day 0 carries a BUY signal."""
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                         index=pd.bdate_range("2026-01-01", periods=len(rows)))
    defaults = {"action": None, "score": None, "buy_high": np.nan, "stop_loss": np.nan, "take_profit": np.nan}
    for key, value in defaults.items():
        frame[key] = value
    frame = frame.astype({"action": object, "score": object})
    for key, value in {"action": "BUY", "score": 4, "buy_high": 100, "stop_loss": 95, "take_profit": 110, **signal}.items():
        frame.iloc[0, frame.columns.get_loc(key)] = value
    return frame


def test_buy_zone_fill_then_target():
    frame = bars([(102, 103, 101, 102), (101, 101, 99, 100), (101, 111, 100, 110)])
    [t] = simulate(frame, start=0, cost_bps=0)
    assert (t["entry"], t["exit"], t["reason"]) == (100, 110, "target")
    assert t["r"] == pytest.approx(2.0)


def test_gap_down_fills_at_open_and_stop_wins_ties():
    frame = bars([(102, 103, 101, 102), (98, 99, 97, 98), (98, 111, 94, 100)])
    [t] = simulate(frame, start=0, cost_bps=0)
    assert t["entry"] == 98
    assert (t["exit"], t["reason"]) == (95, "stop")
    assert t["r"] == pytest.approx(-1.0)


def test_order_expires_and_gap_through_stop_cancels():
    never_dips = [(102, 103, 101, 102)] * 5
    assert simulate(bars(never_dips), start=0, entry_window=3) == []
    gap = [(102, 103, 101, 102), (94, 95, 93, 94), (94, 95, 93, 94)]
    assert simulate(bars(gap), start=0) == []


def test_next_open_and_time_exit():
    frame = bars([(102, 103, 101, 102)] * 6)
    [t] = simulate(frame, entry="next_open", start=0, max_hold=3, cost_bps=0)
    assert (t["entry"], t["reason"], t["days"]) == (102, "time", 3)


def test_min_score_filters_signals():
    frame = bars([(102, 103, 101, 102), (101, 101, 99, 100), (101, 111, 100, 110)], score=3)
    assert simulate(frame, start=0, min_score=4) == []


def test_summarize():
    trades = [
        {"r": 2.0, "return_pct": 4, "days": 5, "reason": "target", "exit_date": "2026-01-02"},
        {"r": -1.0, "return_pct": -2, "days": 3, "reason": "stop", "exit_date": "2026-01-03"},
        {"r": -1.0, "return_pct": -2, "days": 3, "reason": "stop", "exit_date": "2026-01-04"},
        {"r": 0.5, "return_pct": 1, "days": 9, "reason": "open", "exit_date": "2026-01-05"},
    ]
    s = summarize(trades)
    assert s["trades"] == 3
    assert s["win_rate"] == pytest.approx(33.3)
    assert s["expectancy_r"] == 0
    assert s["profit_factor"] == 1.0
    assert s["max_drawdown_r"] == 2.0
    assert s["exits"] == {"target": 1, "stop": 2}


def test_run_backtest_end_to_end():
    result = run_backtest(
        {"WAVY": make_hist(wavy()), "SHORT": make_hist(wavy(100))},
        benchmark=make_hist(np.linspace(100, 150, 600)),
    )
    assert "SHORT" in result["errors"]
    assert [s["symbol"] for s in result["symbols"]] == ["WAVY"]
    assert result["overall"]["trades"] > 0
    assert result["benchmark_buy_hold_pct"] > 0
    assert set(result["signal_study"]["by_action"]) <= {"BUY", "HOLD", "SELL"}
