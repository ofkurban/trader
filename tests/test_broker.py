"""Broker tests against a fake Alpaca — no network, nothing is ever sent."""

import pytest
from fastapi.testclient import TestClient

import backend.alpaca as alpaca
import backend.broker as broker
import backend.main as main


class FakeAlpaca:
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.fail_market_sell = False

    def __call__(self, method, url, params=None, body=None, timeout=15):
        path = url.removeprefix(broker.PAPER_URL)
        assert url.startswith(broker.PAPER_URL), "orders must only go to the paper API"
        self.calls.append((method, path, body))
        if method == "POST" and path == "/v2/orders":
            if body["type"] == "market" and self.fail_market_sell:
                raise alpaca.AlpacaError("HTTP 403: market closed")
            oid = f"o{len(self.orders) + 1}"
            order = {"id": oid, "status": "accepted", "symbol": body["symbol"], "qty": body["qty"],
                     "type": body["type"], "stop_price": body.get("stop_price"), "limit_price": body.get("limit_price")}
            if body.get("order_class") == "oto":
                leg = {"id": f"{oid}-stop", "status": "held", "type": "stop", "symbol": body["symbol"],
                       "qty": body["qty"], "stop_price": body["stop_loss"]["stop_price"]}
                self.orders[leg["id"]] = leg
                order["legs"] = [leg]
            self.orders[oid] = order
            return order
        if method == "GET" and path.startswith("/v2/orders/"):
            oid = path.rsplit("/", 1)[1]
            if oid not in self.orders:
                raise alpaca.AlpacaError('HTTP 404: {"message":"order not found"}')
            return self.orders[oid]
        if method == "PATCH":
            old = self.orders[path.rsplit("/", 1)[1]]
            new = {**old, "id": old["id"] + "r", "stop_price": body["stop_price"]}
            old.update(status="replaced", replaced_by=new["id"])
            self.orders[new["id"]] = new
            return new
        if method == "DELETE":
            self.orders[path.rsplit("/", 1)[1]]["status"] = "canceled"
            return None
        if path == "/v2/account":
            return {"status": "ACTIVE", "equity": "100000", "cash": "100000", "buying_power": "400000"}
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    f = FakeAlpaca()
    monkeypatch.setattr(alpaca, "_request", f)
    monkeypatch.setattr(alpaca, "latest_trade_price", lambda symbol: 100.0)
    return f


def test_live_and_missing_keys_refused(monkeypatch):
    for name in alpaca.KEY_VARS + alpaca.SECRET_VARS:
        monkeypatch.delenv(name, raising=False)
    assert "not configured" in broker.unavailable_reason()
    monkeypatch.setenv("ALPACA_API_KEY", "AKLIVE")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    assert "paper" in broker.unavailable_reason()
    with pytest.raises(broker.BrokerError):
        broker.account()


def test_place_entry_sends_oto_with_stop(fake):
    state = broker.place_entry(trade_id="t1", symbol="BRK-B", qty=3, limit_price=99.996, stop_price=90.004)
    _, _, body = fake.calls[-1]
    assert body["order_class"] == "oto" and body["client_order_id"] == "t1"
    assert body["symbol"] == "BRK.B" and body["qty"] == "3"
    assert (body["limit_price"], body["stop_loss"]["stop_price"]) == ("100.0", "90.0")
    assert state["entry"]["status"] == "accepted"
    assert (state["stop"]["status"], state["stop"]["stop_price"]) == ("held", 90.0)


@pytest.mark.parametrize("qty,limit,stop", [(0, 100, 90), (1, 100, 100), (1, 100, 120)])
def test_place_entry_validation(fake, qty, limit, stop):
    with pytest.raises(broker.BrokerError):
        broker.place_entry(trade_id="t1", symbol="X", qty=qty, limit_price=limit, stop_price=stop)
    assert fake.calls == []


def test_raise_stop_follows_replacements(fake):
    state = broker.place_entry(trade_id="t1", symbol="X", qty=1, limit_price=95, stop_price=90)
    broker.raise_stop(state["stop"]["id"], 92)
    broker.raise_stop(state["stop"]["id"], 94)  # old id: must walk to the replacement
    latest = broker.trade_state(state["entry"]["id"])["stop"]
    assert latest["stop_price"] == 94
    with pytest.raises(broker.BrokerError, match="not above"):
        broker.raise_stop(latest["id"], 93)
    with pytest.raises(broker.BrokerError, match="last price"):
        broker.raise_stop(latest["id"], 100)


def test_close_cancels_stop_then_sells(fake):
    state = broker.place_entry(trade_id="t1", symbol="X", qty=2, limit_price=95, stop_price=90)
    out = broker.close_trade(trade_id="t1", symbol="X", qty=2, stop_order_id=state["stop"]["id"])
    methods = [(m, b["type"] if b else None) for m, _, b in fake.calls]
    assert ("DELETE", None) in methods
    assert methods[-1] == ("POST", "market")
    assert fake.calls[-1][2]["client_order_id"] == "t1-exit"
    assert out["status"] == "accepted"


def test_failed_exit_restores_the_stop(fake):
    state = broker.place_entry(trade_id="t1", symbol="X", qty=2, limit_price=95, stop_price=90)
    fake.fail_market_sell = True
    with pytest.raises(broker.BrokerError, match="re-placed"):
        broker.close_trade(trade_id="t1", symbol="X", qty=2, stop_order_id=state["stop"]["id"])
    _, _, body = fake.calls[-1]
    assert (body["type"], body["stop_price"], body["side"]) == ("stop", "90.0", "sell")


@pytest.fixture
def client(fake, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(alpaca, "lists", lambda symbol: symbol != "NOPE")
    return TestClient(main.app)


def test_api_place_and_sync(client):
    res = client.post("/api/broker/orders", json={"trade_id": "t1", "symbol": "aapl", "qty": 2,
                                                   "limit_price": 95, "stop_price": 90})
    assert res.status_code == 200
    order_id = res.json()["entry"]["id"]
    synced = client.post("/api/broker/sync", json={"trades": [{"trade_id": "t1", "order_id": order_id},
                                                              {"trade_id": "t2", "order_id": "missing"}]})
    trades = synced.json()["trades"]
    assert trades["t1"]["entry"]["status"] == "accepted"
    assert "error" in trades["t2"]
    assert client.get("/api/broker/status").json()["enabled"] is True


def test_api_rejects_bad_input(client):
    base = {"trade_id": "t1", "symbol": "AAPL", "qty": 2, "limit_price": 95, "stop_price": 90}
    assert client.post("/api/broker/orders", json={**base, "symbol": "NOPE"}).status_code == 400
    assert client.post("/api/broker/orders", json={**base, "stop_price": 99}).status_code == 400
    assert client.post("/api/broker/orders", json={**base, "trade_id": "bad id!"}).status_code == 422


def test_public_deploy_needs_password(client, monkeypatch):
    monkeypatch.setattr(main, "LOCAL_CLIENTS", set())
    body = {"trade_id": "t1", "symbol": "AAPL", "qty": 1, "limit_price": 95, "stop_price": 90}
    res = client.post("/api/broker/orders", json=body)
    assert res.status_code == 403 and "APP_PASSWORD" in res.json()["detail"]
    status = client.get("/api/broker/status").json()
    assert status["enabled"] is False and "account" not in status
    monkeypatch.setenv("APP_PASSWORD", "pw")
    assert client.post("/api/broker/orders", json=body, auth=("u", "pw")).status_code == 200
