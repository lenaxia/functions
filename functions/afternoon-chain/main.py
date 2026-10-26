"""afternoon-chain — 22:30 UTC weekdays: score -> digest.

Calls the step functions through the Fission router (in-cluster service by
default; FISSION_ROUTER_URL overrides). Per-step timeouts:

  score    900s (3y refetch + 6-variant replay + control mark)
  digest   120s (watchdog + summary)

Semantics:
  - steps already in step_completions for the run_date are skipped
  - digest runs EVEN IF score fails — reporting missing steps is exactly
    its job (the failure surfaces in the watchdog line)
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

CHAIN_NAME = "afternoon-chain"
STEPS = [
    {"step": "score", "path": "/score", "timeout": 900},
    {"step": "digest", "path": "/digest", "timeout": 120},
]
# functionTimeout is 1140s; keep a safety margin for JSON/DB/overhead.
CHAIN_BUDGET_SECONDS = 1050
MIN_STEP_BUDGET_SECONDS = 60
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


def run_pass(db: TradingDB, today: str, budget_for: "callable") -> list[dict]:
    """One ordered walk over STEPS (no abort: digest always runs)."""
    done: list[dict] = []
    for s in STEPS:
        step = s["step"]
        timeout = budget_for(s["timeout"])
        if timeout < MIN_STEP_BUDGET_SECONDS:
            done.append({"step": step, "ok": False, "skipped": "out_of_budget"})
            continue
        if db.step_done(today, step):
            done.append({"step": step, "skipped": "already_done"})
            continue
        res = call_step(s["path"], timeout)
        ok = bool(res.get("ok"))
        done.append({"step": step, "ok": ok,
                     "already_done": bool(res.get("already_done")),
                     "error": res.get("error")})
        logger.info("step %s -> ok=%s", step, ok)
        if step == "score" and not ok:
            logger.warning("score failed; running digest anyway (watchdog)")
    return done


def main() -> dict:
    today = utc_today()
    started = time.monotonic()
    records: list[dict] = []
    retry_passes = 0

    def budget_for(step_timeout: int) -> int:
        left = CHAIN_BUDGET_SECONDS - (time.monotonic() - started)
        return int(min(step_timeout, max(left, 0)))

    db = TradingDB()
    try:
        records = run_pass(db, today, budget_for)

        def step_record(step: str) -> dict | None:
            return next((r for r in records if r.get("step") == step), None)

        def missing() -> list[str]:
            out = []
            for s in STEPS:
                r = step_record(s["step"])
                if r is None or (not r.get("ok") and r.get("skipped") != "already_done"):
                    out.append(s["step"])
            return out

        miss = missing()
        while miss and retry_passes < MAX_RETRY_PASSES:
            left = CHAIN_BUDGET_SECONDS - (time.monotonic() - started)
            if left < MIN_STEP_BUDGET_SECONDS:
                logger.warning("chain budget exhausted; no retries")
                break
            retry_passes += 1
            logger.warning("retry pass %d for missing=%s (budget left %.0fs)",
                           retry_passes, miss, left)
            time.sleep(RETRY_BACKOFF_SECONDS)
            more = run_pass(db, today, budget_for)
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
            miss = missing()

        ok = not miss
        db.add_event("chain", {
            "chain": CHAIN_NAME, "date": today, "ok": ok,
            "steps": {r.get("step"): {"ok": r.get("ok"), "skipped": r.get("skipped"),
                                      "attempts": r.get("attempts", 1)}
                      for r in records},
            "retries": retry_passes, "missing": miss,
        })
    finally:
        db.close()

    if not ok:
        alert("TradingChainIncomplete",
              "%s incomplete on %s: missing %s" % (CHAIN_NAME, today, ",".join(miss)),
              "steps=%s retries=%d" % (json.dumps(
                  {r.get("step"): r.get("ok", r.get("skipped")) for r in records}),
                  retry_passes),
              severity="critical")
    elif retry_passes:
        alert("TradingChainRecovered",
              "%s recovered on retry on %s" % (CHAIN_NAME, today),
              "retries=%d" % retry_passes, severity="info")

    return {"ok": ok, "date": today, "steps": records,
            "retries": retry_passes, "missing": miss,
            "now_utc": datetime.now(timezone.utc).isoformat()}
