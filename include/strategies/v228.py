"""v228 — vol-scaled mean reversion (lab2 V228, verbatim port of
trading_common/forward.py meanrev_vol_rows into live decision shape).

Rules (unchanged from the lab + forward replay):
  - universe: the EW-12 research names
  - exit: close vs entry fill: +10% TP / -5% stop  (bracket-native)
  - entry: worst-first among names whose n=1-day return (through t-1)
    <= -kvol(2.0) * sigma20 * sqrt(n), up to sel=3 names at 1/3 weight each
  - entries sized 1/3 of equity each; live caps still apply downstream
    (per-order 30k cap will trim the third slot — known live-parity
    divergence, see the lab live-parity harness)

Differences from the pilot chain, by design:
  - no debate/LLM veto step (the +16.2% forward record never had one —
    adding it would break the A/B)
  - fetches its own 40d closes per universe name (sigma20 needs 21+)
"""
from __future__ import annotations

import math

from trading_common import marketdata
from trading_common.trade_rules import market_open

NAME = "v228"

UNIVERSE = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META",
            "GOOGL", "AMD", "JPM", "XOM", "LLY", "AVGO"]

N = 1          # entry lookback (days)
KVOL = 2.0     # sigma multiplier for the dip threshold
SEL = 3        # max concurrent positions (1/3 weight each)
TP_PCT = 10.0  # take-profit vs entry fill
STOP_PCT = 5.0 # stop-loss vs entry fill

STEPS = [
    {"step": "decide", "path": "/decide-v228", "timeout": 480, "optional": False},
    {"step": "execute", "path": "/execute-v228", "timeout": 300, "optional": False},
]

EXPECTED_STEPS = ("decide", "execute")

BRACKET = {"tp_pct": TP_PCT, "stop_pct": STOP_PCT}


def _closes(sym: str, need: int = 25) -> list[float]:
    """Daily closes, oldest-first, at least `need` of them."""
    d = marketdata.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=3mo" % sym)
    res = d["chart"]["result"][0]
    closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c is not None]
    if len(closes) < need:
        raise ValueError("%s: only %d closes" % (sym, len(closes)))
    return closes


def fetch() -> dict:
    """Market context: per-name last price, 1d return through the last
    completed close, and sigma20 (sample stdev of the last 20 daily
    returns, ddof=1 — pandas rolling(20).std() semantics, mirroring
    forward.py _sd20)."""
    ctx = {}
    for sym in UNIVERSE:
        try:
            px = _closes(sym)
        except Exception as e:  # noqa: BLE001 - per-symbol degradation
            ctx[sym] = {"error": str(e)[:120]}
            continue
        price = px[-1]
        ret_1d = px[-1] / px[-2] - 1.0
        rs = [px[k] / px[k - 1] - 1.0 for k in range(len(px) - 20, len(px))]
        mu = sum(rs) / len(rs)
        sigma20 = (sum((r - mu) ** 2 for r in rs) / (len(rs) - 1)) ** 0.5
        ctx[sym] = {"price": price, "ret_1d": ret_1d, "sigma20": sigma20}
    return {"v228": True, "stocks": ctx}


def build_decision(book: dict, ctx: dict, now=None) -> dict:
    import datetime as dt

    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    stocks = ctx.get("stocks", {})

    cash = float(book.get("cash") or 0)
    held = {}
    exits = []
    for sym, p in (book.get("positions") or {}).items():
        qty = int(p.get("qty") or 0)
        if not qty:
            continue
        entry = float(p.get("entry") or 0.0)
        cur = (stocks.get(sym) or {}).get("price") or entry
        pnl_pct = (cur / entry - 1.0) * 100.0 if entry else 0.0
        held[sym] = {"qty": qty, "entry": entry, "price": cur, "pnl_pct": pnl_pct}
        if entry and cur >= entry * (1.0 + TP_PCT / 100.0):
            exits.append({"action": "sell", "symbol": sym, "quantity": qty,
                          "reason": "v228_tp_exit: %.2f%% >= +%.0f%%" % (pnl_pct, TP_PCT),
                          "pnl_pct": round(pnl_pct, 2)})
        elif entry and cur <= entry * (1.0 - STOP_PCT / 100.0):
            exits.append({"action": "sell", "symbol": sym, "quantity": qty,
                          "reason": "v228_stop_exit: %.2f%% <= -%.0f%%" % (pnl_pct, STOP_PCT),
                          "pnl_pct": round(pnl_pct, 2)})

    pos_value = sum(h["qty"] * h["price"] for h in held.values())
    equity = cash + pos_value
    slots = SEL - len(held) + len(exits)

    cands = []
    for sym in UNIVERSE:
        if sym in held:
            continue
        s = stocks.get(sym) or {}
        if "error" in s or not s:
            continue
        thr = -KVOL * s["sigma20"] * math.sqrt(N)
        if s["ret_1d"] <= thr:
            cands.append((s["ret_1d"], sym, s["price"], thr))
    cands.sort()  # worst-first

    entries = []
    slot_size = equity / float(SEL)
    for ret_1d, sym, price, thr in cands:
        if len(entries) >= max(0, slots):
            break
        if not price:
            continue
        qty = int(slot_size // price)
        if qty < 1:
            continue
        entries.append({"action": "buy", "symbol": sym, "quantity": qty,
                        "est_cost": round(qty * price, 2),
                        "reason": "v228_entry: 1d %.2f%% <= -%.0f*x sigma20 (%.2f%%)"
                                  % (ret_1d * 100.0, KVOL, thr * 100.0)})

    return {
        "ok": True,
        "now_utc": now.isoformat(),
        "today": today,
        "market_open": market_open(now),
        "strategy": NAME,
        "account": {"cash": round(cash, 2), "equity": round(equity, 2),
                    "positions_held": len(held)},
        "exits": exits,
        "entries": entries,
    }
