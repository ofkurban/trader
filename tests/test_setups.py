import numpy as np
import pandas as pd
import pytest

from backend.setups import completed_bars, compute_setup


NOW = pd.Timestamp("2026-10-30 17:00", tz="America/New_York")


def base():
    close = np.r_[np.linspace(80, 99, 60), np.full(20, 99.)]
    return pd.DataFrame({"Close": close, "High": close + 1, "Low": close - 1,
                         "Volume": np.full(80, 1_000_000.)},
                        index=pd.bdate_range(end="2026-10-30", periods=80))


def test_near_setup_has_levels_from_prior_bars():
    setup = compute_setup(base(), True, NOW)
    assert setup["state"] == "near"
    assert setup["trigger"] == 100
    assert setup["invalidation"] == 98
    assert not setup["stale"]
    assert compute_setup(base(), False, NOW)["state"] == "forming"


def test_breakout_requires_volume_and_keeps_prebreakout_trigger():
    bars = base()
    bars.loc[bars.index[-1], ["Close", "High", "Low", "Volume"]] = [102, 103, 101, 2e6]
    setup = compute_setup(bars, True, NOW)
    assert setup["state"] == "confirmed"
    assert setup["trigger"] == 100  # today's high must never become today's trigger
    assert setup["breakout_date"] == "2026-10-30"
    bars.loc[bars.index[-1], "Volume"] = 1e6
    assert compute_setup(bars, True, NOW)["state"] != "confirmed"


@pytest.mark.parametrize("price,low,state", [(99, 98, "failed"), (101, 100, "pullback"), (110, 109, "extended")])
def test_recent_breakout_retest_failure_and_extension(price, low, state):
    bars = base()
    bars.loc[bars.index[-2], ["Close", "High", "Low", "Volume"]] = [102, 103, 101, 2e6]
    bars.loc[bars.index[-1], ["Close", "High", "Low"]] = [price, price + 1, low]
    setup = compute_setup(bars, True, NOW)
    assert setup["state"] == state
    assert setup["trigger"] == 100


def test_unfinished_session_is_not_confirmed():
    bars = base()
    bars.loc[bars.index[-1], ["Close", "High", "Volume"]] = [105, 106, 5e6]
    now = pd.Timestamp("2026-10-30 14:00", tz="America/New_York")
    assert len(completed_bars(bars, now)) == 79
    setup = compute_setup(bars, True, now)
    assert setup["state"] == "near"
    assert setup["as_of"] == "2026-10-29"


def test_stale_and_insufficient_data():
    assert compute_setup(base().iloc[:20], True, NOW)["state"] == "unavailable"
    assert compute_setup(base(), True, pd.Timestamp("2026-11-10"))["stale"]


def test_monitor_replays_missed_bars_and_reports_missing_symbols(monkeypatch):
    import backend.early_signals as es
    from backend.setups import monitor
    monkeypatch.setattr(es, "_download", lambda symbols: {"AAA": base(), "SPY": base()})
    monitor.cache_clear()
    try:
        result = monitor(("AAA", "MISSING"))["setups"]
        assert len(result["AAA"]["observations"]) == 60
        assert result["AAA"]["observations"][-1]["as_of"] == "2026-10-30"
        assert "error" in result["MISSING"]
    finally:
        monitor.cache_clear()
