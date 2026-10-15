from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.journal import review_closed, trail_status
from backend.levels import MARKET_TZ


def make_hist(closes, end="2026-09-30"):
    idx = pd.bdate_range(end=end, periods=len(closes), tz=MARKET_TZ)
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1e6},
        index=idx,
    )


def test_trail_rises_with_new_highs():
    # Flat at 100 (ATR = 2 from the +-1 range), then a run to 130
    hist = make_hist(np.r_[np.full(40, 100.0), np.linspace(100, 130, 20)])
    entry = hist.index[40].date()
    status = trail_status(hist, entry_date=entry, entry_price=100, initial_stop=95)
    assert status["peak_close"] == 130
    assert status["trail_stop"] == pytest.approx(130 - 4 * status["atr14"], abs=0.3)
    assert status["trail_stop"] > 95
    assert status["stop_hit"] is None
    assert status["open_r"] == pytest.approx(6.0)
    assert status["locked_r"] > 0


def test_trail_never_falls_and_reports_hit():
    closes = np.r_[np.full(40, 100.0), np.linspace(100, 130, 20), np.linspace(129, 100, 15)]
    hist = make_hist(closes)
    status = trail_status(hist, entry_date=hist.index[40].date(), entry_price=100, initial_stop=95)
    assert status["stop_hit"] is not None
    assert status["trail_stop"] > 115  # stayed near the peak, didn't follow price down
    assert date.fromisoformat(status["stop_hit"]["date"]) > hist.index[60].date()


def test_initial_stop_hit_before_any_trail():
    hist = make_hist(np.r_[np.full(40, 100.0), [99, 97, 93, 92]])
    status = trail_status(hist, entry_date=hist.index[40].date(), entry_price=100, initial_stop=95)
    assert status["stop_hit"]["stop"] == 95


def test_stop_must_be_below_entry():
    with pytest.raises(ValueError):
        trail_status(make_hist(np.full(40, 100.0)), entry_date=date(2026, 9, 1), entry_price=100, initial_stop=100)


def trade(entry, stop, exit_, action="BUY", shares=10):
    return {"symbol": "X", "entry_date": "2026-01-02", "exit_date": "2026-01-12", "entry_price": entry,
            "initial_stop": stop, "exit_price": exit_, "shares": shares, "signal_action": action}


def test_review_closed_stats_and_discipline():
    closed = [
        trade(100, 95, 110),             # +2R on signal
        trade(100, 95, 95),              # -1R on signal
        trade(100, 90, 80, action=None),  # -2R off signal, stop blown
    ]
    out = review_closed(closed)
    s, d = out["stats"], out["discipline"]
    assert s["trades"] == 3
    assert s["expectancy_r"] == pytest.approx(-1 / 3, abs=0.001)
    assert s["total_pnl"] == pytest.approx(100 - 50 - 200)
    assert s["avg_days"] == 10
    assert d["on_signal_pct"] == pytest.approx(66.7)
    assert d["on_signal"]["expectancy_r"] == pytest.approx(0.5)
    assert d["off_signal"]["trades"] == 1
    assert d["blown_stops"] == 1
    assert d["blown_stop_r"] == pytest.approx(-1.0)


def test_review_empty():
    out = review_closed([])
    assert out["stats"] == {"trades": 0}
    assert out["discipline"]["on_signal_pct"] is None
