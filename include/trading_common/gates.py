"""Execution gates — verbatim port of ai-trading/daily_run.py gate logic.

RISK_PAT, the DD veto gate (veto-only, missing/stale = no vetoes), the
headline screen, the market-hours guard and the max-trades/day cap are
unchanged. Only the I/O boundary differs: vetoes come from the decisions
table (debate step) instead of state/dd_verdicts.json, and the trade counter
comes from the fills table instead of the book's trades_YYYYMMDD key.
"""
from __future__ import annotations

import re

MAX_TRADES_PER_DAY = 2

RISK_PAT = re.compile(
    r"(lawsuit|indict\w*|fraud|probe|investigat\w*|\bsec\b|\bdoj\b|sanction\w*|"
    r"recall\w*|bankrupt\w*|resign\w*|downgrade\w*|halted|guidance (?:cut|lower\w*)|"
    r"miss(?:es|ed)? (?:earnings|estimates|revenue)|layoff\w*|delist\w*|ceo exit)", re.I)


def dd_vetoes(debate: dict | None, today: str) -> dict:
    """Veto-only DD gate: {symbol: verdict-row} for TODAY's debate only.

    Missing, failed or stale debate => no vetoes (an abstain never blocks) —
    identical to daily_run.dd_vetoes semantics for a missing/stale file.
    """
    if not debate or debate.get("date") != today:
        return {}
    return {r.get("symbol"): r for r in debate.get("verdicts", [])
            if r.get("verdict") == "veto"}


def headline_risk(headlines: list[str]) -> str | None:
    if not headlines or any(not h.strip() or h.startswith("_news_fetch_error") for h in headlines):
        return "headline screen unavailable"
    for h in headlines:
        if RISK_PAT.search(h):
            return "risk headline: %s" % h[:120]
    return None


def apply_gates(decision: dict, debate: dict | None, trades_today: int):
    """Port of daily_run.main()'s loop: exits first, then gated entries.

    Returns (exits_ok, gated_entries, gate_log) where gate_log carries the
    DAILY/TRADED/SKIP_BUY/DD_VETO line equivalents for the journal.
    """
    today = decision.get("today")
    vetoes = dd_vetoes(debate, today)
    gate_log: list[dict] = []
    trades_used = trades_today

    exits_ok = []
    for e in decision.get("exits", []):
        if trades_used >= MAX_TRADES_PER_DAY:
            gate_log.append({"tag": "SKIP_SELL",
                             "line": "%s max %d trades/day reached"
                                     % (e["symbol"], MAX_TRADES_PER_DAY)})
            break
        exits_ok.append(e)
        gate_log.append({"tag": "TRADED", "line": "sell %s x%s %s"
                         % (e["symbol"], e["quantity"], e["reason"])})
        trades_used += 1

    gated_entries = []
    for ent in decision.get("entries", []):
        sym = ent.get("symbol")
        if trades_used >= MAX_TRADES_PER_DAY:
            gate_log.append({"tag": "SKIP_BUY",
                             "line": "%s max %d trades/day reached" % (sym, MAX_TRADES_PER_DAY)})
            break
        if not decision.get("market_open"):
            gate_log.append({"tag": "SKIP_BUY",
                             "line": "%s market closed (execute only 13:30-20:00 UTC weekdays)" % sym})
            continue
        if sym in vetoes:
            gate_log.append({"tag": "DD_VETO", "line": "%s %s" % (
                sym, (vetoes[sym].get("thesis_excerpt") or "")[:120])})
            continue
        risk = headline_risk(ent.get("headlines") or [])
        if risk:
            gate_log.append({"tag": "SKIP_BUY", "line": "%s %s" % (sym, risk)})
            continue
        gated_entries.append(ent)
        gate_log.append({"tag": "TRADED", "line": "buy %s x%s %s"
                         % (sym, ent.get("quantity", 0), ent.get("reason", "momentum entry"))})
        trades_used += 1

    return exits_ok, gated_entries, gate_log
