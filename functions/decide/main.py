"""decide — marketdata sweep + trade.py rules + gates.

Port of ai-trading/trade.py (via trading_common.trade_rules, verbatim) plus
the daily_run.py gate chain (via trading_common.gates, verbatim): DD vetoes
(veto-only; missing/stale/failed debate = no vetoes, never blocks), headline
risk screen, market-hours guard, max-2-trades/day, exits before entries.

Book source during shadow (SHADOW=1, default): re-synced from
lenaxia/ai-trading state/book.json (git is the source of truth for the
comparison week). Live mode: positions + cash from the trading DB.

Writes decisions(run_date, 'decide') with the gated plan (exits, approved
entries, gate log, prices for execute) + step_completions. Idempotent per
run_date.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from trading_common.db import TradingDB
from trading_common import alpaca as ap
from trading_common import gitstate, marketdata, trade_rules
from trading_common.secrets import flag

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def sync_book_from_git(db: TradingDB) -> dict | None:
    """Shadow mode: positions/cash mirror the GHA-run book.json."""
    book = gitstate.read_json("state/book.json")
    if not book:
        return None
    db.set_positions(book.get("positions") or {})
    meta = {k: book[k] for k in ("cash", "meta") if k in book}
    db.record_decision("1970-01-01", "book_state", meta, status="ok")
    return book


def sync_book_from_broker(db: TradingDB) -> dict | None:
    """Live mode: mirror positions (qty + avg entry) and cash from broker
    truth BEFORE computing the diff. With persistent GTC brackets a stop
    can fire overnight; deciding off a stale DB book would exit ghost
    positions and size entries off stale cash. Fail-open: keep the DB
    book when the broker is unreachable (reconcile flags the drift)."""
    try:
        api = ap.Alpaca()
        acct = ap.account_state(api)
        if not acct.get("ok"):
            logger.warning("broker book sync failed: %s", acct.get("error"))
            return None
        pos = api.api("GET", "/positions")
        if not isinstance(pos, list):
            logger.warning("broker positions sync failed: %s", pos)
            return None
        db.set_positions({p["symbol"]: {"qty": float(p.get("qty") or 0),
                                        "entry": float(p.get("avg_entry_price") or 0.0)}
                          for p in pos if float(p.get("qty") or 0) > 0})
        db.record_decision("1970-01-01", "book_state",
                           {"cash": float(acct.get("cash") or 0.0)}, status="ok")
        logger.info("book synced from broker: %d positions, cash %s",
                    len(pos), acct.get("cash"))
        return acct
    except Exception as e:  # noqa: BLE001
        logger.warning("broker book sync failed: %s", e)
        return None


def load_book(db: TradingDB) -> dict | None:
    row = db.get_decision("1970-01-01", "book_state")
    cash = float(row["payload"].get("cash") or 0.0) if row else 0.0
    positions = db.positions()
    if not positions and not row:
        return None
    return {"cash": cash, "positions": positions}


def main() -> dict:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    db = TradingDB()
    try:
        if db.step_done(today, "decide"):
            return {"ok": True, "date": today, "already_done": True, "step": "decide"}

        shadow = flag("SHADOW", "1")
        if shadow:
            if sync_book_from_git(db) is None:
                logger.warning("shadow book sync failed; using DB book state")
        else:
            if sync_book_from_broker(db) is None:
                logger.warning("live book sync failed; using DB book state")
        book = load_book(db)
        if book is None:
            out = {"ok": False, "error": "no book state (run trading-seed first)"}
            db.record_decision(today, "decide", out, status="error")
            return out

        snap = marketdata.snapshot()
        decision = trade_rules.build_decision(book, snap, now=now)
        prices = {s["sym"]: s.get("price") for s in snap.get("stocks", [])
                  if isinstance(s, dict) and "error" not in s}
        logger.info("data_snapshot %s", ",".join(prices))

        debate_row = db.get_decision(today, "debate")
        debate = debate_row["payload"] if debate_row else None
        debate_fallback = not (debate and debate_row["status"] == "ok"
                               and debate.get("date") == today)

        trades_today = db.trades_today(today)
        exits_ok, gated_entries, gate_log = trading_gates(decision, debate, trades_today)

        # prices for execute's cap math: every book symbol plus the plan's
        # (execute's diff covers the union of DB book and broker holdings —
        # an unpriced symbol there must be a visible rejection, never a
        # zero-notional order)
        price_syms = (set(book.get("positions") or {})
                      | {e["symbol"] for e in exits_ok}
                      | {e["symbol"] for e in gated_entries})

        payload = {
            "ok": True,
            "date": today,
            "now_utc": now.isoformat(),
            "market_open": decision.get("market_open"),
            "account": decision.get("account"),
            "debate_fallback": debate_fallback,
            "vetoes": sorted({r.get("symbol") for r in (debate or {}).get("verdicts", [])
                              if r.get("verdict") == "veto"}),
            "exits": exits_ok,
            "entries": gated_entries,
            "gate_log": gate_log,
            "trades_today_before": trades_today,
            "prices": {s: prices.get(s) for s in sorted(price_syms)},
        }
        db.record_decision(today, "decide", payload, status="ok")
        db.mark_step(today, "decide")
        db.add_event("decide", {"date": today,
                                "exits": [e["symbol"] for e in exits_ok],
                                "entries": [e["symbol"] for e in gated_entries],
                                "debate_fallback": debate_fallback})
        # journal unconditionally — shadow/ is the audit mirror for both modes
        gitstate.journal(today, "decide", payload)
        return payload
    finally:
        db.close()


def trading_gates(decision, debate, trades_today):
    from trading_common.gates import apply_gates
    return apply_gates(decision, debate, trades_today)
