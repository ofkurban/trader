import pytest

from backend.news import _normalize_article
from backend.trader_agent import _extract_prices


@pytest.mark.parametrize(
    "question,first",
    [
        ("I bought 100 shares at $470", 470),
        ("bought 100 shares at 470", 470),
        ("I have 50 shares, entry 300", 300),
        ("paid 1,035.50 for MU", 1035.5),
        ("got in @ 212.4 three weeks ago", 212.4),
        ("bought at 470 on oct 3", 470),
        ("I bought at $470 — should I hold?", 470),
    ],
)
def test_extract_prices_picks_the_price(question, first):
    assert _extract_prices(question)[0] == first


def test_extract_prices_ignores_percents_and_time_spans():
    assert _extract_prices("down 30% in 2 months") == []


def headline(title):
    return _normalize_article({"content": {"title": title}}, "MU")


@pytest.mark.parametrize(
    "title",
    [
        "Chip stocks tumble as Nvidia slides",
        "Memory names hit by heavy selling",
        "Semis rout deepens",
        "Investors dumped chipmakers on Friday",
    ],
)
def test_selloff_headlines_flagged(title):
    assert headline(title)["is_selloff"]


@pytest.mark.parametrize(
    "title",
    [
        "Marvell unveils new router silicon",
        "Routine filing from Seagate",
        "Dumpling chain raises prices",
        "Throughout the quarter, demand held up",
    ],
)
def test_lookalike_words_not_flagged(title):
    assert not headline(title)["is_selloff"]


def test_china_memory_flags():
    assert headline("CXMT expands DRAM supply")["is_china_memory"]
    assert not headline("Shanghai index closes higher")["is_china_memory"]
    assert not headline("Chip prices rise on AI demand")["is_china_memory"]
