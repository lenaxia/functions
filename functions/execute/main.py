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
        # journal unconditionally — shadow/ is the audit mirror for BOTH
        # modes now (cutover made it live-only, which went dark 2026-10-26)
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
        if o["side"] == "sell":
            # decide-driven exits must not race a symbol's open GTC bracket
            # legs (a surviving stop after our own sell = double-sell/short)
            cancel_open_legs(api, o["symbol"])
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

    # Poll orders to terminal state before recording: a single-shot price
    # check raced slow fills on 2026-10-27 and dropped DHR from the book.
    # Shared budget so N orders always fit the function timeout.
    deadline = time.monotonic() + FILL_POLL_BUDGET_SECONDS
    book = {"cash": _book_cash(db), "positions": db.positions()}
    for o in placed:
        if not o.get("ok"):
            continue
        price = _filled_price(api, o.get("id"), deadline)
        assumed = o.get("assumed_price")
        if price:
            db.record_fill(o["symbol"], o["side"], o["qty"], price, assumed,
                           note="live bracket buy" if o["side"] == "buy" else "live sell")
            side = "buy" if o["side"] == "buy" else "sell"
            try:
                broker.execute(book, side, o["symbol"], int(o["qty"]), price,
                               "fission execute")
            except ValueError as e:
                logger.warning("book %s rejected: %s", side, e)
        else:
            # still not terminal when the budget ran out: never fake a fill.
            # The broker-truth sync below picks the position up; the fill row
            # lands on a later cycle.
            o["fill_pending"] = True
            logger.warning("%s %s: no terminal fill within budget", o["side"], o["symbol"])

    # Broker truth is authoritative in live mode: sync positions (qty +
    # avg entry) and cash straight off the account so the book can never
    # drift from a missed/partial local update.
    acct_pos = api.api("GET", "/positions")
    if isinstance(acct_pos, list):
        db.set_positions({p["symbol"]: {"qty": float(p["qty"] or 0),
                                        "entry": float(p.get("avg_entry_price") or 0.0)}
                          for p in acct_pos if float(p.get("qty") or 0) > 0})
    else:
        logger.warning("positions sync failed (%s); keeping local book", acct_pos)
        db.set_positions(book.get("positions") or {})
    acct = api.api("GET", "/account")
    cash = book.get("cash", 0.0)
    if isinstance(acct, dict) and acct.get("cash") is not None:
        cash = float(acct["cash"])
    else:
        logger.warning("account sync failed (%s); keeping local cash", acct)
    db.record_decision("1970-01-01", "book_state", {"cash": cash}, status="ok")


def cancel_open_legs(api: ap.Alpaca, symbol: str) -> int:
    """Cancel this symbol's open orders (bracket stop/TP legs). Returns the
    count canceled; failures are logged, never raised (the sell must go on)."""
    try:
        open_o = api.api("GET", "/orders?status=open&symbols=" + symbol)
        n = 0
        for leg in open_o if isinstance(open_o, list) else []:
            api.api("DELETE", "/orders/" + leg["id"])
            n += 1
        if n:
            logger.info("canceled %d open leg(s) for %s", n, symbol)
        return n
    except Exception as e:  # noqa: BLE001
        logger.warning("cancel_open_legs(%s) failed: %s", symbol, e)
        return 0


def _filled_price(api: ap.Alpaca, order_id: str | None,
                  deadline: float | None = None,
                  interval: float = 3.0) -> float | None:
    """Poll the order until it reaches a terminal state (or the deadline)
    and return the average fill price; None when unknown/unfilled."""
    if not order_id:
        return None
    while True:
        o = api.api("GET", "/orders/" + order_id)
        if isinstance(o, dict):
            status = o.get("status")
            if status in ("filled", "partially_filled") and o.get("filled_avg_price"):
                return float(o["filled_avg_price"])
            if status in ("canceled", "expired", "rejected", "done_for_day"):
                return None
        if deadline is not None and time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def _book_cash(db: TradingDB) -> float:
    row = db.get_decision("1970-01-01", "book_state")
    return float(row["payload"].get("cash") or 0.0) if row else 0.0
