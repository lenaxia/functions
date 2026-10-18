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
  - decide failure ABORTS the chain (execute is skipped; the afternoon
    digest watchdog reports the missing steps)
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

from trading_common.db import TradingDB, utc_today
from trading_common.secrets import secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STEPS = [
    {"step": "debate", "path": "/debate", "timeout": 2400, "optional": True},
    {"step": "decide", "path": "/decide", "timeout": 480, "optional": False},
    {"step": "execute", "path": "/execute", "timeout": 300, "optional": False},
]


def router_url(path: str) -> str:
    base = secret("FISSION_ROUTER_URL", "http://router.fission.svc.cluster.local").rstrip("/")
    return base + path


def call_step(path: str, timeout: int, run_date: str) -> dict:
    req = urllib.request.Request(
        router_url(path), data=json.dumps({"run_date": run_date}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"ok": False, "error": "HTTP %s: %s" % (e.code, body)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


def main() -> dict:
    today = utc_today()
    done: list[dict] = []
    aborted: str | None = None

    pre = {}
    db = TradingDB()
    try:
        for s in STEPS:
            pre[s["step"]] = db.step_done(today, s["step"])
    finally:
        db.close()

    for s in STEPS:
        step = s["step"]
        if pre.get(step):
            done.append({"step": step, "skipped": "already_done"})
            continue
        res = call_step(s["path"], s["timeout"], today)
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

    result = {"ok": aborted is None, "date": today, "steps": done, "aborted_at": aborted,
              "now_utc": datetime.now(timezone.utc).isoformat()}
    if aborted:
        logger.error("chain aborted after %s failure", aborted)
    return result
