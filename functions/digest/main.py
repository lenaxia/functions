"""digest — plain-language summary + step watchdog.

Reads step_completions for the UTC day as a watchdog (missing morning/afternoon
steps get flagged in the summary), builds a plain-language digest of the day
(debate verdicts, decide trades/skips/vetoes, execute outcome, forward +
control scoreboard, book equity) and posts it BOTH to the events table
(type='digest') and to the git journal (shadow/<date>/digest.json — the
state/log.txt equivalent during the comparison week).

Idempotent per run_date.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from trading_common import gitstate
from trading_common.db import TradingDB
from trading_common.secrets import flag

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXPECTED_STEPS = ["debate", "decide", "execute", "score"]


def watchdog(db: TradingDB, today: str, is_weekday: bool) -> list[str]:
    if not is_weekday:
        return []  # morning steps legitimately absent on weekends
    return [s for s in EXPECTED_STEPS if not db.step_done(today, s)]


def build_summary(db: TradingDB, today: str, missing: list[str]) -> str:
    lines = []
    if missing:
        lines.append("WATCHDOG: missing steps today: %s." % ", ".join(missing))
    else:
        lines.append("All expected steps completed today (debate, decide, execute, score).")

    debate = (db.get_decision(today, "debate") or {}).get("payload") or {}
    if debate:
        verdicts = ", ".join("%s=%s" % (v.get("symbol"), v.get("verdict"))
                             for v in debate.get("verdicts", [])) or "no candidates"
        lines.append("DD gate: %s." % verdicts)

    decide = (db.get_decision(today, "decide") or {}).get("payload") or {}
    if decide:
        if decide.get("debate_fallback"):
            lines.append("Debate was missing or failed — decision ran with the "
                         "fallback flag (no vetoes applied).")
        for e in decide.get("exits", []):
            lines.append("EXIT %s x%s (%s)." % (e["symbol"], e["quantity"], e["reason"]))
        for e in decide.get("entries", []):
            lines.append("ENTRY %s x%s (~$%s, %s)." % (
                e["symbol"], e["quantity"], e.get("est_cost"), e.get("reason")))
        for g in decide.get("gate_log", []):
            if g.get("tag") in ("SKIP_BUY", "DD_VETO", "SKIP_SELL"):
                lines.append("%s: %s." % (g["tag"], g["line"]))
        acct = decide.get("account") or {}
        if acct:
            lines.append("Book: equity $%s (cash $%s, %d positions)."
                         % (acct.get("equity"), acct.get("cash"), acct.get("positions_held")))

    execute = (db.get_decision(today, "execute") or {}).get("payload") or {}
    if execute:
        mode = execute.get("mode", "?")
        orders = execute.get("orders") or []
        if execute.get("note"):
            lines.append("Execute (%s): %s." % (mode, execute["note"]))
        for o in orders:
            lines.append("WOULD ORDER (%s): %s %s x%s @~%s."
                         % (mode, o["side"], o["symbol"], o["qty"], o.get("assumed_price")))
        for r in execute.get("rejections") or []:
            lines.append("REJECTED: %s %s x%s — %s."
                         % (r["side"], r["symbol"], r["qty"], r["reason"]))

    score = (db.get_decision(today, "score") or {}).get("payload") or {}
    fwd = score.get("forward") or {}
    if fwd:
        spy = (fwd.get("benchmark") or {}).get("SPY", {})
        lines.append("Forward OOS: SPY %+.2f%%; %s."
                     % (spy.get("return_to_date", 0) * 100,
                        "; ".join("%s %+.2f%%" % (k, v.get("return_to_date", 0) * 100)
                                  for k, v in sorted((fwd.get("variants") or {}).items()))))
    ctrl = score.get("control") or {}
    if ctrl.get("equity") is not None:
        lines.append("Control clone: $%s (%+.2f%% since anchor)."
                     % (ctrl.get("equity"), ctrl.get("return_pct", 0)))

    if len(lines) == 1:
        lines.append("No decisions recorded yet today.")
    return "\n".join(lines)


def main() -> dict:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    db = TradingDB()
    try:
        if db.step_done(today, "digest"):
            return {"ok": True, "date": today, "already_done": True, "step": "digest"}

        missing = watchdog(db, today, now.weekday() <= 4)
        text = build_summary(db, today, missing)
        payload = {"ok": True, "date": today, "missing_steps": missing, "text": text}
        db.record_decision(today, "digest", payload, status="ok")
        db.mark_step(today, "digest")
        db.add_event("digest", {"date": today, "missing_steps": missing, "text": text})
        # journal unconditionally — shadow/ is the audit mirror for both modes
        gitstate.journal(today, "digest", {"missing_steps": missing, "text": text})
        logger.info("digest:\n%s", text)
        return payload
    finally:
        db.close()
