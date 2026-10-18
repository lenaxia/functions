"""afternoon-chain — 22:30 UTC weekdays: score -> digest.

Calls the step functions through the Fission router (in-cluster service by
default; FISSION_ROUTER_URL overrides). Per-step timeouts:

  score    900s (3y refetch + 6-variant replay + control mark)
  digest   120s (watchdog + summary)

Semantics:
  - steps already in step_completions for the run_date are skipped
  - digest runs EVEN IF score fails — reporting missing steps is exactly
    its job (the failure surfaces in the watchdog line)
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
    {"step": "score", "path": "/score", "timeout": 900},
    {"step": "digest", "path": "/digest", "timeout": 120},
]


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


def main() -> dict:
    today = utc_today()
    done: list[dict] = []

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
        res = call_step(s["path"], s["timeout"])
        ok = bool(res.get("ok"))
        done.append({"step": step, "ok": ok,
                     "already_done": bool(res.get("already_done")),
                     "error": res.get("error")})
        logger.info("step %s -> ok=%s", step, ok)
        if step == "score" and not ok:
            logger.warning("score failed; running digest anyway (watchdog)")

    result = {"ok": all(d.get("ok") or d.get("skipped") for d in done),
              "date": today, "steps": done,
              "now_utc": datetime.now(timezone.utc).isoformat()}
    return result
