import numpy as np
import pandas as pd
import pytest

from backend.analysts import analyst_report, prepare_ratings, side_of


def make_hist(closes, start="2020-01-01"):
    idx = pd.bdate_range(start=start, periods=len(closes), tz="America/New_York")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close}, index=idx)


def make_ratings(rows):
    """rows: (date, firm, grade, target)"""
    df = pd.DataFrame(
        [{"Firm": f, "ToGrade": g, "Action": "main", "currentPriceTarget": t} for _, f, g, t in rows],
        index=pd.DatetimeIndex([d for d, *_ in rows], name="GradeDate"),
    )
    return df.iloc[::-1]  # Yahoo lists newest first


# Stock doubles in year 1, halves in year 2, flat after; SPY flat throughout
CLOSES = np.r_[np.linspace(100, 200, 261), np.linspace(200, 100, 261), np.full(400, 100.0)]
HIST = make_hist(CLOSES)
SPY = make_hist(np.full(len(CLOSES), 400.0))["Close"]


def test_side_of_grades():
    assert side_of("Outperform") == 1
    assert side_of(" Underweight ") == -1
    assert side_of("Neutral") == 0


def test_right_firm_qualifies_wrong_firm_does_not():
    rows = []
    for k in range(6):
        rows.append((f"2020-01-{2 + k:02d}", "Good", "Buy", 190))  # rally follows
        rows.append((f"2020-01-{2 + k:02d}", "Bad", "Sell", 80))
        rows.append((f"2021-01-{4 + k:02d}", "Good", "Sell", 110))  # slide follows
        rows.append((f"2021-01-{4 + k:02d}", "Bad", "Buy", 250))
    rows.append(("2023-06-01", "Good", "Buy", 120))
    rows.append(("2023-06-01", "Bad", "Buy", 130))
    report = analyst_report(make_ratings(rows), HIST, SPY)

    assert [f["firm"] for f in report["top"]] == ["Good"]
    good = report["top"][0]
    assert good["calls"] == 12 and good["accuracy_pct"] == 100.0
    assert good["target_hit_pct"] == 100.0
    assert good["rating"] == "Buy" and good["target"] == 120
    assert good["upside_pct"] == pytest.approx(20.0)
    assert [f["firm"] for f in report["others"]] == ["Bad"]
    assert report["others"][0]["accuracy_pct"] == 0.0
    assert report["consensus"]["buy"] == 1


def test_recent_calls_are_not_graded_and_stale_firms_are_not_current():
    rows = [(f"2020-01-{2 + k:02d}", "Old", "Buy", 150) for k in range(6)]
    rows += [("2023-08-01", "New", "Buy", 120)]  # under a year before the last bar
    report = analyst_report(make_ratings(rows), HIST, SPY)
    assert report["top"] == []  # Old was right but its last call is years old
    assert report["graded_calls"] == 6


def test_targets_scaled_for_later_splits():
    ratings = make_ratings([("2020-01-02", "A", "Buy", 1000), ("2021-06-01", "A", "Buy", 300)])
    splits = pd.Series([10.0], index=pd.DatetimeIndex(["2021-01-04"], tz="America/New_York"))
    targets = prepare_ratings(ratings, splits)["target"].tolist()
    assert targets == [100.0, 300.0]


def test_zero_targets_ignored_and_same_day_duplicates_collapsed():
    ratings = make_ratings([
        ("2020-01-02 09:00", "A", "Hold", 0),
        ("2020-01-02 15:00", "A", "Buy", 150),
        ("2020-02-03 09:00", "A", "Buy", 0),
    ])
    rated = prepare_ratings(ratings)
    assert rated["ToGrade"].tolist() == ["Buy", "Buy"]
    assert rated["target"].iloc[0] == 150 and np.isnan(rated["target"].iloc[1])
