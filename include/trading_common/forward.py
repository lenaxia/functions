#!/usr/bin/env python3
"""OUT-OF-SAMPLE FORWARD TRACKER (local simulation; virtual money only).

Tracks 6 variants live-out-of-sample from FORWARD_START (2026-09-29), each
starting with 100,000 virtual. Both backtest labs (137 + 307 variants) found
nothing passing multiple-testing correction on 3y history, so promotion
decisions require forward evidence. This machine collects it.

Daily job, in order:
  (a) refresh data: seed from lab/data once, then refetch from Yahoo with the
      same UA/retry behavior as lab/data/fetch.py, merged append-mostly into
      forward/data/ (history before the trailing revision tail is frozen so
      the warm-up replay is stable run-to-run);
  (b) replay every variant over the FULL available history under the
      no-lookahead convention (signals from closes <= t-1, execution at the
      close of day t), using pre-forward history only as warm-up state;
  (c) RECORD equity / positions / trades for dates >= FORWARD_START only,
      and overwrite forward_state.json wholesale (idempotent per UTC day:
      same data in -> same state out; re-running never double-appends);
  (d) print one comparison JSON to stdout: per-variant return-to-date,
      current positions, trade count, exposure, plus a SPY buy-and-hold
      benchmark series over the same window.

Conventions (see NOTES.md for the full statement):
  - v1 variants (donchian / xs-momentum / ew12 baseline) replay under the
    v1 engine semantics copied from lab/backtest.py: daily mult =
    (1 + r_day) * (1 - cost), cost = 5bps * |target - drifted| one-way
    turnover charged on the trade day; weights drift between targets.
  - v2 variants (V228 meanrev_vol, V306 killswitch) replay under the
    lab2/engine.py semantics: mult = (1 + r - cost) with r = W2[t-1] . ret[t]
    and cost = 5bps * |target_t - W2[t-1]| (the accounting used inside
    lab2 apply_killswitch). V306 segments are [history .. FORWARD_START-1]
    and [FORWARD_START ..] with killswitch equity/peak reset at the forward
    boundary, mirroring lab2's per-split reset (state carries across the
    boundary only through the effective weight row).
  - Costs 5 bps per side, long-only, no leverage, close marks only.
  - Deterministic: state is a pure function of the CSVs in forward/data/.

Universe (v1/lab conventions): 12 tradeable names; SPY benchmark, QQQ held
out. Stocks only; no trading-platform API is ever called - the only network
use is the public Yahoo daily-OHLC endpoint for research data refresh.
"""

import csv
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# Fission pods are ephemeral: cache CSVs in /tmp (refetched 3y each cold
# start; ~15s for 14 symbols). TRADING_FORWARD_DATA_DIR overrides.
DATA_DIR = os.environ.get("TRADING_FORWARD_DATA_DIR", "/tmp/trading-forward-data")
STATE_PATH = os.path.join(DATA_DIR, "forward_state.json")
SRC_DATA = os.environ.get("TRADING_FORWARD_SRC_DATA", "")  # optional seed source

UNIVERSE = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META",
            "GOOGL", "AMD", "JPM", "XOM", "LLY", "AVGO"]
BENCH = "SPY"
ALL_SYMS = sorted(UNIVERSE + [BENCH, "QQQ"])

FORWARD_START = "2026-09-29"
START_EQUITY = 100000.0
COST = 0.0005                 # 5 bps per side
EPS = 1e-12
SLEEVE = 1.0 / len(UNIVERSE)  # v1 fixed 1/12 slot
DONCHIAN_WINDOW = 63

# v2 killswitch overlay parameters (lab2 V306)
KS_TRIGGER = -0.10
KS_SESSIONS = 20

# fetch behavior (mirrors lab/data/fetch.py)
BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
RANGE = "3y"
INTERVAL = "1d"
ATTEMPTS = 2
RETRY_SLEEP = 2.0
SYMBOL_PAUSE = 0.5
REVISION_TAIL = 5            # most recent N existing bars a refetch may revise

