"""Alerting for the trading chain via the cluster Alertmanager.

Posts alerts to the in-cluster vmalertmanager v2 API. The cluster's
default route is the pushover receiver (severity != info pages the
owner's phone; severity == "info" is routed to the null receiver and
only shows up in the Alertmanager UI), so no extra credentials are
needed — reachability is the only requirement.

Alerts are fire-and-forget: starts_at=now, ends_at=now+5m so they fire
once and auto-resolve instead of lingering as stuck notifications.
Delivery is best-effort: alert() never raises (a broken alerting path
must not fail a trading step; the event row + git journal are the
system of record).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .secrets import secret

_LOG = logging.getLogger(__name__)

DEFAULT_ALERTMANAGER_URL = "http://vmalertmanager-victoria-metrics.monitoring.svc.cluster.local:9093"
ALERT_TTL_SECONDS = 300  # fire once, auto-resolve


def _alertmanager_url() -> str:
    return secret("ALERTMANAGER_URL", DEFAULT_ALERTMANAGER_URL).rstrip("/")


def _post_alerts(alerts: list[dict], timeout: float = 10.0) -> bool:
    url = _alertmanager_url() + "/api/v2/alerts"
    body = json.dumps(alerts).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception as e:  # noqa: BLE001
        _LOG.warning("alert post failed: %s", e)
        return False


def alert(alertname: str, summary: str, description: str = "",
          severity: str = "warning") -> bool:
    """Fire a one-shot alert. severity 'critical'|'warning' pages (pushover
    default route); 'info' is recorded but not routed to a receiver."""
    now = datetime.now(timezone.utc)
    payload = [{
        "labels": {
            "alertname": alertname,
            "severity": severity,
            "source": "trading",
            "job": "trading-chain",
        },
        "annotations": {
            "summary": summary,
            "description": description or summary,
        },
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(seconds=ALERT_TTL_SECONDS)).isoformat(),
    }]
    ok = _post_alerts(payload)
    _LOG.info("alert %s (%s) posted=%s", alertname, severity, ok)
    return ok
