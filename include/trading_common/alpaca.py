"""Alpaca REST client + reconcile/caps/halt logic — verbatim port of
/workspace/ai4trade/alpaca_bridge.py and alpaca/halt_resume_check.py.

- api(): same request/error handling ({"_error": ...} on HTTP errors).
- diff computation: sells first, target-vs-held per symbol.
- Brackets: paper-mode logic (entry-based -5% stop / +15% take-profit).
- Caps (PILOT_NOTIONAL env, verbatim live-mode math):
    per-order  = 30%  of pilot notional
    book cap   = 104% of pilot notional
    kill-line  = 85%  of pilot notional (equity below -> halt)
- Halt: market-dictated resume — gates_open() ported from
  halt_resume_check.py (VIX <= 70th pct of trailing 252d AND HYG/LQD ratio
  > 60d mean - 2sd). Halt state itself lives in the trading DB events table.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .secrets import secret

UA_Y = {"User-Agent": "Mozilla/5.0"}

MAX_NOTIONAL_PER_ORDER_PAPER = 30000.0  # paper-mode guard (unchanged)


def pilot_caps() -> dict:
    pilot = float(secret("PILOT_NOTIONAL", "135000"))
    return {
        "pilot_notional": pilot,
        "max_notional_per_order": pilot * 0.30,
        "max_book_notional": pilot * 1.04,
        "equity_kill_line": pilot * 0.85,
    }


def _creds() -> tuple[str, str, str]:
    key = secret("ALPACA_PAPER_API_KEY")
    sec = secret("ALPACA_PAPER_API_SECRET")
    base = secret("ALPACA_PAPER_API_ENDPOINT", "https://paper-api.alpaca.markets").rstrip("/")
    return key, sec, base


class Alpaca:
    def __init__(self, key: str | None = None, sec: str | None = None,
                 base: str | None = None):
        k, s, b = _creds()
        self.key = key or k
        self.sec = sec or s
        self.base = (base or b)
        if not self.base.endswith("/v2"):
            self.base += "/v2"
        self.hdrs = {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.sec,
                     "Content-Type": "application/json"}

    def api(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers=self.hdrs)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return {"_error": "HTTP %s %s" % (e.code, e.read().decode()[:200])}
        except Exception as e:  # noqa: BLE001
            return {"_error": str(e)[:200]}


def account_state(api: Alpaca) -> dict:
    acct = api.api("GET", "/account")
    if "_error" in acct:
        return {"ok": False, "error": acct["_error"]}
    clock = api.api("GET", "/clock")
    if "_error" in clock:
        return {"ok": False, "error": "clock: %s" % clock["_error"]}
    positions = api.api("GET", "/positions")
    if not isinstance(positions, list):
        # api() returns {"_error": ...} on HTTP/transport failures; a failed
        # positions read must fail the run (an empty-held reconcile plan is
        # indistinguishable from "sell/buy everything" drift)
        return {"ok": False, "error": "positions: %s" % positions.get("_error", positions)}
    held = {p["symbol"]: float(p["qty"]) for p in positions}
    held_value = sum(float(p["market_value"]) for p in positions)
    return {"ok": True, "open": bool(clock.get("is_open")),
            "equity": float(acct.get("equity") or 0.0),
            "cash": float(acct.get("cash") or 0.0),
            "held": held, "held_value": round(held_value, 2)}


def diffs(target: dict[str, float], held: dict[str, float],
          min_abs: float = 1.0) -> list[dict]:
    """Sells-first diff list between target and held books."""
    out = []
    for sym in sorted(set(target) | set(held)):
        want, have = target.get(sym, 0.0), held.get(sym, 0.0)
        d = round(want - have, 4)
        if abs(d) >= min_abs:
            out.append({"symbol": sym, "action": "buy" if d > 0 else "sell",
                        "qty": abs(d)})
    return sorted(out, key=lambda x: x["action"] != "sell")


def bracket_body(body: dict, sym: str, ref_price: float | None) -> dict:
    """Paper-mode bracket attachment: -5% stop / +15% TP off the reference
    (entry/last) price; bracket dropped when no price is known."""
    if body.get("side") != "buy" or not ref_price:
        return body
    body.update({"order_class": "bracket",
                 "stop_loss": {"stop_price": round(ref_price * 0.95, 2)},
                 "take_profit": {"limit_price": round(ref_price * 1.15, 2)}})
    return body


def evaluate_caps(diffs_list: list[dict], prices: dict[str, float],
                  held_value: float, caps: dict) -> tuple[list[dict], list[dict]]:
    """Live-mode guard math (verbatim): per-order cap, projected-book cap and
    run cumulative-buy cap. Sells first (the bridge sorts the diff list the
    same way before its order loop). Returns (orders, rejections)."""
    orders, rejections = [], []
    buy_ledger = 0.0
    projected_book = held_value
    for d in sorted(diffs_list, key=lambda x: x["action"] != "sell"):
        sym, qty = d["symbol"], int(d["qty"])
        px = prices.get(sym) or 0.0
        if d["action"] == "buy" and (not px or px <= 0):
            rejections.append({"symbol": sym, "side": "buy", "qty": qty,
                               "reason": "no price available - cap bypass guard"})
            continue
        notional = qty * px
        if d["action"] == "buy":
            if notional > caps["max_notional_per_order"]:
                rejections.append({"symbol": sym, "side": "buy", "qty": qty,
                                   "notional": round(notional, 2),
                                   "reason": "per-order cap $%.0f" % caps["max_notional_per_order"]})
                continue
            if projected_book + notional > caps["max_book_notional"]:
                rejections.append({"symbol": sym, "side": "buy", "qty": qty,
                                   "notional": round(notional, 2),
                                   "projected_book": round(projected_book + notional, 2),
                                   "reason": "total notional cap $%.0f" % caps["max_book_notional"]})
                continue
            if buy_ledger + notional > caps["max_book_notional"]:
                rejections.append({"symbol": sym, "side": "buy", "qty": qty,
                                   "notional": round(notional, 2),
                                   "reason": "run cumulative buy cap $%.0f" % caps["max_book_notional"]})
                continue
            buy_ledger += notional
            projected_book += notional
        else:
            projected_book = max(0.0, projected_book - notional)
        orders.append({"symbol": sym, "side": d["action"], "qty": qty,
                       "notional": round(notional, 2),
                       "assumed_price": round(px, 4)})
    return orders, rejections


# ---- halt gates: verbatim port of alpaca/halt_resume_check.py -------------

def _series(sym: str, days: int = 400) -> list[float]:
    r = urllib.request.Request(
        "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=%dd"
        % (sym, days), headers=UA_Y)
    d = json.loads(urllib.request.urlopen(r, timeout=20).read())["chart"]["result"][0]
    return [c for c in d["indicators"]["quote"][0]["close"] if c]


def gates_open() -> bool | None:
    """Panic gates: VIX <= 70th pct of trailing 252d AND HYG/LQD credit
    stress cleared (ratio > 60d mean - 2sd). None on data failure (fail
    closed: treat as closed)."""
    try:
        vix = _series("^VIX")
        pct70 = (sorted(vix[:252])[-76] if len(vix) >= 252
                 else sorted(vix)[int(len(vix) * .7)])
        v_ok = vix[-1] <= pct70
        hyg, lqd = _series("HYG"), _series("LQD")
        n = min(len(hyg), len(lqd), 60)
        ratios = [h / l for h, l in zip(hyg[-n:], lqd[-n:])]
        m = sum(ratios) / len(ratios)
        sd = (sum((x - m) ** 2 for x in ratios) / len(ratios)) ** .5
        c_ok = ratios[-1] > m - 2 * sd
        return v_ok and c_ok
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
