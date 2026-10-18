"""Read state from the lenaxia/ai-trading repo (git is the source of truth
during the shadow comparison week) and write the shadow journal back to it.

Uses the GitHub contents API with a token from the `trading` secret
(GITHUB_TOKEN). Tokens are never logged or printed.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .secrets import secret

_LOG = logging.getLogger(__name__)

REPO = "lenaxia/ai-trading"
BRANCH = "main"
API = "https://api.github.com/repos/%s/contents/%%s?ref=%s" % (REPO, BRANCH)


def _headers(token: str) -> dict:
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "lenaxia-functions-trading"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def read_json(path: str) -> dict | None:
    """Fetch a JSON file from ai-trading@main; None if unavailable."""
    token = secret("GITHUB_TOKEN")
    req = urllib.request.Request(API % path, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read().decode())
        return json.loads(base64.b64decode(meta["content"]).decode())
    except Exception as e:  # noqa: BLE001 - degrade to None, caller decides
        _LOG.warning("gitstate read %s failed: %s", path, e)
        return None


def journal(run_date: str, step: str, payload: dict) -> bool:
    """Write shadow/<run_date>/<step>.json into ai-trading (create or update).

    Best-effort: returns False (and logs) when no token or the API fails; the
    DB event log remains the durable record either way.
    """
    token = secret("GITHUB_TOKEN")
    if not token:
        _LOG.warning("GITHUB_TOKEN not set; shadow journal skipped")
        return False
    path = "shadow/%s/%s.json" % (run_date, step)
    url = "https://api.github.com/repos/%s/contents/%s" % (REPO, path)
    body = json.dumps({
        "step": step,
        "run_date": run_date,
        "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payload": payload,
    }, indent=2, sort_keys=True, default=str) + "\n"

    def call(method: str, data: dict | None = None):
        req = urllib.request.Request(
            url, data=json.dumps(data).encode() if data else None,
            method=method, headers=_headers(token))
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")

    try:
        try:
            sha = call("GET")["sha"]  # existing file -> update in place
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            sha = None
        message = "[shadow] %s %s" % (run_date, step)
        call("PUT", {"message": message, "content": base64.b64encode(
            body.encode()).decode(), "branch": BRANCH, **({"sha": sha} if sha else {})})
        return True
    except Exception as e:  # noqa: BLE001
        _LOG.warning("shadow journal write failed: %s", e)
        return False
