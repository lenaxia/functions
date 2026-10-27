"""score — forward OOS tracker + CONTROL mark.

Port of ai-trading/forward/forward.py (verbatim via trading_common.forward:
refetch 3y daily OHLC, replay all 6 out-of-sample variants + SPY benchmark
under the no-lookahead convention, deterministic, idempotent per UTC day) and
ai-trading/control.py (verbatim via trading_common.control: buy-and-hold
clone, anchored 2026-09-29 at 100k).

Persisted:
  - equity_series: 'fwd:<variant_id>' per replay date, 'fwd:SPY' benchmark,
    'control' nightly mark
  - decisions(run_date, 'score'): comparison JSON
  - decisions sentinel rows ('control_state', 'sleeve_weights') carry the
    control anchor state and the personal sleeve weights
  - step_completions(run_date, 'score')

Sleeve weights (personal data) are fetched from lenaxia/ai-trading
personal_sleeve.json via the GitHub token in the `trading` secret — never
committed to this repo. Falls back to the DB sentinel once seeded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from trading_common import control, forward, gitstate
from trading_common.db import TradingDB
from trading_common.secrets import flag

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_sleeve(db: TradingDB) -> dict | None:
    sleeve = gitstate.read_json("personal_sleeve.json")
    if sleeve:
        db.record_decision("1970-01-01", "sleeve_weights", sleeve, status="ok")
        return sleeve
    row = db.get_decision("1970-01-01", "sleeve_weights")
    return row["payload"] if row else None


def load_control_state(db: TradingDB) -> dict:
    row = db.get_decision("1970-01-01", "control_state")
    if row:
        st = dict(row["payload"])
        st.setdefault("shares", {})
        st.setdefault("history", [])
        return st
    return {"shares": {}, "anchor_date": None, "history": []}


def run_forward(db: TradingDB) -> dict:
    state, comp, _fi = forward.main()
    for vid, v in state["variants"].items():
        for row in v["equity_series"]:
            if "note" not in row:
                db.upsert_equity("fwd:" + vid, row["date"], row["equity"])
    for row in state["benchmark"]["SPY"]["series"]:
        if "note" not in row:
            db.upsert_equity("fwd:SPY", row["date"], row["equity"])
    meta = {k: state["meta"][k] for k in
            ("forward_start", "start_equity", "forward_window_days",
             "last_data_date", "cost_bps_per_side")}
    db.record_decision("1970-01-01", "forward_meta", meta, status="ok")
    return comp


def run_control(db: TradingDB, sleeve: dict | None) -> dict:
    st = load_control_state(db)
    res, st = control.run(sleeve=sleeve, st=st)
    if res.get("ok") and not res.get("skipped") and not res.get("already_recorded"):
        db.upsert_equity("control", res["date"], res["equity"])
    if st.get("shares"):
        db.record_decision("1970-01-01", "control_state", st, status="ok")
    return res


def main() -> dict:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    shadow = flag("SHADOW", "1")
    db = TradingDB()
    try:
        if db.step_done(today, "score"):
            return {"ok": True, "date": today, "already_done": True, "step": "score"}

        comp = run_forward(db)
        ctrl = run_control(db, load_sleeve(db))

        payload = {"ok": True, "date": today, "forward": comp, "control": ctrl}
        db.record_decision(today, "score", payload, status="ok")
        db.mark_step(today, "score")
        db.add_event("score", {"date": today,
                               "variants": {k: v["return_to_date"]
                                            for k, v in comp.get("variants", {}).items()},
                               "spy": comp.get("benchmark", {}).get("SPY", {}).get("return_to_date"),
                               "control": ctrl.get("return_pct")})
        # journal unconditionally — shadow/ is the audit mirror for both modes
        gitstate.journal(today, "score", payload)
        return payload
    finally:
        db.close()
