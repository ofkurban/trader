"""Alpaca **paper** trading: entry orders with an attached stop, stop raises, exits.

Orders only ever go to the paper API. Live keys (not starting with "PK") are
refused rather than routed anywhere, so nothing here can touch real money.

Each journal trade maps to one Alpaca OTO order: a limit buy plus a stop-loss
leg that activates once the buy fills. The trailing stop is applied by raising
that leg's stop price (Alpaca replaces the order, giving the leg a new id, so
state lookups follow the replacement chain). Client order ids are the journal
trade id, so a repeated click can't place a second order.
"""

from __future__ import annotations

import time
import urllib.parse

from backend import alpaca

PAPER_URL = "https://paper-api.alpaca.markets"
OPEN_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding", "pending_replace"}
MAX_QTY = 100_000


class BrokerError(RuntimeError):
    """A request the broker refused, or one we refuse before sending."""


def unavailable_reason() -> str | None:
    creds = alpaca._credentials()
    if not creds:
        return "Alpaca keys are not configured."
    if not creds[0].startswith("PK"):
        return "Only Alpaca paper keys (starting with PK) can place orders here."
    return None


def _call(method: str, path: str, **kwargs):
    reason = unavailable_reason()
    if reason:
        raise BrokerError(reason)
    try:
        return alpaca._request(method, f"{PAPER_URL}{path}", **kwargs)
    except alpaca.AlpacaError as exc:
        raise BrokerError(str(exc)) from exc


def _price(value: float) -> float:
    # Alpaca rejects sub-penny prices at or above $1
    return round(value, 2) if value >= 1 else round(value, 4)


def account() -> dict:
    a = _call("GET", "/v2/account")
    return {
        "status": a.get("status"),
        "equity": float(a.get("equity") or 0),
        "cash": float(a.get("cash") or 0),
        "buying_power": float(a.get("buying_power") or 0),
    }


def place_entry(*, trade_id: str, symbol: str, qty: int, limit_price: float, stop_price: float) -> dict:
    """Limit buy (good till cancelled) with a stop-loss leg that arms on fill."""
    if not 1 <= qty <= MAX_QTY:
        raise BrokerError(f"Quantity must be 1–{MAX_QTY} whole shares.")
    limit_price, stop_price = _price(limit_price), _price(stop_price)
    if not 0 < stop_price < limit_price:
        raise BrokerError("Stop must be below the limit price.")
    order = _call(
        "POST",
        "/v2/orders",
        body={
            "symbol": alpaca.to_alpaca_symbol(symbol),
            "qty": str(qty),
            "side": "buy",
            "type": "limit",
            "limit_price": str(limit_price),
            "time_in_force": "gtc",
            "order_class": "oto",
            "stop_loss": {"stop_price": str(stop_price)},
            "client_order_id": trade_id,
        },
    )
    return summarize(order)


def _order(order_id: str) -> dict:
    return _call("GET", f"/v2/orders/{urllib.parse.quote(order_id, safe='')}", params={"nested": "true"})


def _follow_replacements(order: dict) -> dict:
    """A replaced order points at its successor; walk to the live one."""
    for _ in range(50):
        if order.get("status") != "replaced" or not order.get("replaced_by"):
            return order
        order = _order(order["replaced_by"])
    return order


def _fill(order: dict | None) -> dict | None:
    if not order:
        return None
    return {
        "id": order["id"],
        "status": order.get("status"),
        "qty": float(order.get("qty") or 0),
        "filled_qty": float(order.get("filled_qty") or 0),
        "filled_avg_price": float(order["filled_avg_price"]) if order.get("filled_avg_price") else None,
        "filled_at": order.get("filled_at"),
        "limit_price": float(order["limit_price"]) if order.get("limit_price") else None,
        "stop_price": float(order["stop_price"]) if order.get("stop_price") else None,
        "submitted_at": order.get("submitted_at"),
    }


def summarize(order: dict) -> dict:
    legs = order.get("legs") or []
    stop_leg = next((leg for leg in legs if leg.get("type") in ("stop", "stop_limit")), None)
    return {"entry": _fill(order), "stop": _fill(_follow_replacements(stop_leg) if stop_leg else None)}


def trade_state(order_id: str, exit_order_id: str | None = None) -> dict:
    state = summarize(_order(order_id))
    state["exit"] = _fill(_order(exit_order_id)) if exit_order_id else None
    return state


def raise_stop(stop_order_id: str, new_stop: float) -> dict:
    """Move an open stop leg up (never down, never to or above the market)."""
    leg = _follow_replacements(_order(stop_order_id))
    if leg.get("status") not in OPEN_STATUSES:
        raise BrokerError(f"Stop order is {leg.get('status')}, not open.")
    current = float(leg["stop_price"])
    new_stop = _price(new_stop)
    if new_stop <= current:
        raise BrokerError(f"New stop {new_stop} is not above the current stop {current}.")
    try:
        last = alpaca.latest_trade_price(leg["symbol"])
    except alpaca.AlpacaError:
        last = None
    if last is not None and new_stop >= last:
        raise BrokerError(f"New stop {new_stop} is at or above the last price {last}.")
    replaced = _call(
        "PATCH", f"/v2/orders/{urllib.parse.quote(leg['id'], safe='')}", body={"stop_price": str(new_stop)}
    )
    return _fill(replaced)


def cancel(order_id: str) -> None:
    """Cancel an order (cancelling an unfilled entry also cancels its stop leg)."""
    _call("DELETE", f"/v2/orders/{urllib.parse.quote(order_id, safe='')}")


def close_trade(*, trade_id: str, symbol: str, qty: int, stop_order_id: str | None) -> dict:
    """Sell at market. The stop leg holds the shares, so cancel it first."""
    restore_stop = None
    if stop_order_id:
        leg = _follow_replacements(_order(stop_order_id))
        if leg.get("status") in OPEN_STATUSES:
            restore_stop = float(leg["stop_price"])
            cancel(leg["id"])
            for _ in range(20):  # cancellation is asynchronous
                if _order(leg["id"]).get("status") in ("canceled", "filled"):
                    break
                time.sleep(0.25)
            else:
                raise BrokerError("Stop order didn't cancel in time; try again.")
            if _order(leg["id"]).get("status") == "filled":
                raise BrokerError("The stop filled first — the position is already closed.")
    try:
        order = _call(
            "POST",
            "/v2/orders",
            body={
                "symbol": alpaca.to_alpaca_symbol(symbol),
                "qty": str(qty),
                "side": "sell",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": f"{trade_id}-exit",
            },
        )
    except BrokerError as exc:
        if restore_stop is None:
            raise
        # Never leave the position unprotected: put the stop back as a standalone order
        try:
            _call(
                "POST",
                "/v2/orders",
                body={
                    "symbol": alpaca.to_alpaca_symbol(symbol),
                    "qty": str(qty),
                    "side": "sell",
                    "type": "stop",
                    "stop_price": str(restore_stop),
                    "time_in_force": "gtc",
                    "client_order_id": f"{trade_id}-stop-restored",
                },
            )
        except BrokerError as restore_exc:
            raise BrokerError(
                f"Market sell failed ({exc}) and the stop could not be restored ({restore_exc}). "
                "The position has NO stop — set one in Alpaca now."
            ) from exc
        raise BrokerError(
            f"Market sell failed ({exc}). Your stop at {restore_stop} was re-placed as a separate "
            "order — manage it in Alpaca."
        ) from exc
    return _fill(order)
