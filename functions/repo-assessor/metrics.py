"""Prometheus metrics definition and lifecycle.

A single Metrics instance owns the collectors. The HTTP server is started
once on first instantiation (module-level registry) and serves /metrics
at METRICS_PORT. In Fission the same pod serves the function and the
metrics endpoint concurrently.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    start_http_server,
)


_LOG = logging.getLogger(__name__)
_started = False
_started_lock = threading.Lock()


class Metrics:
    def __init__(self, config: dict | Any) -> None:
        self._start_server_once(int(config.get("metrics_port", 8080)))

        self.runs = Counter(
            "repo_assessor_runs_total",
            "Fission invocations of repo-assessor",
            ["result"],
        )
        self.posts = Counter(
            "repo_assessor_posts_total",
            "Per-post outcomes",
            ["outcome"],
        )
        self.workspace_creations = Counter(
            "repo_assessor_workspace_creations_total",
            "Workspace create attempts",
            ["result"],
        )
        self.workspace_active_seconds = Histogram(
            "repo_assessor_workspace_active_seconds",
            "Wall-clock seconds workspace was Active during a run",
        )
        self.session_stage_seconds = Histogram(
            "repo_assessor_session_duration_seconds",
            "Per-prompt wall-clock",
            ["stage"],
        )
        self.classification_decisions = Counter(
            "repo_assessor_classification_decision_total",
            "Classifier outcomes",
            ["decision"],
        )
        self.assessment_scores = Histogram(
            "repo_assessor_assessment_score",
            "Score distribution per dimension",
            ["dimension"],
            buckets=(1, 2, 3, 4, 5),
        )
        self.reddit_api_calls = Counter(
            "repo_assessor_reddit_api_calls_total",
            "Reddit API usage",
            ["endpoint", "result"],
        )
        self.state_ops = Counter(
            "repo_assessor_state_backend_operations_total",
            "State backend health",
            ["backend", "op", "result"],
        )
        self.in_flight_gauge = Gauge(
            "repo_assessor_in_flight_gauge",
            "Current in-flight post count",
        )
        self.run_info = Info(
            "repo_assessor",
            "Build/runtime info",
        )
        self.run_info.info({"subreddit": str(config.get("reddit_subreddit", "selfhosted"))})

    @staticmethod
    def _start_server_once(port: int) -> None:
        global _started
        with _started_lock:
            if _started:
                return
            try:
                start_http_server(port)
                _started = True
                _LOG.info("prometheus metrics listening on :%d/metrics", port)
            except OSError as e:
                _LOG.warning("prometheus http server start failed (port %d): %s", port, e)
