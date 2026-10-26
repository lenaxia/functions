"""reconcile — daily DB-vs-broker drift check + schedule watchdog.

Runs after the afternoon-chain (22:50 UTC weekdays; also manually via
POST /reconcile). Three checks:

  1. book drift: DB positions (tradingpg) vs live Alpaca paper positions.
     A mismatch means the tracked book and the broker disagree — the
     exact silent failure that stranded the old paper account. Critical
     alert on any qty mismatch; cash drift alone pages as critical too
     (cash is rewritten from broker truth after every execute).
  2. schedule gap: on weekdays by the time this runs, the morning steps
     (debate/decide/execute) and score must all be in step_completions
     for today. Missing -> the cron/chain never fired (the chain alerts
     only cover runs that HAPPENED and failed; this covers runs that
     never happened).
  3. broker unreachable: Alpaca API failure is critical (can't verify
     the book).

Every run writes an events row (type='reconcile') — the observability
trail. The HTTP response is always ok:true unless the reconcile itself
crashed (the checks report their own status in 'checks').
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from trading_common import alpaca
from trading_common.db import TradingDB, utc_today
from trading_common.notify import alert

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUIRED_MORNING = ("debate", "decide", "execute")
REQUIRED_AFTERNOON = ("score",)
QTY_TOLERANCE = 1e-6
CASH_TOLERANCE = 1.0


def book_snapshot(db: TradingDB) -> tuple[dict[str, float], float]:
    positions = {sym: float(p.get("qty") or 0)
                 for sym, p in (db.positions() or {}).items()
                 if float(p.get("qty") or 0) > 0}
    row = db.get_decision("1970-01-01", "book_state")
    cash = float(row["payload"].get("cash") or 0.0) if row else 0.0
    return positions, cash


def diff_book(db_pos: dict[str, float], db_cash: float, acct: dict) -> dict:
    held = {s: float(q) for s, q in (acct.get("held") or {}).items()}
    symbols = sorted(set(db_pos) | set(held))
    qty_drift = {}
    for s in symbols:
        d, h = db_pos.get(s, 0.0), held.get(s, 0.0)
        if abs(d - h) > QTY_TOLERANCE:
            qty_drift[s] = {"db": d, "alpaca": h}
    cash_delta = round(acct.get("cash", 0.0) - db_cash, 2)
    return {
        "qty_drift": qty_drift,
        "cash": {"db": round(db_cash, 2), "alpaca": acct.get("cash"),
                 "delta": cash_delta},
        "cash_drift": abs(cash_delta) > CASH_TOLERANCE,
        "drift": bool(qty_drift) or abs(cash_delta) > CASH_TOLERANCE,
    }


def schedule_gap(db: TradingDB, today: str) -> list[str]:
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Sat/Sun: no chains scheduled
        return []
    missing = [s for s in REQUIRED_MORNING if not db.step_done(today, s)]
    missing += [s for s in REQUIRED_AFTERNOON if not db.step_done(today, s)]
    return missing


def main() -> dict:
    today = utc_today()
    db = TradingDB()
    checks: dict = {}
    try:
        db_pos, db_cash = book_snapshot(db)

        acct = {}
        try:
            api = alpaca.Alpaca()
            acct = alpaca.account_state(api)
        except Exception as e:  # noqa: BLE001
            acct = {"ok": False, "error": str(e)[:300]}

        if not acct.get("ok"):
            checks["broker"] = {"ok": False, "error": acct.get("error")}
            alert("TradingReconcileBrokerUnreachable",
                  "reconcile %s: Alpaca unreachable" % today,
                  str(acct.get("error"))[:300], severity="critical")
        else:
            d = diff_book(db_pos, db_cash, acct)
            checks["drift"] = {"ok": not d["drift"], **d}
            if d["drift"]:
                alert("TradingDriftDetected",
                      "reconcile %s: DB vs Alpaca drift" % today,
                      "qty_drift=%s cash=%s" % (d["qty_drift"], d["cash"]),
                      severity="critical")
            checks["book"] = {"ok": True, "db_positions": len(db_pos),
                              "equity": acct.get("equity"),
                              "open": acct.get("open")}

        gap = schedule_gap(db, today)
        checks["schedule"] = {"ok": not gap, "missing": gap}
        if gap:
            alert("TradingScheduleGap",
                  "reconcile %s: chains never completed" % today,
                  "missing steps: %s" % ",".join(gap), severity="critical")

        ok = all(c.get("ok") for c in checks.values())
        db.add_event("reconcile", {"date": today, "ok": ok, "checks": checks})
        logger.info("reconcile %s ok=%s checks=%s", today, ok,
                    {k: v.get("ok") for k, v in checks.items()})
    finally:
        db.close()

    return {"ok": True, "date": today, "checks": checks,
            "all_good": ok,
            "now_utc": datetime.now(timezone.utc).isoformat()}
