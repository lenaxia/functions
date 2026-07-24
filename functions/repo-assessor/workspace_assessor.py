"""Workspace lifecycle + assessment wrapper around the LLMSafeSpaces SDK.

Single workspace per Fission run; one session per post inside the
workspace. Classify and assess are sequential messages within the same
session so the agent retains the classification context for assessment.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model import (
    AIUsageAssessment,
    AIUsageLevel,
    Assessment,
    AssessmentParseError,
    Category,
    Classification,
    ConcernLevel,
    ConfigError,
    RepoMetadata,
    ScoreEvidence,
    SecurityScore,
    WorkspaceError,
    WorkspaceNotActive,
)


_LOG = logging.getLogger(__name__)
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_CLASSIFY_PLACEHOLDERS = ("TITLE", "BODY", "URL", "GITHUB_REPOS")
_ASSESS_PLACEHOLDERS = ("REPO_URL", "CATEGORY")

_RECOGNISED_CLASSIFY_PLACEHOLDERS = set(_CLASSIFY_PLACEHOLDERS)
_RECOGNISED_ASSESS_PLACEHOLDERS = set(_ASSESS_PLACEHOLDERS)


class WorkspaceAssessor:
    def __init__(self, config: dict | Any, client: Any) -> None:
        self._runtime = config.get("llmsafespaces_runtime", "python")
        self._model = config.get("llmsafespaces_model")
        self._ready_timeout = int(config.get("workspace_ready_timeout", 300))
        self._health_interval = int(config.get("workspace_health_poll_interval", 30))
        self._client = client

    def create_workspace(self, name: str) -> Any:
        # Use _request directly; published SDK 0.5.4's workspaces.create
        # raises TypeError on the server's agentNeedsRefresh field.
        body = {"name": name, "runtime": self._runtime}
        payload = self._client._request("POST", "/workspaces", json=body)
        return _WorkspaceHandle(
            id=payload.get("id", ""),
            phase=payload.get("phase", ""),
        )

    def wait_for_active(self, workspace_id: str, *, poll_interval: float | None = None) -> Any:
        interval = poll_interval if poll_interval is not None else self._health_interval
        deadline = time.monotonic() + self._ready_timeout
        while True:
            status = self._client.workspaces.get_status(workspace_id)
            phase = status.get("phase", "Unknown")
            if phase == "Active":
                if self._model:
                    self._client.workspaces.set_model(workspace_id, self._model)
                    _LOG.info("set model=%s on workspace %s", self._model, workspace_id)
                return status
            if phase in ("Failed", "Terminated"):
                raise WorkspaceError(f"workspace {workspace_id} entered terminal phase {phase}")
            if time.monotonic() >= deadline:
                raise WorkspaceNotActive(
                    f"workspace {workspace_id} not Active after {self._ready_timeout}s (last phase: {phase})"
                )
            time.sleep(max(0.0, interval))

    def health_check(self, workspace_id: str) -> bool:
        try:
            status = self._client.workspaces.get_status(workspace_id)
        except Exception as e:
            _LOG.warning("workspace health check failed: %s", e)
            return False
        return status.get("phase") == "Active"

    def create_session(self, workspace_id: str) -> str:
        # Use _request directly; sessions.ensure hits EnsureSessionResponse
        # dataclass which is currently fine but matches the pattern.
        payload = self._client._request("POST", f"/workspaces/{workspace_id}/sessions/new")
        return payload.get("sessionId", "")

    def classify(
        self,
        workspace_id: str,
        session_id: str,
        post: Any,
        github_url: str,
    ) -> Classification:
        prompt = _render_prompt(
            "classify.md",
            _CLASSIFY_PLACEHOLDERS,
            TITLE=post.title,
            BODY=post.selftext or "",
            URL=post.url or "",
            GITHUB_REPOS=github_url,
        )
        raw = _send_and_get_content(self._client, workspace_id, session_id, prompt)
        payload = _parse_json(raw, "classification")
        return _build_classification(payload)

    def assess(
        self,
        workspace_id: str,
        session_id: str,
        github_url: str,
        category: Category,
    ) -> Assessment:
        prompt = _render_prompt(
            "assess.md",
            _ASSESS_PLACEHOLDERS,
            REPO_URL=github_url,
            CATEGORY=category.value,
        )
        raw = _send_and_get_content(self._client, workspace_id, session_id, prompt)
        payload = _parse_json(raw, "assessment")
        return _build_assessment(payload)

    def delete_workspace(self, workspace_id: str | None) -> None:
        if not workspace_id:
            return
        try:
            self._client._request("DELETE", f"/workspaces/{workspace_id}")
        except Exception as e:
            _LOG.warning("failed to delete workspace %s: %s", workspace_id, e)


def _render_prompt(filename: str, recognised: tuple[str, ...], **substitutions: str) -> str:
    path = _PROMPTS_DIR / filename
    template = path.read_text()
    placeholders_in_template = set(re.findall(r"{{([A-Z_]+)}}", template))
    missing = [p for p in recognised if "{{" + p + "}}" not in template]
    if missing:
        raise ConfigError(
            f"prompt template {filename} missing required placeholders: {sorted(missing)}"
        )
    unknown = placeholders_in_template - set(recognised)
    for u in sorted(unknown):
        _LOG.warning("prompt template %s has unknown placeholder {{%s}}", filename, u)
    rendered = template
    for key, value in substitutions.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _extract_text(response: Any) -> str:
    if response is None:
        return ""
    if hasattr(response, "content"):
        return response.content or ""
    if isinstance(response, dict):
        return response.get("content") or ""
    return str(response) or ""


def _send_and_get_content(client: Any, workspace_id: str, session_id: str, prompt: str) -> str:
    """Send a message via the SDK and return the assistant's text content.

    Uses _request directly (same code path the SDK's send_message takes,
    but bypasses the SDK's Workspace/APIKey dataclass construction that
    is broken in published 0.5.4 against the live server).
    """
    body = {
        "content": prompt,
        "parts": [{"type": "text", "text": prompt}],
    }
    raw = client._request(
        "POST",
        f"/workspaces/{workspace_id}/sessions/{session_id}/message",
        json=body,
    )
    if isinstance(raw, dict):
        parts = raw.get("parts", [])
        if parts:
            return "".join(
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("type") == "text"
            )
        return raw.get("content") or ""
    return str(raw) if raw else ""


def _parse_json(raw: str, label: str) -> dict:
    if not raw or not raw.strip():
        raise AssessmentParseError(f"empty {label} response", raw=raw)
    cleaned = _strip_code_fence(raw)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AssessmentParseError(f"{label} response has no JSON object", raw=raw)
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise AssessmentParseError(f"{label} JSON parse failed: {e}", raw=raw) from e


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped


def _build_classification(payload: dict) -> Classification:
    required = ("is_announcement", "is_major_update", "category", "reason")
    missing = [k for k in required if k not in payload]
    if missing:
        raise AssessmentParseError(f"classification missing keys: {missing}", raw=json.dumps(payload))
    try:
        category = Category(payload["category"])
    except ValueError as e:
        raise AssessmentParseError(
            f"unknown category {payload['category']!r}",
            raw=json.dumps(payload),
        ) from e
    return Classification(
        is_announcement=bool(payload["is_announcement"]),
        is_major_update=bool(payload["is_major_update"]),
        category=category,
        reason=str(payload["reason"]),
    )


def _build_assessment(payload: dict) -> Assessment:
    try:
        metadata = _build_metadata(payload.get("metadata") or {})
        analysis = payload["analysis"]
        ratio_field = analysis["test_to_code_ratio"]
        ratio_value = float(ratio_field.get("ratio")) if ratio_field.get("ratio") is not None else 0.0
        ai_payload = payload.get("ai_usage") or {}
        return Assessment(
            metadata=metadata,
            code_quality=_build_score(analysis["code_quality"]),
            release_ci_flow=_build_score(analysis["release_ci_flow"]),
            test_to_code_ratio=(ratio_value, _build_score(ratio_field)),
            architectural_robustness=_build_score(analysis["architectural_robustness"]),
            security=SecurityScore(
                internal_only=_build_score(analysis["security_internal_only"]),
                reverse_proxy=_build_score(analysis["security_reverse_proxy"]),
                sso=_build_score(analysis["security_sso"]),
            ),
            ai_usage=AIUsageAssessment(
                signals=list(ai_payload.get("signals") or []),
                level=AIUsageLevel(ai_payload.get("level", "none")),
                evidence=str(ai_payload.get("evidence", "")),
            ),
            overall_concern_vs_baseline=ConcernLevel(
                payload.get("overall_concern_vs_baseline", "similar")
            ),
            key_concerns=list(payload.get("key_concerns") or []),
            key_strengths=list(payload.get("key_strengths") or []),
            tldr=str(payload.get("tldr", "")),
        )
    except KeyError as e:
        raise AssessmentParseError(f"assessment missing required key {e}", raw=json.dumps(payload)) from e
    except (ValueError, TypeError) as e:
        raise AssessmentParseError(f"assessment shape invalid: {e}", raw=json.dumps(payload)) from e


def _build_metadata(payload: dict) -> RepoMetadata:
    return RepoMetadata(
        repo_url=str(payload.get("repo_url") or ""),
        repo_created=_parse_date(payload.get("repo_created")),
        repo_latest_commit=_parse_date(payload.get("repo_latest_commit")),
        repo_age_days=_opt_int(payload.get("repo_age_days")),
        total_commits=_opt_int(payload.get("total_commits")),
        avg_commits_per_month=_opt_float(payload.get("avg_commits_per_month")),
        commits_last_3_months=_opt_int(payload.get("commits_last_3_months")),
        pct_commits_last_3_months=_opt_float(payload.get("pct_commits_last_3_months")),
        contributors=_opt_int(payload.get("contributors")),
        stars=_opt_int(payload.get("stars")),
        open_issues=_opt_int(payload.get("open_issues")),
        license=str(payload.get("license") or "unknown"),
        primary_language=_opt_str(payload.get("primary_language")),
    )


def _build_score(payload: dict) -> ScoreEvidence:
    try:
        return ScoreEvidence(
            score=int(payload["score"]),
            evidence=str(payload.get("evidence", "")),
        )
    except (KeyError, ValueError) as e:
        raise AssessmentParseError(f"invalid score object {payload!r}: {e}", raw=str(payload)) from e


def _parse_date(value: Any) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value) or None


@dataclass(frozen=True)
class _WorkspaceHandle:
    """Lightweight workspace handle returned by create_workspace.

    Avoids the SDK's Workspace dataclass, which in published 0.5.4 raises
    TypeError on the server's agentNeedsRefresh field. Carries only what
    the orchestrator needs: id + initial phase.
    """
    id: str
    phase: str
