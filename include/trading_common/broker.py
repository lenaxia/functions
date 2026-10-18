#!/usr/bin/env python3
"""Local paper broker for the self-hosted ai-trading experiment.

Replaces the off-boarded ai4trade platform ledger. Pure stdlib, NO network
calls, no LLM. The book is a JSON file (default <repo>/state/book.json)
holding cash, positions {symbol: {qty, entry}}, recent fills and meta.

Cost model: 5 bps per side, charged to cash. Buys weight the fill price into
the position's average entry; sells reduce qty and credit cash net of cost.
Invalid fills (insufficient cash or position) raise ValueError so callers
can skip and log instead of corrupting the book.
"""
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BOOK = os.path.join(HERE, "state", "book.json")
COST_BPS = 5.0  # per side


def load_book(path=None):
    with open(path or DEFAULT_BOOK) as fh:
        return json.load(fh)


def save_book(book, path=None):
    path = path or DEFAULT_BOOK
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(book, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def position_value(book, prices):
    """Mark-to-market value of all positions given {sym: price}."""
    return sum(p.get("qty", 0) * prices.get(sym, p.get("entry") or 0.0)
               for sym, p in (book.get("positions") or {}).items())


def execute(book, action, symbol, qty, price, note=""):
    """Apply one fill to the book in place; returns the fill record.

    action: 'buy' | 'sell'; qty: positive int; price: per-share fill price;
    note: free-text reason kept in the fill history.
    """
    action = (action or "").lower()
    qty = int(qty)
    if action not in ("buy", "sell") or qty <= 0 or not price or price <= 0:
        raise ValueError("bad fill: %s %s x%s @%s" % (action, symbol, qty, price))
    symbol = symbol.upper()
    value = qty * float(price)
    cost = value * COST_BPS / 10000.0
    pos = book.setdefault("positions", {}).setdefault(symbol, {"qty": 0, "entry": 0.0})
    if action == "buy":
        if book.get("cash", 0) < value + cost:
            raise ValueError("insufficient cash: %.2f < %.2f" % (book.get("cash", 0), value + cost))
        total_qty = pos.get("qty", 0) + qty
        pos["entry"] = round(((pos.get("entry") or 0.0) * pos.get("qty", 0) + value) / total_qty, 6)
        pos["qty"] = total_qty
        book["cash"] = round(book.get("cash", 0) - value - cost, 2)
    else:
        if pos.get("qty", 0) < qty:
            raise ValueError("insufficient position: %s %s < %s" % (symbol, pos.get("qty", 0), qty))
        pos["qty"] -= qty
        if pos["qty"] == 0:
            pos["entry"] = 0.0
        book["cash"] = round(book.get("cash", 0) + value - cost, 2)
    fill = {"ts": datetime.now(timezone.utc).isoformat(),
            "action": action, "symbol": symbol, "qty": qty,
            "price": round(float(price), 6), "cost": round(cost, 4),
            "note": note, "cash_after": book["cash"]}
    book.setdefault("fills", []).append(fill)
    del book["fills"][:-500]  # keep the last 500 fills
    book["updated"] = fill["ts"]
    return fill


if __name__ == "__main__":
    print(json.dumps({"ok": True, "book": DEFAULT_BOOK, "cost_bps_per_side": COST_BPS}))
