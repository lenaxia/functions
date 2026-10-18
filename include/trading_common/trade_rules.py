"""Decision engine — verbatim port of ai-trading/trade.py.

The subprocess/stdubound I/O of the original is replaced by pure functions:
build_decision(book, snap, extra, now) returns the same JSON shape trade.py
printed. Policy constants, entry/exit rules, sizing and ordering are
unchanged. Headline fetching (Google News RSS) is identical.
"""
from __future__ import annotations

import html
import re
import urllib.request
from datetime import datetime, timezone

BENCH = {"SPY", "QQQ"}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

POLICY = {
    "style": "conservative momentum, growth tilt",
    "entry": "1d RS vs SPY >= +3%, 1d change > 0, within 5% of 52w high",
    "position_size_pct_of_equity": 10,
    "max_positions": 15,
    "stop_loss_pct": -5.0,
    "take_profit_pct": 15.0,
    "max_trades_per_day": 2,
}


def headlines(query: str, limit: int = 5) -> list[str]:
    url = ("https://news.google.com/rss/search?q=" + urllib.request.quote(query)
           + "&hl=en-US&gl=US&ceid=US:en")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read().decode("utf-8", "ignore")
        titles = re.findall(r"<title>(.*?)</title>", body, re.S)[1:]  # [0] is channel title
        return [html.unescape(t)[:200] for t in titles[:limit]]
    except Exception as e:  # noqa: BLE001
        return ["_news_fetch_error: " + str(e)[:100]]


def market_open(now: datetime) -> bool:
    if now.weekday() > 4:
        return False
    hm = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= hm < 20 * 60


def build_decision(book: dict, snap: dict,
                   extra: list[str] | None = None,
                   now: datetime | None = None) -> dict:
    """Port of trade.py main(). `book` is the state/book.json shape
    ({cash, positions: {sym: {qty, entry}}}); `snap` is a marketdata.snapshot()
    result."""
    extra = [a.upper() for a in (extra or []) if a.isalpha()]
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    stocks = {s["sym"]: s for s in snap.get("stocks", []) if "error" not in s}
    spy = stocks.get("SPY", {}).get("chg_1d_pct") or 0.0

    cash = float(book.get("cash") or 0)
    held = []
    for sym, p in (book.get("positions") or {}).items():
        qty = p.get("qty") or 0
        entry = p.get("entry") or 0.0
        cur = (stocks.get(sym) or {}).get("price") or entry
        if qty:
            held.append({"symbol": sym, "quantity": qty, "entry_price": entry,
                         "current_price": cur,
                         "pnl_pct": round((cur / entry - 1) * 100, 2) if entry else None})
    held_syms = {h["symbol"] for h in held}
    pos_value = sum(h["quantity"] * h["current_price"] for h in held)
    equity = cash + pos_value

    exits = []
    for h in held:
        pct = h["pnl_pct"]
        if pct is None:
            continue
        if pct <= POLICY["stop_loss_pct"]:
            exits.append({"action": "sell", "symbol": h["symbol"], "quantity": h["quantity"],
                          "reason": "stop loss: %.2f%% <= -5%%" % pct, "pnl_pct": pct})
        elif pct >= POLICY["take_profit_pct"]:
            exits.append({"action": "sell", "symbol": h["symbol"], "quantity": h["quantity"],
                          "reason": "take profit: %.2f%% >= +15%%" % pct, "pnl_pct": pct})

    cands = []
    for sym, s in stocks.items():
        if sym in BENCH or sym in held_syms:
            continue
        rs = (s.get("chg_1d_pct") or 0) - spy
        hi, lo, px = s.get("week_52_high"), s.get("week_52_low"), s.get("price")
        dist_hi = (hi / px - 1) * 100 if (hi and px) else None
        if rs >= 3.0 and (s.get("chg_1d_pct") or 0) > 0 and dist_hi is not None and dist_hi <= 5.0:
            cands.append({"symbol": sym, "price": px, "chg_1d_pct": s["chg_1d_pct"],
                          "rs_vs_spy_pts": round(rs, 2), "dist_to_52w_high_pct": round(dist_hi, 2)})
    cands.sort(key=lambda c: -c["rs_vs_spy_pts"])
    cands = cands[:3]

    entries = []
    slots = POLICY["max_positions"] - len(held) + len(exits)
    budget_share = equity * POLICY["position_size_pct_of_equity"] / 100.0
    for c in cands:
        if len(entries) >= max(0, slots):
            break
        qty = int(budget_share // c["price"]) if c["price"] else 0
        if qty < 1:
            continue
        entries.append({"action": "buy", "symbol": c["symbol"], "quantity": qty,
                        "est_cost": round(qty * c["price"], 2),
                        "reason": "momentum: RS +%.2f pts vs SPY, %.2f%% below 52w high"
                                  % (c["rs_vs_spy_pts"], c["dist_to_52w_high_pct"]),
                        "headlines": headlines("%s stock" % c["symbol"])})

    return {
        "ok": True,
        "now_utc": now.isoformat(),
        "today": today,
        "market_open": market_open(now),
        "policy": POLICY,
        "account": {"cash": round(cash, 2), "positions_value": round(pos_value, 2),
                    "equity": round(equity, 2), "positions_held": len(held)},
        "sleeve_excluded": sorted(held_syms),
        "held": held,
        "exits": exits,
        "entries": entries,
        "market_headlines": headlines("stock market today politics Fed"),
        "note": "Exits before entries. Max 2 trades/day total. Buys require a headline screen "
                "(skip on material political/regulatory/earnings risk) executed by the decide "
                "step, which also applies the DD veto gate before execute reconciles the broker.",
    }
