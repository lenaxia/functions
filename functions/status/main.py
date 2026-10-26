"""status — read-only observability endpoint (GET /status).

One JSON snapshot of the system state with zero external calls (no
Alpaca, no Yahoo, no LLM — DB only, so it is fast and cannot fail
because an upstream is down):

  system:    halt state, last chain event, last reconcile event
  book:      tracked positions count + cash sentinel + latest execute
             account snapshot (equity / broker position count)
  steps:    step_completions for the last 7 days (watchdog view)
  equity:    latest mark per strategy curve (pilot, control, fwd:*)
  recent:    last 10 events (audit tail)

No secrets in the response. Authelia sits in front of this route at
the edge (two_factor), same as the other trading endpoints.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from trading_common.db import TradingDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def halt_state(db: TradingDB) -> dict:
    row = db.one(
        "SELECT type FROM events WHERE type IN ('halt_set','halt_cleared') "
        "ORDER BY id DESC LIMIT 1")
    halted = bool(row and row[0] == "halt_set")
    return {"halted": halted, "gates_open": not halted}


def latest_equity(db: TradingDB) -> dict:
    rows = db.all(
        "SELECT DISTINCT ON (strategy) strategy, as_of, equity "
        "FROM equity_series ORDER BY strategy, as_of DESC")
    return {r[0]: {"as_of": str(r[1]), "equity": float(r[2])} for r in rows}


def recent_steps(db: TradingDB, days: int = 7) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows = db.all(
        "SELECT run_date, step, ts FROM step_completions "
        "WHERE run_date >= %s ORDER BY run_date DESC, step", (since,))
    return [{"date": str(r[0]), "step": r[1], "ts": r[2].isoformat()} for r in rows]


def last_event(db: TradingDB, type_: str) -> dict | None:
    row = db.one(
        "SELECT ts, detail FROM events WHERE type = %s "
        "ORDER BY id DESC LIMIT 1", (type_,))
    if not row:
        return None
    return {"ts": row[0].isoformat(), "detail": row[1]}


def main() -> dict:
    db = TradingDB()
    try:
        book_row = db.get_decision("1970-01-01", "book_state")
        positions = db.positions() or {}
        held = {s: p for s, p in positions.items() if (p.get("qty") or 0) > 0}
        exec_row = db.one(
            "SELECT run_date, payload FROM decisions WHERE step = 'execute' "
            "ORDER BY run_date DESC LIMIT 1")
        acct = {}
        if exec_row:
            p = exec_row[1] or {}
            acct = {"as_of": str(exec_row[0]), "mode": p.get("mode"),
                    "equity": p.get("equity"), "cash": p.get("cash"),
                    "alpaca_positions": p.get("alpaca_positions"),
                    "halted": (p.get("halt") or {}).get("halted")}

        return {
            "ok": True,
            "system": {"halt": halt_state(db),
                       "last_chain": last_event(db, "chain"),
                       "last_reconcile": last_event(db, "reconcile")},
            "book": {"cash": book_row["payload"].get("cash") if book_row else None,
                     "positions": len(held),
                     "symbols": sorted(held),
                     "account": acct},
            "steps": recent_steps(db),
            "equity": latest_equity(db),
            "recent_events": [
                {"id": r[0], "ts": r[1].isoformat(), "type": r[2]}
                for r in db.all(
                    "SELECT id, ts, type FROM events ORDER BY id DESC LIMIT 10")],
            "now_utc": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()
