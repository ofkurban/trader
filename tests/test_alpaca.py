"""Alpaca client + routing, with HTTP mocked (no network, no real keys)."""

import pandas as pd
import pytest

import backend.alpaca as alpaca
import backend.news as news
from backend.symbols import search_symbols

ASSETS = [
    {"symbol": "TSLA", "name": "Tesla, Inc. Common Stock", "tradable": True},
    {"symbol": "NVDA", "name": "NVIDIA Corporation Common Stock", "tradable": True},
    {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc. Class B Common Stock", "tradable": True},
    {"symbol": "TSLL", "name": "Direxion Daily TSLA Bull 2X Shares", "tradable": True},
    {"symbol": "DEAD", "name": "Delisted Co", "tradable": False},
]


def bar(day, close, volume=1_000_000):
    return {"t": f"{day}T04:00:00Z", "o": close, "h": close + 1, "l": close - 1, "c": close, "v": volume}


@pytest.fixture
def fake_alpaca(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    monkeypatch.setattr(alpaca, "_assets_failed_at", None)
    alpaca.us_assets.cache_clear()
    search_symbols.cache_clear()
    news._raw_news.cache_clear()
    calls = []

    def fake_get(url, params=None, timeout=15):
        calls.append((url, params or {}))
        if url.endswith("/v2/assets"):
            return ASSETS
        if "/bars" in url:
            if params.get("feed") == "sip" and fake_get.refuse_sip:
                raise alpaca.AlpacaError('HTTP 403: {"message":"subscription does not permit querying recent SIP data"}')
            return {"bars": [bar("2026-10-06", 100), bar("2026-10-07", 102)], "next_page_token": None}
        if url.endswith("/v1beta1/news"):
            return {"news": [{"headline": "Chip stocks tumble", "summary": "", "source": "benzinga",
                              "url": "https://example.com/a", "created_at": "2026-10-07T13:00:00Z"}]}
        raise AssertionError(url)

    fake_get.refuse_sip = False
    monkeypatch.setattr(alpaca, "_get", fake_get)
    yield fake_get, calls
    alpaca.us_assets.cache_clear()
    search_symbols.cache_clear()
    news._raw_news.cache_clear()


def test_disabled_without_both_keys(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    for name in alpaca.SECRET_VARS:
        monkeypatch.delenv(name, raising=False)
    assert not alpaca.enabled()
    assert not alpaca.lists("AAPL")


def test_keys_are_trimmed(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "  PKTEST ")
    monkeypatch.setenv("ALPACA_API_SECRET", " secret\n")
    assert alpaca._credentials() == ("PKTEST", "secret")


def test_routing_and_names(fake_alpaca):
    assert alpaca.lists("TSLA")
    assert alpaca.lists("BRK-B")  # Yahoo-style share class
    assert not alpaca.lists("005930.KS")
    assert not alpaca.lists("^SOX")
    assert not alpaca.lists("DEAD")
    assert alpaca.asset_name("NVDA") == "NVIDIA Corporation"
    assert alpaca.asset_name("BRK-B") == "Berkshire Hathaway Inc."


def test_history_shape_matches_yahoo(fake_alpaca):
    _, calls = fake_alpaca
    hist = alpaca.history("TSLA", "1y")
    assert list(hist.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert str(hist.index.tz) == "America/New_York"
    assert hist.index[-1] == pd.Timestamp("2026-10-07", tz="America/New_York")
    params = calls[-1][1]
    assert params["feed"] == "sip" and params["adjustment"] == "all"


def test_history_falls_back_to_iex_when_sip_refused(fake_alpaca):
    fake_get, calls = fake_alpaca
    fake_get.refuse_sip = True
    hist = alpaca.history("TSLA", "1y")
    assert len(hist) == 2
    assert calls[-1][1]["feed"] == "iex"


def test_news_uses_alpaca_for_us_symbols(fake_alpaca):
    articles = news.fetch_news("TSLA")
    assert articles[0]["title"] == "Chip stocks tumble"
    assert articles[0]["publisher"] == "Benzinga"
    assert articles[0]["is_selloff"]


def test_search_ranks_symbol_then_name(fake_alpaca, monkeypatch):
    import backend.symbols as symbols

    monkeypatch.setattr(symbols, "_yahoo_search", lambda q, limit: [
        {"symbol": "TL0.DE", "name": "Tesla Inc", "exchange": "XETRA", "type": "Equity"}
    ])
    results = search_symbols("tsla")
    # Ticker match first, then Yahoo, then name-only matches (TSLL's name mentions TSLA)
    assert [r["symbol"] for r in results] == ["TSLA", "TL0.DE", "TSLL"]
    assert "symbol_match" not in results[0]


def test_name_search_prefers_yahoo_relevance(fake_alpaca, monkeypatch):
    import backend.symbols as symbols

    monkeypatch.setattr(symbols, "_yahoo_search", lambda q, limit: [
        {"symbol": "TSLA", "name": "Tesla, Inc.", "exchange": "NASDAQ", "type": "Equity"},
        {"symbol": "TL0.DE", "name": "Tesla Inc", "exchange": "XETRA", "type": "Equity"},
    ])
    # "tesla" is a name match only, so Yahoo's ranking leads
    assert [r["symbol"] for r in search_symbols("tesla")] == ["TSLA", "TL0.DE"]


def test_search_works_when_yahoo_is_down(fake_alpaca, monkeypatch):
    import backend.symbols as symbols

    def down(q, limit):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(symbols, "_yahoo_search", down)
    assert search_symbols("nvidia")[0]["symbol"] == "NVDA"


def test_secret_name_aliases(monkeypatch):
    for name in alpaca.SECRET_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_SECRET_KEY", ' "secret" ')
    assert alpaca._credentials() == ("PKTEST", "secret")


def test_asset_outage_backs_off(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    monkeypatch.setattr(alpaca, "_assets_failed_at", None)
    alpaca.us_assets.cache_clear()
    attempts = []

    def down(*args, **kwargs):
        attempts.append(1)
        raise alpaca.AlpacaError("HTTP 500")

    monkeypatch.setattr(alpaca, "_get", down)
    assert not alpaca.lists("TSLA")
    assert not alpaca.lists("NVDA")
    assert len(attempts) == 1  # second call skipped during back-off
