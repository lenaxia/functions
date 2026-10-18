"""debate — DD gate, port of ai-trading/dd_screen.py (veto-only authority).

Runs the trade-decision engine for today's candidates (same as dd_screen's
run_trade_py step, in-process), then one LLM debate per symbol (single round,
temperature 0 — mirroring MAX_DEBATE_ROUNDS=1) via the OpenAI-compatible
endpoint from the `trading` secret. Verdict mapping is verbatim:
buy/overweight -> pass, hold -> pass_with_caution, underweight/sell -> veto,
anything else (incl. errors/timeouts) -> abstain. The LLM can only REMOVE
candidates, never add. Per-symbol 20-minute timeout, MAX 3 candidates.

Output: decisions(run_date='today', step='debate') + step_completions.
Idempotent per run_date.

Deviation from dd_screen.py (documented in the PR): the TradingAgents
multi-agent graph is not vendored into Fission; the debate is a single
structured chat completion per symbol over the same inputs (portfolio,
candidate reason, headlines) producing the same rating space. Veto-only
semantics, mapping, candidate cap and timeouts are unchanged.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from trading_common import llm
from trading_common.db import TradingDB, utc_today
from trading_common import gitstate
from trading_common.secrets import flag
from trading_common import marketdata, trade_rules

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_CANDIDATES = 3
SYMBOL_TIMEOUT_S = 20 * 60

MAPPING = {"buy": "pass", "overweight": "pass", "hold": "pass_with_caution",
           "underweight": "veto", "sell": "veto", "review": "abstain"}

SYSTEM_PROMPT = (
    "You are a due-diligence debate panel for a single stock. Argue bull vs "
    "bear in one round each (short paragraphs), then give the portfolio "
    "manager's final rating. You may only REMOVE buy candidates, never add "
    "new ones. End your reply with exactly one line: 'RATING: <buy|overweight"
    "|hold|underweight|sell|review>'.")


def load_book(db: TradingDB) -> dict:
    """Book for candidate generation: positions table + cash sentinel."""
    row = db.get_decision("1970-01-01", "book_state")
    cash = 0.0
    if row:
        cash = float(row["payload"].get("cash") or 0.0)
    return {"cash": cash, "positions": db.positions()}


def candidates(db: TradingDB) -> tuple[list[str], dict]:
    """Port of dd_screen.run_trade_py + candidate slicing."""
    snap = marketdata.snapshot()
    decision = trade_rules.build_decision(load_book(db), snap)
    if not decision.get("ok"):
        return [], decision
    cands = [e["symbol"] for e in decision.get("entries", [])][:MAX_CANDIDATES]
    return cands, decision


def debate_symbol(sym: str, portfolio: dict, entry: dict, result: dict) -> None:
    """Port of dd_screen.ta_debate: single LLM call, verbatim verdict mapping.

    Runs in a thread; the caller enforces SYMBOL_TIMEOUT_S.
    """
    try:
        headlines = "\n".join("- " + h for h in (entry.get("headlines") or [])[:5])
        user = (
            "Candidate buy: %s\nReason: %s\nRecent headlines:\n%s\n\n"
            "Portfolio: cash $%.2f, holdings %s\n\n"
            "Debate this entry and end with the RATING line."
            % (sym, entry.get("reason", ""), headlines or "- (none)",
               portfolio.get("cash", 0.0),
               portfolio.get("holdings", {})))
        out = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user}], temperature=0.0)
        rating = None
        for line in reversed(out.strip().splitlines()):
            if line.strip().lower().startswith("rating:"):
                rating = line.split(":", 1)[1].strip().lower()
                break
        verdict = MAPPING.get(rating or "", "abstain")
        result.update({"symbol": sym, "verdict": verdict, "rating": rating,
                       "thesis_excerpt": out[:400], "error": None})
    except Exception as e:  # noqa: BLE001 - abstain, never block
        result.update({"symbol": sym, "verdict": "abstain", "rating": None,
                       "thesis_excerpt": "", "error": str(e)[:200]})


def main() -> dict:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    db = TradingDB()
    try:
        if db.step_done(today, "debate"):
            return {"ok": True, "date": today, "already_done": True, "step": "debate"}

        cands, decision = candidates(db)
        holdings = {k: v["qty"] for k, v in (db.positions()).items() if v.get("qty")}
        portfolio = {"cash": decision.get("account", {}).get("cash", 100000),
                     "holdings": {k: {"quantity": v} for k, v in holdings.items()}}
        entries_by_sym = {e.get("symbol"): e for e in decision.get("entries", [])}

        out = {"date": today, "generated_utc": now.isoformat(), "candidates": cands,
               "verdicts": [], "note": "veto-only: LLM may remove candidates, never add"}
        for sym in cands:
            res = {"symbol": sym}
            t = threading.Thread(target=debate_symbol,
                                 args=(sym, portfolio, entries_by_sym.get(sym, {}), res),
                                 daemon=True)
            t.start()
            t.join(SYMBOL_TIMEOUT_S)
            if t.is_alive():
                res["verdict"] = "abstain"
                res["error"] = "timeout after %ds" % SYMBOL_TIMEOUT_S
            out["verdicts"].append(res)
            logger.info("%s %s (rating=%s) %s", sym, res.get("verdict"),
                        res.get("rating"), (res.get("error") or "")[:120])
        if not cands:
            logger.info("no candidates today — nothing to debate")

        db.record_decision(today, "debate", out, status="ok")
        db.mark_step(today, "debate")
        db.add_event("debate", {"date": today,
                                "verdicts": {v["symbol"]: v["verdict"] for v in out["verdicts"]}})
        if flag("SHADOW", "1"):
            gitstate.journal(today, "debate", out)
        return out
    finally:
        db.close()
