"""API tests with Yahoo calls stubbed out — no network."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.main as main
import backend.trader_agent as agent


@pytest.fixture
def client(monkeypatch):
    idx = pd.bdate_range(end="2026-09-30", periods=250, tz="America/New_York")
    close = pd.Series(np.linspace(100, 200, 250), index=idx)
    hist = pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1e6},
        index=idx,
    )
    monkeypatch.setattr(main, "fetch_history_with_source", lambda symbol, period="1y": (hist, "yahoo"))
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.setattr(main, "fetch_news", lambda symbol, limit=8: [])
    monkeypatch.setattr(main, "lookup_name", lambda symbol: f"{symbol} Inc")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return TestClient(main.app)


def test_any_ticker_can_be_opened(client):
    res = client.get("/api/stock/aapl")
    assert res.status_code == 200
    assert res.json()["symbol"] == "AAPL"
    for symbol in ("BRK-B", "005930.KS", "^SOX", "NQ=F"):
        assert client.get(f"/api/stock/{symbol}").status_code == 200, symbol


@pytest.mark.parametrize("bad", ["AAPL;rm", "a b", "TOOLONGSYMBOLNAME1", "$$$"])
def test_invalid_ticker_rejected(client, bad):
    assert client.get(f"/api/stock/{bad}").status_code == 400


def test_custom_watchlist(client):
    res = client.get("/api/watchlist?symbols=aapl,MSFT,aapl")
    assert [i["symbol"] for i in res.json()["watchlist"]] == ["AAPL", "MSFT"]
    default = client.get("/api/watchlist").json()["watchlist"]
    assert [i["symbol"] for i in default] == main.WATCHLIST
    too_many = ",".join(f"T{i}" for i in range(31))
    assert client.get(f"/api/watchlist?symbols={too_many}").status_code == 400


def test_password_gate(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/api/watchlist").status_code == 401
    assert client.get("/api/watchlist", auth=("me", "wrong")).status_code == 401
    assert client.get("/api/watchlist", auth=("anyone", "s3cret")).status_code == 200
    assert client.get("/healthz").status_code == 200


def test_no_cors_header_for_other_origins(client):
    res = client.get("/api/watchlist", headers={"Origin": "https://evil.example"})
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


def test_stock_rejects_unknown_period(client):
    assert client.get("/api/stock/MU?period=5d").status_code == 422
    assert client.get("/api/stock/MU?period=6mo").status_code == 200


def test_stock_survives_news_failure(client, monkeypatch):
    def boom(symbol, limit=8):
        raise RuntimeError("yahoo news down")

    monkeypatch.setattr(main, "fetch_news", boom)
    res = client.get("/api/stock/MU")
    assert res.status_code == 200
    assert res.json()["news"] == []


def test_chat_rejects_bad_roles_and_models(client):
    url = "/api/agent/chat/MU"
    bad_role = {"message": "hi", "history": [{"role": "system", "content": "x"}]}
    assert client.post(url, json=bad_role).status_code == 422
    assert client.post(url, json={"message": "hi", "model": "../../x?y"}).status_code == 422
    too_long = {"message": "hi", "history": [{"role": "user", "content": "x"}] * 51}
    assert client.post(url, json=too_long).status_code == 422


def test_analyze_falls_back_on_llm_timeout(client, monkeypatch):
    def slow(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(agent, "get_provider", lambda: "groq")
    monkeypatch.setattr(agent, "_call_groq", slow)
    res = client.post("/api/agent/analyze/MU")
    assert res.status_code == 200
    body = res.json()
    assert body["used_fallback"] is True
    assert "SETUP:" in body["analysis"]
