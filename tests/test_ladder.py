from datetime import date

import pytest

from backend.ladder import compute_ladder, compute_system

TODAY = date(2026, 10, 13)


def levels(**overrides):
    base = {
        "price": 100.0, "atr14": 2.0, "stop_loss": 80.0, "sma50": 100.0, "sma20": 101.0,
        "buy_low": 92.0, "buy_high": 96.0, "take_profit": 112.0,
        "sma200": 90.0, "rsi14": 50.0, "risk_reward": 2.0,
        "change_1d_pct": 0.5, "volume_ratio": 1.0,
    }
    return {**base, **overrides}


@pytest.mark.parametrize(
    "price, rung",
    # SMA50 100, step 2×ATR = 4: rungs at 88 / 92 / 96 / 104 / 108
    [(85, "buy_lot"), (90, "buy"), (94, "buy_little"), (100, "hold"), (106, "trim"), (110, "trim_more")],
)
def test_price_lands_on_the_right_rung(price, rung):
    assert compute_ladder(levels(price=price))["current"] == rung


def test_step_has_a_floor_for_calm_stocks():
    ladder = compute_ladder(levels(atr14=0.1))  # 2×ATR = 0.2 < 2% of price
    assert ladder["step"] == 2.0
    assert ladder["rungs"][0]["label"] == "TRIM MORE" and ladder["rungs"][0]["low"] == 104.0


def test_good_day_to_buy_when_everything_passes():
    market = {"price": 500, "sma50": 480, "change_1d_pct": 0.3}
    system = compute_system(levels(price=94), market=market, earnings_date=date(2026, 11, 5),
                            last_bar=TODAY, today=TODAY)
    assert system["day"]["status"] == "go"
    assert system["ladder_check"]["status"] == "go"


def test_wait_when_chasing_into_earnings():
    system = compute_system(levels(price=94, change_1d_pct=5.0), earnings_date=date(2026, 10, 15),
                            last_bar=TODAY, today=TODAY)
    assert system["day"]["status"] == "wait"
    failed = [c["label"] for c in system["day"]["checks"] if not c["ok"]]
    assert failed == ["Not chasing a spike", "No earnings this week"]


def test_ladder_flagged_below_stop_and_sma200():
    system = compute_system(levels(price=78, sma200=95), last_bar=TODAY, today=TODAY)
    assert system["ladder"]["below_stop"]
    assert system["ladder_check"]["status"] == "wait"


def test_hold_zone_needs_no_action():
    system = compute_system(levels(price=100), last_bar=TODAY, today=TODAY)
    assert system["day"]["text"].startswith("Hold zone")
