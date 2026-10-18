#!/usr/bin/env python3
"""CONTROL variant — verbatim port of ai-trading/control.py.

The untouched clone of the owner's real (investable) portfolio:
buy-and-hold, no rules, no exits. Weights come from personal_sleeve.json
(fetched from the ai-trading repo by the caller during shadow). Anchored at
100,000 on first run using the latest completed close; marked to market
nightly. Idempotent per UTC day; skips weekends.

I/O boundary changes only: sleeve weights are a parameter, and the state
(shares/anchor) is loaded/saved by the caller instead of control_state.json.
Anchoring math, cash handling, weekend/idempotency and the single-fetch
mark-to-market flow are unchanged.
"""
import json
import urllib.request
from datetime import datetime, timezone

START_CASH = 100000.0
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def fetch_closes(syms):
    out = {}
    for s in syms:
        try:
            r = urllib.request.Request(
                "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=5d" % s,
                headers=UA)
            with urllib.request.urlopen(r, timeout=15) as resp:
                d = json.loads(resp.read().decode())
            res = d["chart"]["result"][0]
            out[s] = {datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"): c
                      for t, c in zip(res.get("timestamp") or [],
                                      (res.get("indicators") or {}).get("quote")[0].get("close") or [])
                      if c}
        except Exception:
            out[s] = {}
    return out


def sleeve_weights(sleeve: dict) -> tuple[dict, float]:
    """weights -> (normalized non-cash weights, cash fraction) — original
    cash_keys/pop/normalize sequence."""
    weights = {k: v / 100.0 for k, v in sleeve["weights_pct"].items()}
    cash_keys = [k for k in weights if k in ("CASH", "SPAXX**", "CORE**", "FDRXX**")]
    cash_frac = sum(weights.pop(k) for k in cash_keys)  # held as cash, like the real account
    inv = sum(weights.values())
    weights = {k: v / inv for k, v in weights.items()}
    return weights, cash_frac


def run(now: datetime | None = None, sleeve: dict | None = None, st: dict | None = None):
    """Port of control.py main(). Returns (result_json, new_state).

    sleeve: personal_sleeve.json content (required when st has no shares).
    st: prior state {shares, anchor_date, anchor_prices, cash_pct, history}.
    """
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if now.weekday() > 4:
        return {"ok": True, "skipped": "weekend"}, (st or {"shares": {}, "anchor_date": None, "history": []})
    st = st or {"shares": {}, "anchor_date": None, "history": []}
    if st["history"] and st["history"][-1]["date"] == today:
        last = st["history"][-1]
        return ({"ok": True, "date": today, "already_recorded": True,
                 "equity": last.get("equity"),
                 "return_pct": round((last.get("equity", START_CASH) / START_CASH - 1) * 100, 2),
                 "cash_pct": st.get("cash_pct")}, st)

    if not st["shares"] and not sleeve:
        return {"ok": False, "error": "sleeve weights required to anchor"}, st

    if st["shares"]:
        # subsequent runs: mark to market on the latest common close
        syms = sorted(st["shares"])
        cl = fetch_closes(syms)
        common = set.intersection(*[set(v.keys()) for v in cl.values() if v] or [set()])
        if not common:
            return {"ok": False, "error": "no common close dates"}, st
        val_date = max(common)
        cash_frac = st.get("cash_pct") or 0.0
    else:
        weights, cash_frac = sleeve_weights(sleeve)
        st["cash_pct"] = cash_frac
        syms = sorted(weights)
        cl = fetch_closes(syms)
        common = set.intersection(*[set(v.keys()) for v in cl.values() if v] or [set()])
        if not common:
            return {"ok": False, "error": "no common close dates"}, st
        val_date = max(common)
        prices = {s: cl[s].get(val_date) for s in syms}
        priced = {s: p for s, p in prices.items() if p}
        # exact-largest-remainder: share counts so day-0 value == START_CASH
        raw = {s: (START_CASH * (1 - cash_frac)) * weights[s] / p for s, p in priced.items()}
        base = {s: int(v) for s, v in raw.items()}
        residual = round(START_CASH * (1 - cash_frac) - sum(q * priced[s] for s, q in base.items()))
        # spend the residual on the cheapest symbol to minimize error
        cheap = min(priced, key=priced.get)
        if residual > 0:
            base[cheap] += residual // priced[cheap]
        st["shares"] = {s: q for s, q in base.items() if q > 0}
        st["anchor_prices"] = priced
        st["anchor_date"] = val_date
        st["anchor_note"] = "largest-remainder anchoring; day-0 value == 100000"

    equity = cash_frac * START_CASH + sum(q * cl[s].get(val_date, 0) for s, q in st["shares"].items())
    st["history"] = st.get("history", [])
    st["history"].append({"date": today, "val_date": val_date, "equity": round(equity, 2)})
    return ({"ok": True, "date": today, "val_date": val_date,
             "anchor_date": st["anchor_date"],
             "cash_pct": round(cash_frac * 100, 2),
             "positions": {s: q for s, q in sorted(st["shares"].items()) if q},
             "equity": round(equity, 2),
             "return_pct": round((equity / START_CASH - 1) * 100, 2)}, st)
