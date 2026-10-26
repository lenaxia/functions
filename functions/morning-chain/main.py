"""morning-chain — 13:45 UTC weekdays: debate -> decide -> execute.

Calls the step functions through the Fission router (in-cluster service by
default; FISSION_ROUTER_URL overrides). Per-step timeouts match the step
functions' own limits:

  debate   2400s (40 min LLM budget)
  decide    480s (marketdata sweep + gates)
  execute   300s (reconcile)

Semantics:
  - every step is idempotent per run_date: steps already in
    step_completions are skipped (safe re-runs / manual retries)
  - debate failure does NOT abort — decide runs with the fallback flag
    (missing debate = no vetoes, never blocks)
  - decide failure ABORTS the chain (execute is skipped) — but retry
    passes below re-walk from the top, so a recovered decide still gets
    its execute on a later pass
  - RETRY: after the first pass, failed steps are retried up to
    MAX_RETRY_PASSES more times while the chain's time budget allows
    (transient failures: cold-start timeouts, node blips, Yahoo 5xx).
    A pass re-checks step_completions first, so concurrent completions
    are never double-run.
  - observability: every chain writes an events row ('chain'); final
    missing steps fire a TradingChainIncomplete alert (critical ->
    pushover); a chain recovered only by retries fires an info alert.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from trading_common.db import TradingDB, utc_today
from trading_common.notify import alert
from trading_common.secrets import secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHAIN_NAME = "morning-chain"
STEPS = [
    {"step": "debate", "path": "/debate", "timeout": 2400, "optional": True},
    {"step": "decide", "path": "/decide", "timeout": 480, "optional": False},
    {"step": "execute", "path": "/execute", "timeout": 300, "optional": False},
]
# functionTimeout is 3480s; keep a safety margin for JSON/DB/overhead.
CHAIN_BUDGET_SECONDS = 3300
MIN_STEP_BUDGET_SECONDS = 120   # don't start a step we can't finish
MAX_RETRY_PASSES = 2
RETRY_BACKOFF_SECONDS = 45


def router_url(path: str) -> str:
    base = secret("FISSION_ROUTER_URL", "http://router.fission.svc.cluster.local").rstrip("/")
    return base + path


def call_step(path: str, timeout: int) -> dict:
    # POST with no body: the step functions' main() take no arguments (each
    # derives run_date from its own clock and is idempotent per run_date)
    req = urllib.request.Request(router_url(path), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"ok": False, "error": "HTTP %s: %s" % (e.code, body)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


def run_pass(db: TradingDB, today: str, budget_for: "callable") -> tuple[list[dict], str | None]:
    """One ordered walk over STEPS. Returns (step records, aborted_at)."""
    done: list[dict] = []
    aborted: str | None = None
    for s in STEPS:
        step = s["step"]
        timeout = budget_for(s["timeout"])
        if timeout < MIN_STEP_BUDGET_SECONDS:
            done.append({"step": step, "ok": False, "skipped": "out_of_budget"})
            aborted = aborted or step
            break
        if db.step_done(today, step):
            done.append({"step": step, "skipped": "already_done"})
            continue
        res = call_step(s["path"], timeout)
        ok = bool(res.get("ok"))
        done.append({"step": step, "ok": ok,
                     "already_done": bool(res.get("already_done")),
                     "error": res.get("error")})
        logger.info("step %s -> ok=%s", step, ok)
        if not ok:
            if s["optional"]:
                logger.warning("optional step %s failed; continuing with fallback", step)
                continue
            aborted = step
            break
    return done, aborted


def main() -> dict:
    today = utc_today()
    started = time.monotonic()
    records: list[dict] = []
    aborted: str | None = None
    retry_passes = 0

    def budget_for(step_timeout: int) -> int:
        left = CHAIN_BUDGET_SECONDS - (time.monotonic() - started)
        return int(min(step_timeout, max(left, 0)))

    db = TradingDB()
    try:
        records, aborted = run_pass(db, today, budget_for)

        def step_record(step: str) -> dict | None:
            return next((r for r in records if r.get("step") == step), None)

        def missing() -> list[str]:
            # only REQUIRED steps count against ok (debate failure is
            # tolerated by design — decide runs with the fallback flag)
            out = []
            for s in STEPS:
                if s["optional"]:
                    continue
                r = step_record(s["step"])
                if r is None or (not r.get("ok") and r.get("skipped") != "already_done"):
                    out.append(s["step"])
            return out

        while aborted and retry_passes < MAX_RETRY_PASSES:
            left = CHAIN_BUDGET_SECONDS - (time.monotonic() - started)
            if left < MIN_STEP_BUDGET_SECONDS:
                logger.warning("chain budget exhausted; no retries")
                break
            retry_passes += 1
            logger.warning("retry pass %d for missing=%s (budget left %.0fs)",
                           retry_passes, aborted, left)
            time.sleep(RETRY_BACKOFF_SECONDS)
            more, aborted = run_pass(db, today, budget_for)
            # merge: latest record per step wins; keep first-pass errors in history
            by_step = {r.get("step"): r for r in records if r.get("step")}
            for r in more:
                prev = by_step.get(r.get("step"))
                if prev is not None:
                    r["attempts"] = prev.get("attempts", 1) + 1
                    if prev.get("error"):
                        r["first_error"] = prev["error"]
                by_step[r.get("step")] = r
            records = [by_step.get(s["step"], {"step": s["step"], "ok": False,
                                               "skipped": "not_attempted",
                                               "attempts": 0})
                       for s in STEPS]

        missing = missing()
        ok = not missing
        db.add_event("chain", {
            "chain": CHAIN_NAME, "date": today, "ok": ok,
            "steps": {r.get("step"): {"ok": r.get("ok"), "skipped": r.get("skipped"),
                                      "attempts": r.get("attempts", 1)}
                      for r in records},
            "retries": retry_passes, "missing": missing,
            "aborted_at": aborted,
        })
    finally:
        db.close()

    if not ok:
        alert("TradingChainIncomplete",
              "%s incomplete on %s: missing %s" % (CHAIN_NAME, today, ",".join(missing)),
              "steps=%s retries=%d" % (json.dumps(
                  {r.get("step"): r.get("ok", r.get("skipped")) for r in records}),
                  retry_passes),
              severity="critical")
    elif retry_passes:
        alert("TradingChainRecovered",
              "%s recovered on retry on %s" % (CHAIN_NAME, today),
              "retries=%d" % retry_passes, severity="info")

    result = {"ok": ok, "date": today, "steps": records,
              "retries": retry_passes, "missing": missing,
              "aborted_at": aborted,
              "now_utc": datetime.now(timezone.utc).isoformat()}
    if aborted and not ok:
        logger.error("chain incomplete after %s failure", aborted)
    return result
