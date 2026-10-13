from datetime import date

import numpy as np
import pandas as pd
import pytest

import backend.early_signals as es

TODAY = date(2026, 10, 12)


def frame(closes, volumes):
    index = pd.date_range("2026-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=index)


def test_insider_signal_needs_two_open_market_buyers():
    tx = pd.DataFrame({
        "Insider": ["A", "B", "C", "A"],
        "Text": ["Purchase at price 10", "Purchase at price 11", "Stock Award(Grant)", "Sale at price 12"],
        "Value": [100_000, 200_000, 0, 50_000],
        "Start Date": pd.to_datetime(["2026-09-01", "2026-09-20", "2026-09-21", "2026-10-01"]),
    })
    hit = es.insider_signal(tx, TODAY)
    assert hit["hit"] and hit["sales"] == 1
    assert "2 insider(s)" in hit["detail"]

    one_buyer = es.insider_signal(tx[tx["Insider"] != "B"], TODAY)
    assert not one_buyer["hit"]

    stale = tx.assign(**{"Start Date": pd.to_datetime(["2026-01-01"] * 4)})
    assert not es.insider_signal(stale, TODAY)["hit"]


def test_volume_signal_wants_heavy_volume_with_flat_price():
    assert es.volume_signal({"vol_ratio": 2.0, "ret_10d": 0.01})["hit"]
    assert not es.volume_signal({"vol_ratio": 2.0, "ret_10d": 0.12})["hit"]  # already moving
    assert not es.volume_signal({"vol_ratio": 1.1, "ret_10d": 0.0})["hit"]


def test_catalyst_window():
    assert es.catalyst_signal({"Earnings Date": [date(2026, 11, 5)]}, TODAY)["hit"]
    assert not es.catalyst_signal({"Earnings Date": [date(2027, 3, 1)]}, TODAY)["hit"]
    assert not es.catalyst_signal({}, TODAY)["hit"]


def test_value_signal_uses_year_over_year_growth():
    income = pd.DataFrame(
        [[130.0, 90.0, 180.0, 110.0, 100.0]],  # seasonal swings, +30% YoY
        index=["Total Revenue"],
        columns=pd.to_datetime(["2026-07-31", "2026-04-30", "2026-01-31", "2025-10-31", "2025-07-31"]),
    )
    estimate = pd.DataFrame({"growth": [0.40]}, index=["0q"])
    growth = es.revenue_growth(income, estimate)
    assert growth == pytest.approx((0.30, 0.40))
    assert es.value_signal(growth, forward_pe=15, peer_pe=25)["hit"]
    assert not es.value_signal(growth, forward_pe=40, peer_pe=25)["hit"]  # not cheap
    assert not es.value_signal((0.30, 0.20), forward_pe=15, peer_pe=25)["hit"]  # slowing


def test_relative_strength_vs_sector():
    assert es.strength_signal({"ret_1m": 0.08}, 0.02, 0.03, "XLK")["hit"]
    assert es.strength_signal({"ret_1m": 0.0}, -0.05, -0.04, "XLK")["hit"]  # held up in a sell-off
    assert not es.strength_signal({"ret_1m": 0.03}, 0.02, 0.03, "XLK")["hit"]


def test_scan_excludes_hot_stocks_and_ranks_by_signal_count(monkeypatch):
    n = 120
    flat = np.full(n, 100.0)
    rocket = np.concatenate([np.full(n - 21, 100.0), np.linspace(100, 150, 21)])  # +50% in a month
    quiet_volume = np.full(n, 1_000_000.0)
    loud_volume = np.concatenate([np.full(n - 5, 1_000_000.0), np.full(5, 3_000_000.0)])
    frames = {
        "AAA": frame(flat, loud_volume),
        "BBB": frame(flat, quiet_volume),
        "HOT": frame(rocket, quiet_volume),
        "SPY": frame(flat, quiet_volume),
    }
    monkeypatch.setattr(es, "UNIVERSE", ["AAA", "BBB", "HOT"])
    monkeypatch.setattr(es, "WATCHLIST", [])
    monkeypatch.setattr(es, "_dynamic_symbols", lambda: [])
    monkeypatch.setattr(es, "_download", lambda symbols: {s: f for s, f in frames.items() if s in symbols})
    monkeypatch.setattr(es, "_fetch_fundamentals", lambda s: {
        "info": {"shortName": s, "sector": "Technology", "forwardPE": 20},
        "eps_trend": None, "eps_revisions": None, "insiders": None,
        "calendar": {}, "growth": (None, None),
    })
    es.scan.cache_clear()
    try:
        result = es.scan()
    finally:
        es.scan.cache_clear()

    assert result["excluded_hot"] == ["HOT"]
    symbols = [r["symbol"] for r in result["top"]]
    assert symbols[0] == "AAA" and "HOT" not in symbols
    assert result["top"][0]["signals"]["volume"]["hit"]


def test_accumulation_compares_up_and_down_day_volume():
    close = pd.Series([100, 101, 100, 101, 100, 101] * 10, dtype=float)
    heavy_up = pd.Series([1.0, 2.0, 1.0, 2.0, 1.0, 2.0] * 10)  # up days trade 2×
    assert es.updown_volume_ratio(close, heavy_up) == pytest.approx(2.0)
    assert es.accumulation_signal({"updown_ratio": 1.5})["hit"]
    assert not es.accumulation_signal({"updown_ratio": 1.1})["hit"]


def test_upgrades_signal_counts_recent_positive_actions():
    actions = pd.DataFrame(
        {
            "Firm": ["UBS", "Needham", "Citi", "Old Co"],
            "Action": ["up", "main", "main", "up"],
            "priceTargetAction": ["Raises", "Raises", "Lowers", "Raises"],
            "currentPriceTarget": [120.0, 110.0, 90.0, 80.0],
        },
        index=pd.to_datetime(["2026-10-01", "2026-09-25", "2026-09-30", "2026-06-01"]),
    )
    hit = es.upgrades_signal(actions, TODAY, 100.0)
    assert hit["hit"]
    assert "2 upgrade(s) / target raise(s), 1 cut(s)" in hit["detail"]
    assert not es.upgrades_signal(actions.iloc[[0, 2]], TODAY, 100.0)["hit"]  # 1 up vs 1 cut


def test_beats_signal_needs_four_straight_beats():
    quarters = pd.to_datetime(["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])
    beats = pd.DataFrame({"surprisePercent": [0.02, 0.05, 0.10, 0.04]}, index=quarters)
    assert es.beats_signal(beats)["hit"]
    one_miss = beats.assign(surprisePercent=[-0.01, 0.05, 0.10, 0.04])
    assert not es.beats_signal(one_miss)["hit"]


def test_shorts_signal_wants_falling_short_interest():
    assert es.shorts_signal({"sharesShort": 8e6, "sharesShortPriorMonth": 10e6})["hit"]
    assert not es.shorts_signal({"sharesShort": 11e6, "sharesShortPriorMonth": 10e6})["hit"]
    assert not es.shorts_signal({})["hit"]