VARIANT_SPECS = [
    {"id": "don_p10_s8", "family": "donchian", "source": "v1 lab",
     "description": "Donchian: buy within 10% of 63d high, -8% stop (v1 conventions)",
     "params": {"p": 10, "stop": 8, "window": DONCHIAN_WINDOW}},
    {"id": "mom_n5_k5_abs_c21", "family": "xs-momentum", "source": "v1 lab",
     "description": ("XS momentum: top-5 by 5d return, abs filter "
                     "(cash when n-day return <= 0), rebalance every 21d"),
     "params": {"n": 5, "k": 5, "abs_filter": True, "cadence": 21}},
    {"id": "mom_n21_k5_abs_c1", "family": "xs-momentum", "source": "v1 lab",
     "description": ("XS momentum: top-5 by 21d return, abs filter "
                     "(cash when n-day return <= 0), rebalance every 1d"),
     "params": {"n": 21, "k": 5, "abs_filter": True, "cadence": 1}},
    {"id": "base_ew12", "family": "baseline", "source": "v1 lab",
     "description": "Equal-weight buy-and-hold of the 12 non-benchmark names",
     "params": {"assets": "EW-12"}},
    {"id": "V228_meanrev_vol_n1_k2_y10_sel3", "family": "meanrev_vol",
     "source": "v2 lab2 (V228)",
     "description": ("Vol-scaled mean reversion: buy worst 1d performers when "
                     "1d return <= -2.0 * sigma20 (per-name 20d stdev of daily "
                     "returns), up to 3 names at 1/3 each; exit +10% TP / -5% "
                     "stop vs entry fill close"),
     "params": {"n": 1, "kvol": 2.0, "y": 10.0, "sel": 3, "z_exit": 5.0}},
    {"id": "V306_killswitch_V228", "family": "killswitch_overlay",
     "source": "v2 lab2 (V306)",
     "description": ("V228 with killswitch: portfolio equity <= -10% from its "
                     "running peak (forward-window peak) -> flatten to cash for "
                     "20 sessions, then resume base weights and reset the peak"),
     "params": {"base": "V228", "trigger_pct": -10.0, "cash_sessions": 20}},
]


def v2_engine_params(specs: list[dict]) -> dict:
    """Engine params for the v2 mean-reversion families, selected by variant
    identity — never by list position (V306's spec params are the overlay's
    own base/trigger/cash config, not the engine's n/kvol/y/sel/z_exit)."""
    return next(s["params"] for s in specs
                if s["family"] == "meanrev_vol" and s["id"].startswith("V228"))


# --------------------------------------------------------------------------
# Data layer
# --------------------------------------------------------------------------

def seed_data():
    """One-time copy of seed CSVs when a seed source is configured. Skipped
    when TRADING_FORWARD_SRC_DATA is unset (Fission: refetch 3y directly)."""
    if not SRC_DATA or not os.path.isdir(SRC_DATA):
        return False
    if os.path.isdir(DATA_DIR) and any(f.endswith(".csv") for f in os.listdir(DATA_DIR)):
        return False
    os.makedirs(DATA_DIR, exist_ok=True)
    for sym in ALL_SYMS:
        src = os.path.join(SRC_DATA, sym + ".csv")
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DATA_DIR, sym + ".csv"))
    return True


