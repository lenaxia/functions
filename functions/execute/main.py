"""execute — Alpaca bridge, port of alpaca_bridge.py (paper/live reconcile)
with halt/resume from alpaca/halt_resume_check.py.

Target book = current DB positions adjusted by the decide step's gated plan
(exits removed, approved entries added — the daily_run.py fill loop
expressed as a reconcile). Reconciles the Alpaca account to that target:

  - sells first, then buys; market orders, day TIF
  - buys get bracket orders (-5% stop / +15% TP off the reference price)
  - caps from PILOT_NOTIONAL (verbatim live-mode math: per-order 30%,
    book 104%, run cumulative 104%, kill-line 85% -> halt)
  - halt is market-dictated: resume only when the panic gates re-open
    (VIX <= 70th pct of 252d AND HYG/LQD credit stress cleared); gates
    failing closed keeps the halt. No fixed dwell (sensitivity T3).

SHADOW=1 (deployment default): paper-read-only — account/positions/clock
are read, the full order plan is computed and logged (events +
decisions(status='shadow') + git journal + fills marked shadow=true), but NO
order is placed and the DB book is not mutated.

Live (SHADOW=0): orders placed; fills recorded with assumed price and
slippage; the DB book is updated with broker.py's verbatim fill math.

Idempotent per run_date.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from trading_common import alpaca as ap
from trading_common import broker, gitstate
from trading_common.db import TradingDB
from trading_common.secrets import flag

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def halt_state(db: TradingDB) -> dict | None:
    """Latest halt_set without a halt_cleared (events table)."""
    cleared = db.one(
        "SELECT id FROM events WHERE type = 'halt_cleared' "
        "ORDER BY id DESC LIMIT 1")
    cleared_id = cleared[0] if cleared else None
    latest = db.one(
        "SELECT id, detail FROM events WHERE type = 'halt_set' AND id > COALESCE(%s, 0) "
        "ORDER BY id DESC LIMIT 1", (cleared_id or 0,))
    if latest is None:
        return None
    import json
    detail = latest[1]
    return detail if isinstance(detail, dict) else json.loads(detail)


def check_halt(db: TradingDB) -> dict:
    """Halt gate: if halted, try market-dictated resume (gates_open)."""
    halted = halt_state(db)
    if not halted:
        return {"halted": False}
    gates = ap.gates_open()
    if gates is True:
        db.add_event("halt_cleared", {"reason": "panic gates re-open (market-dictated)"})
        logger.info("halt auto-resumed (gates open)")
        return {"halted": False, "resumed": True}
    logger.info("stay_halted (gates=%s)", gates)
    return {"halted": True, "gates_open": gates}


def target_book(db: TradingDB, decide_payload: dict) -> dict[str, float]:
    """Current positions adjusted by the gated plan."""
    book = db.positions()
    target = {sym: float(p["qty"]) for sym, p in book.items()}
    for e in decide_payload.get("exits", []):
        sym = e["symbol"]
        target[sym] = max(0.0, target.get(sym, 0.0) - float(e["quantity"]))
    for e in decide_payload.get("entries", []):
        sym = e["symbol"]
        target[sym] = target.get(sym, 0.0) + float(e["quantity"])
    return {s: q for s, q in target.items() if q > 0}


def main() -> dict:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    shadow = flag("SHADOW", "1")
    db = TradingDB()
    try:
        if db.step_done(today, "execute"):
            return {"ok": True, "date": today, "already_done": True, "step": "execute"}

        row = db.get_decision(today, "decide")
        if not row or row["status"] != "ok" or not row["payload"].get("ok"):
            out = {"ok": False, "error": "decide step for %s not available" % today}
            db.record_decision(today, "execute", out, status="error")
            return out
        decide_payload = row["payload"]

        halt = check_halt(db)
        caps = ap.pilot_caps()
        target = target_book(db, decide_payload)
        prices = decide_payload.get("prices") or {}

        api = ap.Alpaca()
        acct = ap.account_state(api)
        result = {"now": ap.now_iso(), "mode": "shadow" if shadow else "live",
                  "date": today, "target_positions": len(target),
                  "caps": caps, "halt": halt}

        if not acct.get("ok"):
            result.update({"ok": False, "error": "alpaca read failed: %s" % acct.get("error")})
            db.record_decision(today, "execute", result, status="error")
            return result
        result.update({"open": acct["open"], "equity": acct["equity"],
                       "cash": acct["cash"], "alpaca_positions": len(acct["held"]),
                       "held_book_value": acct["held_value"]})

        pending = ap.diffs(target, acct["held"])
        result["pending_diff"] = pending

        if halt.get("halted"):
            result.update({"ok": True, "note": "halted — no orders (market-dictated resume)"})
        elif not pending:
            result.update({"ok": True, "note": "in sync"})
        elif not acct["open"]:
            result.update({"ok": True, "note": "market closed — diff queued for next open run"})
        elif acct["equity"] < caps["equity_kill_line"]:
            db.add_event("halt_set", {"halted_at": ap.now_iso(),
                                      "reason": "equity < kill-line",
                                      "equity": acct["equity"],
                                      "kill_line": caps["equity_kill_line"]})
            result.update({"ok": True, "note": "KILL-LINE tripped — halted; auto-resume "
                                               "when panic gates re-open (market-dictated)"})
        else:
            orders, rejections = ap.evaluate_caps(pending, prices, acct["held_value"], caps)
            result.update({"orders": orders, "rejections": rejections})
            if shadow:
                for o in orders:
                    db.record_fill(o["symbol"], o["side"], o["qty"], None,
                                   o["assumed_price"], note="shadow would-order", shadow=True)
                result["note"] = "shadow: paper-read-only — %d order(s) logged, none placed" % len(orders)
            else:
                place_orders(db, api, orders, result)
            result["ok"] = True

        db.record_decision(today, "execute", result,
                           status="shadow" if shadow else "ok")
        db.mark_step(today, "execute")
        db.add_event("execute", {"date": today, "mode": result["mode"],
                                 "orders": len(result.get("orders") or []),
                                 "rejections": len(result.get("rejections") or []),
                                 "note": result.get("note", "")})
        if shadow:
            gitstate.journal(today, "execute", result)
        return result
    finally:
        db.close()


def place_orders(db: TradingDB, api: ap.Alpaca, orders: list[dict], result: dict) -> None:
    """Live path: place orders (sells first — evaluate_caps preserves order),
    attach brackets on buys, then record fills + update the DB book with
    broker.py's verbatim fill math."""
    placed = []
    for o in orders:
        body = {"symbol": o["symbol"], "qty": int(o["qty"]), "side": o["side"],
                "type": "market", "time_in_force": "day"}
        ref = None
        if o["side"] == "buy":
            ref = o.get("assumed_price")
        body = ap.bracket_body(body, o["symbol"], ref)
        resp = api.api("POST", "/orders", body)
        placed.append({**o, "bracket": body.get("order_class") == "bracket",
                       "ok": "_error" not in resp, "id": resp.get("id"),
                       "err": resp.get("_error")})
    result["orders"] = placed

    # fill prices where already available (market orders usually fill fast)
    book = {"cash": _book_cash(db), "positions": db.positions()}
    for o in placed:
        if not o.get("ok"):
            continue
        price = _filled_price(api, o.get("id"))
        assumed = o.get("assumed_price")
        if o["side"] == "buy" and price:
            db.record_fill(o["symbol"], "buy", o["qty"], price, assumed,
                           note="live bracket buy")
            try:
                broker.execute(book, "buy", o["symbol"], int(o["qty"]), price,
                               "fission execute")
            except ValueError as e:
                logger.warning("book buy rejected: %s", e)
        elif o["side"] == "sell":
            db.record_fill(o["symbol"], "sell", o["qty"], price, assumed,
                           note="live sell")
            try:
                broker.execute(book, "sell", o["symbol"], int(o["qty"]),
                               price or assumed or 0.0, "fission execute")
            except ValueError as e:
                logger.warning("book sell rejected: %s", e)
    db.set_positions(book.get("positions") or {})
    db.record_decision("1970-01-01", "book_state",
                       {"cash": book.get("cash", 0.0)}, status="ok")


def _filled_price(api: ap.Alpaca, order_id: str | None) -> float | None:
    if not order_id:
        return None
    o = api.api("GET", "/orders/" + order_id)
    try:
        if o.get("status") in ("filled", "partially_filled") and o.get("filled_avg_price"):
            return float(o["filled_avg_price"])
    except Exception:
        pass
    return None


def _book_cash(db: TradingDB) -> float:
    row = db.get_decision("1970-01-01", "book_state")
    return float(row["payload"].get("cash") or 0.0) if row else 0.0
