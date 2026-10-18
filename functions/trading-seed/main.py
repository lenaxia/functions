"""trading-seed — one-shot (idempotent) schema + seed for the trading DB.

PHASE 1 deliverable, runnable as a Fission function (manual HTTP trigger):
  1. applies include/trading_common/schema.sql (also auto-applied on every
     function connect; running it here is belt-and-braces)
  2. seeds from the CURRENT GIT STATE of lenaxia/ai-trading@main (GitHub
     token from the `trading` secret):
       - state/book.json          -> positions + book_state cash sentinel
       - control_state.json       -> equity_series('control') history
                                    + control_state sentinel (shares/anchor)
       - forward/forward_state.json -> equity_series('fwd:<variant>') series
                                    + 'fwd:SPY' benchmark + forward_meta
       - personal_sleeve.json     -> sleeve_weights sentinel (control input)

Re-running never double-appends (all writes are upserts keyed by
symbol / (strategy, as_of) / (run_date, step)).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from trading_common import gitstate
from trading_common.db import TradingDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def seed_book(db: TradingDB) -> dict:
    book = gitstate.read_json("state/book.json")
    if not book:
        raise RuntimeError("state/book.json unavailable from lenaxia/ai-trading "
                           "(check GITHUB_TOKEN)")
    db.set_positions(book.get("positions") or {})
    meta = {k: book[k] for k in ("cash", "meta") if k in book}
    db.record_decision("1970-01-01", "book_state", meta, status="ok")
    positions = sum(1 for p in (book.get("positions") or {}).values()
                    if (p.get("qty") or 0) > 0)
    return {"cash": book.get("cash"), "positions": positions}


def seed_control(db: TradingDB) -> dict:
    st = gitstate.read_json("control_state.json")
    if not st:
        return {"skipped": "control_state.json unavailable"}
    for row in st.get("history", []):
        db.upsert_equity("control", row["date"], row["equity"])
    keep = {k: st[k] for k in ("shares", "anchor_date", "anchor_prices",
                               "anchor_note", "cash_pct", "history") if k in st}
    db.record_decision("1970-01-01", "control_state", keep, status="ok")
    return {"history_rows": len(st.get("history", [])),
            "anchor_date": st.get("anchor_date")}


def seed_forward(db: TradingDB) -> dict:
    st = gitstate.read_json("forward/forward_state.json")
    if not st:
        return {"skipped": "forward/forward_state.json unavailable"}
    rows = 0
    for vid, v in (st.get("variants") or {}).items():
        for row in v.get("equity_series", []):
            if "note" not in row:
                db.upsert_equity("fwd:" + vid, row["date"], row["equity"])
                rows += 1
    bench_rows = 0
    for row in (st.get("benchmark") or {}).get("SPY", {}).get("series", []):
        if "note" not in row:
            db.upsert_equity("fwd:SPY", row["date"], row["equity"])
            bench_rows += 1
    meta = {k: st.get("meta", {}).get(k) for k in
            ("forward_start", "start_equity", "cost_bps_per_side",
             "last_data_date", "universe_traded")}
    db.record_decision("1970-01-01", "forward_meta", meta, status="ok")
    return {"variants": len(st.get("variants") or {}), "series_rows": rows,
            "benchmark_rows": bench_rows,
            "forward_start": meta.get("forward_start")}


def seed_sleeve(db: TradingDB) -> dict:
    sleeve = gitstate.read_json("personal_sleeve.json")
    if not sleeve:
        return {"skipped": "personal_sleeve.json unavailable"}
    db.record_decision("1970-01-01", "sleeve_weights", sleeve, status="ok")
    return {"stored": True, "as_of": sleeve.get("as_of")}


def main() -> dict:
    now = datetime.now(timezone.utc)
    db = TradingDB()  # connect applies schema.sql idempotently
    try:
        out = {"ok": True, "seeded_utc": now.isoformat(),
               "book": seed_book(db),
               "control": seed_control(db),
               "forward": seed_forward(db),
               "sleeve": seed_sleeve(db)}
        db.add_event("seed", {"result": {k: v for k, v in out.items() if k != "ok"}})
        logger.info("seed complete: %s", out)
        return out
    except Exception as e:  # noqa: BLE001
        db.add_event("seed", {"error": str(e)[:300]})
        return {"ok": False, "error": str(e)[:300]}
    finally:
        db.close()