def fetch_symbol(sym):
    """List of row dicts from Yahoo's public chart endpoint, or raise."""
    url = BASE_URL.format(sym=sym) + "?interval={}&range={}".format(INTERVAL, RANGE)
    last_err = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            result = payload["chart"]["result"][0]
            stamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]
            rows = []
            for i, ts in enumerate(stamps):
                close = quote["close"][i]
                if close is None:
                    continue
                o = quote["open"][i]
                h = quote["high"][i]
                l = quote["low"][i]
                v = quote["volume"][i]
                rows.append({
                    "date": datetime.fromtimestamp(
                        ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": o if o is not None else close,
                    "high": h if h is not None else close,
                    "low": l if l is not None else close,
                    "close": close,
                    "volume": int(v) if v is not None else 0,
                })
            rows.sort(key=lambda r: r["date"])
            return rows
        except Exception as exc:  # noqa: BLE001 - retry, then give up per symbol
            last_err = exc
            sys.stderr.write("[{}] fetch attempt {}/{} failed: {}\n".format(
                sym, attempt, ATTEMPTS, exc))
            if attempt < ATTEMPTS:
                time.sleep(RETRY_SLEEP)
    raise RuntimeError("fetch failed: {}".format(last_err))


def _read_csv(path):
    rows = []
    with open(path, newline="") as fh:
        for rec in csv.DictReader(fh):
            rows.append({"date": rec["date"],
                         "open": float(rec["open"]),
                         "high": float(rec["high"]),
                         "low": float(rec["low"]),
                         "close": float(rec["close"]),
                         "volume": int(rec["volume"])})
    return rows


def _write_csv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([r["date"],
                        "{:.6f}".format(r["open"]),
                        "{:.6f}".format(r["high"]),
                        "{:.6f}".format(r["low"]),
                        "{:.6f}".format(r["close"]),
                        r["volume"]])
    os.replace(tmp, path)


def merge_symbol(sym, fetched):
    """Append-mostly merge: new dates append; only the trailing REVISION_TAIL
    existing bars may be revised by the fetch (handles late fixes and
    same-session partial bars); deeper history is frozen so warm-up replay
    stays stable run-to-run."""
    path = os.path.join(DATA_DIR, sym + ".csv")
    existing = _read_csv(path) if os.path.isfile(path) else []
    if not existing:
        _write_csv(path, fetched)
        return {"appended": len(fetched), "revised": 0}
    have = {r["date"]: r for r in existing}
    dates = [r["date"] for r in existing]
    tail = set(dates[-REVISION_TAIL:])
    appended = revised = 0
    for r in fetched:
        d = r["date"]
        if d not in have:
            have[d] = r
            appended += 1
        elif d in tail:
            have[d] = r
            revised += 1
    merged = sorted(have.values(), key=lambda r: r["date"])
    _write_csv(path, merged)
    return {"appended": appended, "revised": revised}


def refresh_data():
    """Seed if needed, then refetch all symbols. Returns manifest dict."""
    seeded = seed_data()
    manifest = {"fetched_at_utc": datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"), "seeded_from_lab": seeded, "symbols": {}}
    for sym in ALL_SYMS:
        info = {"status": "ok", "rows": 0, "first_date": None, "last_date": None,
                "appended": 0, "revised": 0}
        try:
            rows = fetch_symbol(sym)
            if not rows:
                raise RuntimeError("no non-null rows parsed")
            mg = merge_symbol(sym, rows)
            info.update(mg)
            final = _read_csv(os.path.join(DATA_DIR, sym + ".csv"))
            info["rows"] = len(final)
            info["first_date"] = final[0]["date"]
            info["last_date"] = final[-1]["date"]
        except Exception as exc:  # noqa: BLE001 - keep existing file, mark stale
            info["status"] = "stale ({})".format(exc)
            if os.path.isfile(os.path.join(DATA_DIR, sym + ".csv")):
                final = _read_csv(os.path.join(DATA_DIR, sym + ".csv"))
                info["rows"] = len(final)
                info["first_date"] = final[0]["date"]
                info["last_date"] = final[-1]["date"]
            else:
                info["status"] = "MISSING ({})".format(exc)
        manifest["symbols"][sym] = info
        time.sleep(SYMBOL_PAUSE)
    missing = [s for s, i in manifest["symbols"].items()
               if i["status"].startswith("MISSING")]
    if missing:
        raise RuntimeError("no usable data for: {}".format(", ".join(missing)))
    return manifest


def load_aligned():
    """Load all CSVs, align on the intersection calendar, return
    (dates, px, dropped) with px[sym] -> list of closes."""
    per = {}
    for sym in ALL_SYMS:
        rows = _read_csv(os.path.join(DATA_DIR, sym + ".csv"))
        per[sym] = {r["date"]: r["close"] for r in rows}
    common = set.intersection(*(set(m) for m in per.values()))
    dropped = {s: len(per[s]) - len(common) for s in ALL_SYMS}
    dates = sorted(common)
    if len(dates) < 300:
        raise RuntimeError("aligned history too short: {} rows".format(len(dates)))
    px = {s: [per[s][d] for d in dates] for s in ALL_SYMS}
    for s in ALL_SYMS:
        if min(px[s]) <= 0:
            raise RuntimeError("{} non-positive close".format(s))
    return dates, px, dropped


# --------------------------------------------------------------------------
# v1 strategies - semantics copied verbatim from lab/strategies.py
# --------------------------------------------------------------------------

class Fixed:
    """Buy-and-hold a fixed weight vector, trade once at the first execution
    close, then never again. Empty weights = 100% cash."""

    warmup = 0

    def __init__(self, px, weights):
        self.px = px
        self.w = dict(weights)
        self.done = not self.w

    def target(self, j):
        if self.done:
            return None
        self.done = True
        return dict(self.w)

    def on_fill(self, i, w):
        pass


class XSMomentum:
    """Rank the universe by n-day return through decision day j; hold the
    top-k equal-weighted. With absf=True, names with return <= 0 are excluded
    (their slots go to cash). Rebalances every `cad` decision days."""

    def __init__(self, px, n, k, absf, cad):
        self.px = px
        self.n = n
        self.k = k
        self.absf = absf
        self.cad = cad
        self.warmup = n
        self.hold = None

    def target(self, j):
        if (j - self.n) % self.cad != 0:
            return None
        px = self.px
        scored = []
        for s in UNIVERSE:
            p1 = px[s][j]
            p0 = px[s][j - self.n]
            scored.append((p1 / p0 - 1.0, s))
        scored.sort(key=lambda t: (-t[0], t[1]))
        if self.absf:
            scored = [t for t in scored if t[0] > 0.0]
        self.hold = {s: 1.0 / self.k for _r, s in scored[: self.k]}
        return dict(self.hold)

    def on_fill(self, i, w):
        pass


class Donchian:
    """Sleeve enters when close is within p% of the trailing 63-day closing
    high; exits on a close s% below the entry fill price. Re-entries allowed."""

    def __init__(self, px, p, stop):
        self.px = px
        self.p = p / 100.0
        self.stop = stop / 100.0
        self.warmup = DONCHIAN_WINDOW - 1
        self.state = {s: None for s in UNIVERSE}
        self.pending = set()
        self.active = None

    def target(self, j):
        px = self.px
        act = set()
        changed = False
        for s in UNIVERSE:
            price = px[s][j]
            entry = self.state[s]
            if entry is None:
                hh = max(px[s][j - DONCHIAN_WINDOW + 1: j + 1])
                if price >= hh * (1.0 - self.p):
                    self.pending.add(s)
                    act.add(s)
                    changed = True
            else:
                if price <= entry * (1.0 - self.stop):
                    self.state[s] = None
                    self.pending.discard(s)
                    changed = True
                else:
                    act.add(s)
        if not changed and self.active is not None:
            return None
        self.active = act
        return {s: SLEEVE for s in act}

    def on_fill(self, i, w):
        for s in list(self.pending):
            if w.get(s, 0.0) > 0.0:
                self.state[s] = self.px[s][i]
            self.pending.discard(s)


# --------------------------------------------------------------------------
# v1 engine - semantics copied from lab/backtest.py run_variant, extended
# with absolute-equity / trade / recording bookkeeping for the forward window.
# --------------------------------------------------------------------------

def run_v1(strat, px, dates, fi, label):
    """fi: first forward index (dates[fi] >= FORWARD_START) or None."""
    n = len(dates)
    drifted = {}
    first_exec = strat.warmup + 1
    eq_abs = None                     # set to START_EQUITY at the boundary
    series, trades = [], []
    mults = [1.0] * n                 # unrounded daily multipliers (checks)
    fwd_eq = [None] * n               # unrounded absolute equity, forward only
    for i in range(1, n):
        # 1) day-i P&L from holdings set at close i-1 (weights drifted)
        r_day = 0.0
        if drifted:
            dayr = {}
            for s, w in drifted.items():
                rs = px[s][i] / px[s][i - 1] - 1.0
                r_day += w * rs
                dayr[s] = rs
            denom = 1.0 + r_day
            if denom > 0:
                drifted = {s: w * (1.0 + dayr[s]) / denom for s, w in drifted.items()}
            else:
                drifted = {}
        # absolute-equity bookkeeping from the forward boundary on
        if fi is not None and i == fi:
            eq_abs = START_EQUITY
        eq_pre = eq_abs * (1.0 + r_day) if eq_abs is not None else None
        # 2) trade at close i towards target computed from data <= i-1
        cost = 0.0
        tgt = strat.target(i - 1) if i >= first_exec else None
        if tgt is not None:
            syms = set(tgt) | set(drifted)
            turn = 0.0
            for s in syms:
                tw, dw = tgt.get(s, 0.0), drifted.get(s, 0.0)
                turn += abs(tw - dw)
                if eq_pre is not None and abs(tw - dw) > EPS:
                    qty = abs(tw - dw) * eq_pre / px[s][i]
                    action, reason = _v1_reason(label, tw, dw)
                    trades.append({"date": dates[i], "action": action,
                                   "symbol": s, "qty": round(qty, 4),
                                   "reason": reason,
                                   "price": round(px[s][i], 4)})
            cost = turn * COST
            drifted = {s: w for s, w in tgt.items() if w > EPS}
            strat.on_fill(i, drifted)
        mult = (1.0 + r_day) * (1.0 - cost)
        mults[i] = mult
        if eq_abs is not None:
            eq_abs *= mult
            fwd_eq[i] = eq_abs
            series.append({"date": dates[i], "equity": round(eq_abs, 2),
                           "exposure": round(sum(drifted.values()), 6)})
    last_w = dict(drifted)
    return series, trades, last_w, (eq_abs if eq_abs is not None else START_EQUITY), mults, fwd_eq


def _v1_reason(label, tw, dw):
    if dw <= EPS and tw > EPS:
        action, shape = "buy", "entry"
    elif dw > EPS and tw <= EPS:
        action, shape = "sell", "exit"
    elif tw > dw:
        action, shape = "buy", "increase"
    else:
        action, shape = "sell", "decrease"
    if label == "donchian":
        reason = {"entry": "donchian_entry", "exit": "donchian_stop_exit",
                  "increase": "donchian_rebalance",
                  "decrease": "donchian_rebalance"}[shape]
    elif label == "xs-momentum":
        reason = {"entry": "momo_buy", "exit": "momo_sell",
                  "increase": "momo_rebalance",
                  "decrease": "momo_rebalance"}[shape]
    else:
        reason = {"entry": "initial_buy", "exit": "final_exit",
                  "increase": "rebalance", "decrease": "rebalance"}[shape]
    return action, reason


# --------------------------------------------------------------------------
# v2 meanrev_vol - stdlib port of lab2/families.py meanrev_vol/_meanrev_loop
# --------------------------------------------------------------------------

def _sd20(px, s, j):
    """Sample stdev (ddof=1) of the last 20 daily returns through index j
    (pandas rolling(20).std() semantics)."""
    rs = [px[s][k] / px[s][k - 1] - 1.0 for k in range(j - 19, j + 1)]
    mu = sum(rs) / len(rs)
    return (sum((r - mu) ** 2 for r in rs) / (len(rs) - 1)) ** 0.5


def meanrev_vol_rows(px, n, kk, y, sel, z_exit):
    """Target-weight rows W[t] (dict sym->weight) decided at close t using
    only info through t-1, exactly lab2 _meanrev_loop: exits on close t-1 vs
    entry fill price (+y% TP / -z% stop), then worst-first entries among
    names whose n-day return (through t-1) <= -kk * sigma20 * sqrt(n), up to
    sel names at 1/sel each; entry fill price = close of day t."""
    T = len(px[UNIVERSE[0]])
    rows = []
    reasons = {}
    held = {}
    min_j = max(n, 20)            # rr needs j-n>=0; sigma20 needs j>=20
    for t in range(T):
        if t >= 1:
            for s in list(held):
                p = px[s][t - 1]
                if (p >= held[s] * (1.0 + y / 100.0)
                        or p <= held[s] * (1.0 - z_exit / 100.0)):
                    reasons[(t, s)] = ("v228_tp_exit" if p >= held[s] * (1.0 + y / 100.0)
                                       else "v228_stop_exit")
                    del held[s]
        j = t - 1
        if j >= min_j:
            cands = []
            for s in UNIVERSE:
                if s in held:
                    continue
                rr = px[s][j] / px[s][j - n] - 1.0
                thr = -kk * _sd20(px, s, j) * math.sqrt(n)
                if rr <= thr:
                    cands.append((rr, UNIVERSE.index(s), s))
            cands.sort()
            for _rr, _idx, s in cands:
                if len(held) >= sel:
                    break
                held[s] = px[s][t]
                reasons[(t, s)] = "v228_entry"
        rows.append({s: 1.0 / sel for s in held})
    return rows, reasons


# --------------------------------------------------------------------------
# v2 engine + killswitch - stdlib port of lab2/engine.py backtest() and
# apply_killswitch() (same-day cost accounting, as inside apply_killswitch)
# --------------------------------------------------------------------------

def run_v2(rows, reasons, px, dates, fi, killswitch):
    """v2 accounting chain: mult[t] = 1 + r - COST*to with r = W2[t-1].ret and
    to = |target_t - W2[t-1]| (lab2/engine.py killswitch accounting; without
    the overlay target_t == base row, so to is the plain one-way turnover).
    Killswitch (V306): two segments [1..fi-1] (warm-up; equity discarded) and
    [fi..] (forward; eq/peak reset to 1.0 at the boundary, effective weight
    row carried across) - mirroring lab2's per-split state reset. Trigger-day
    turnover follows v2 exactly: the cost of trading to the base target is
    charged even though the overlay then flattens to cash."""
    T = len(dates)
    W2 = [dict(r) for r in rows]
    fires = {"warmup": 0, "forward": 0}
    flatten_days, resume_days = set(), set()
    if killswitch:
        segments = [("warmup", 1, (fi - 1) if fi is not None else T - 1)]
        if fi is not None:
            segments.append(("forward", fi, T - 1))
        for name, s0, s1 in segments:
            eq = peak = 1.0
            resume_at = None
            for t in range(s0, s1 + 1):
                if resume_at is not None and t >= resume_at:
                    peak, resume_at = eq, None
                    resume_days.add(t)
                target = dict(rows[t]) if resume_at is None else {}
                prev = W2[t - 1]
                r = sum(w * (px[s][t] / px[s][t - 1] - 1.0)
                        for s, w in prev.items())
                to = sum(abs(target.get(s, 0.0) - prev.get(s, 0.0))
                         for s in set(target) | set(prev))
                eq *= (1.0 + r - COST * to)
                if resume_at is None:
                    if eq > peak:
                        peak = eq
                    if eq <= peak * (1.0 + KS_TRIGGER):
                        fires[name] += 1
                        W2[t] = {}
                        flatten_days.add(t)
                        resume_at = t + KS_SESSIONS
                    else:
                        W2[t] = dict(rows[t])
                else:
                    W2[t] = {}

    # forward-window recording pass over the stored effective rows
    series, trades = [], []
    eq_abs = None
    mults = [1.0] * T
    fwd_eq = [None] * T
    for t in range(1, T):
        if fi is not None and t == fi:
            eq_abs = START_EQUITY
        if eq_abs is None:
            continue
        prev, cur = W2[t - 1], W2[t]
        r = sum(w * (px[s][t] / px[s][t - 1] - 1.0) for s, w in prev.items())
        if killswitch:
            target = ({} if (t in flatten_days
                             or any(f < t < f + KS_SESSIONS for f in flatten_days))
                      else dict(rows[t]))
            to = sum(abs(target.get(s, 0.0) - prev.get(s, 0.0))
                     for s in set(target) | set(prev))
        else:
            to = sum(abs(cur.get(s, 0.0) - prev.get(s, 0.0))
                     for s in set(cur) | set(prev))
        eq_pre = eq_abs * (1.0 + r)
        for s in set(cur) | set(prev):
            delta = cur.get(s, 0.0) - prev.get(s, 0.0)
            if abs(delta) <= EPS:
                continue
            qty = abs(delta) * eq_pre / px[s][t]
            action = "buy" if delta > 0 else "sell"
            reason = reasons.get((t, s))
            if reason is None:
                if t in flatten_days:
                    reason = "killswitch_flatten"
                elif t in resume_days:
                    reason = "killswitch_resume"
                else:
                    reason = "v228_rebalance"
            trades.append({"date": dates[t], "action": action, "symbol": s,
                           "qty": round(qty, 4), "reason": reason,
                           "price": round(px[s][t], 4)})
        eq_abs *= (1.0 + r - COST * to)
        mults[t] = 1.0 + r - COST * to
        fwd_eq[t] = eq_abs
        series.append({"date": dates[t], "equity": round(eq_abs, 2),
                       "exposure": round(sum(cur.values()), 6)})
    last_w = dict(W2[-1]) if W2 else {}
    return series, trades, last_w, (eq_abs if eq_abs is not None else START_EQUITY), fires, mults, fwd_eq


# --------------------------------------------------------------------------
# Recording / state assembly
# --------------------------------------------------------------------------

def positions_from_weights(w, eq_abs, px, dates, trades):
    pos = {}
    last_i = len(dates) - 1
    for s, wt in sorted(w.items()):
        if wt <= EPS:
            continue
        price = px[s][last_i]
        qty = wt * eq_abs / price
        entry_date, entry_price = None, None
        for tr in reversed(trades):
            if tr["symbol"] == s and tr["action"] == "buy":
                entry_date, entry_price = tr["date"], price
                break
        pos[s] = {"qty": round(qty, 4), "weight": round(wt, 6),
                  "last_price": round(price, 4),
                  "value": round(wt * eq_abs, 2),
                  "entry_date": entry_date}
    return pos


def anchor(exposure):
    return {"date": FORWARD_START, "equity": round(START_EQUITY, 2),
            "exposure": round(exposure, 6),
            "note": ("window-start anchor (virtual cash; no forward session "
                     "close recorded yet - first session close replaces this "
                     "row on the next run with data)")}


def build_state(dates, px, fetch_manifest, dropped):
    fi = next((i for i, d in enumerate(dates) if d >= FORWARD_START), None)
    last_date = dates[-1]

    # SPY buy-and-hold benchmark over the same window (ref = last close
    # strictly before the window, so the benchmark earns day-1 like variants)
    if fi is not None:
        ref = px[BENCH][fi - 1]
        spy_series = [{"date": dates[i],
                       "equity": round(START_EQUITY * px[BENCH][i] / ref, 2)}
                      for i in range(fi, len(dates))]
        spy_ret = spy_series[-1]["equity"] / START_EQUITY - 1.0
    else:
        spy_series = [anchor(1.0)]
        spy_ret = 0.0

    variants_state = {}
    for spec in VARIANT_SPECS:
        vid, fam = spec["id"], spec["family"]
        if fam == "donchian":
            strat = Donchian(px, spec["params"]["p"], spec["params"]["stop"])
            series, trades, last_w, eq, _m, _e = run_v1(strat, px, dates, fi, "donchian")
        elif fam == "xs-momentum":
            p = spec["params"]
            strat = XSMomentum(px, p["n"], p["k"], p["abs_filter"], p["cadence"])
            series, trades, last_w, eq, _m, _e = run_v1(strat, px, dates, fi, "xs-momentum")
        elif fam == "baseline":
            strat = Fixed(px, {s: SLEEVE for s in UNIVERSE})
            series, trades, last_w, eq, _m, _e = run_v1(strat, px, dates, fi, "baseline")
        else:
            p = v2_engine_params(VARIANT_SPECS)
            rows, reasons = meanrev_vol_rows(px, p["n"], p["kvol"], p["y"],
                                             p["sel"], p["z_exit"])
            series, trades, last_w, eq, fires, _m, _e = run_v2(
                rows, reasons, px, dates, fi, killswitch=(fam == "killswitch_overlay"))
        if fi is None:
            series = [anchor(sum(last_w.values()))]
            trades = []
            eq = START_EQUITY
        exposures = [row["exposure"] for row in series
                     if "note" not in row] or [sum(last_w.values())]
        variants_state[vid] = {
            "family": fam,
            "source": spec["source"],
            "description": spec["description"],
            "params": spec["params"],
            "as_of_date": last_date,
            "equity_series": series,
            "current_positions": positions_from_weights(
                last_w, eq, px, dates, trades),
            "trades": trades,
            "trade_count": len(trades),
            "return_to_date": round(eq / START_EQUITY - 1.0, 6),
            "current_equity": round(eq, 2),
            "current_exposure": round(sum(last_w.values()), 6),
            "avg_exposure": round(sum(exposures) / len(exposures), 6),
        }
        if fam == "killswitch_overlay":
            variants_state[vid]["killswitch_fires"] = fires

    state = {
        "meta": {
            "tracker": "forward OOS tracker (local simulation, virtual money)",
            "generated_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "engine": ("no-lookahead: signals from closes <= t-1, execution at "
                       "t close; v1 variants (1-4) under lab/backtest.py "
                       "accounting ((1+r)*(1-cost), drift between targets); "
                       "v2 variants (5-6) under lab2/engine.py killswitch "
                       "accounting ((1+r-cost))"),
            "cost_bps_per_side": 5,
            "forward_start": FORWARD_START,
            "start_equity": START_EQUITY,
            "forward_window_days": 0 if fi is None else len(dates) - fi,
            "warmup_range": [dates[0], dates[-1] if fi is None else dates[fi - 1]],
            "last_data_date": last_date,
            "universe_traded": UNIVERSE,
            "benchmark": BENCH,
            "idempotency": ("state is fully recomputed from forward/data CSVs "
                            "on every run and written atomically; re-running "
                            "on the same UTC day overwrites, never appends"),
            "data": {"fetch_manifest": fetch_manifest,
                     "dropped_misaligned_rows": dropped},
        },
        "benchmark": {BENCH: {"series": spy_series,
                              "return_to_date": round(spy_ret, 6)}},
        "variants": variants_state,
    }
    return state, fi


def comparison_json(state, fi):
    comp = {
        "generated_utc": state["meta"]["generated_utc"],
        "forward_start": FORWARD_START,
        "last_data_date": state["meta"]["last_data_date"],
        "forward_window_days": state["meta"]["forward_window_days"],
        "benchmark": {
            "SPY": {"return_to_date": state["benchmark"]["SPY"]["return_to_date"],
                    "series": state["benchmark"]["SPY"]["series"]},
        },
        "variants": {},
    }
    for vid, v in state["variants"].items():
        comp["variants"][vid] = {
            "return_to_date": v["return_to_date"],
            "current_positions": {s: p["weight"] for s, p in
                                  v["current_positions"].items()},
            "trade_count": v["trade_count"],
            "current_exposure": v["current_exposure"],
            "avg_exposure": v["avg_exposure"],
            "current_equity": v["current_equity"],
            "as_of_date": v["as_of_date"],
        }
        if "killswitch_fires" in v:
            comp["variants"][vid]["killswitch_fires"] = v["killswitch_fires"]
    return comp


def write_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def replay(dates, px, fetch_manifest, dropped):
    return build_state(dates, px, fetch_manifest, dropped)[0]


def main():
    fetch_manifest = refresh_data()
    dates, px, dropped = load_aligned()
    state, fi = build_state(dates, px, fetch_manifest, dropped)

    # determinism guard: a second replay from the same data must be identical
    state2 = replay(dates, px, fetch_manifest, dropped)
    strip = lambda s: {k: v for k, v in s.items() if k != "meta"}  # noqa: E731
    if json.dumps(strip(state), sort_keys=True) != json.dumps(strip(state2), sort_keys=True):
        raise RuntimeError("non-deterministic replay")

    write_state(state)
    return state, comparison_json(state, fi), fi


if __name__ == "__main__":
    _state, _comp, _fi = main()
    print(json.dumps(_comp, indent=1, sort_keys=True))
