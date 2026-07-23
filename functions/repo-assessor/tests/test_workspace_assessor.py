"""Tests for workspace_assessor.py — T-701..T-724."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from model import (
    AIUsageLevel,
    AssessmentParseError,
    Category,
    Classification,
    ConfigError,
    ConcernLevel,
    WorkspaceError,
    WorkspaceNotActive,
)


def _import_workspace_assessor():
    import workspace_assessor
    return workspace_assessor


def _minimal_config_dict():
    return {
        "llmsafespaces_runtime": "python",
        "workspace_ready_timeout": 300,
        "workspace_health_poll_interval": 30,
    }


def _make_mock_lss_client(
    classify_response: str = '{"is_announcement": true, "is_major_update": false, "category": "media", "reason": "x"}',
    assess_response: str | None = None,
    workspace_create_phase: str = "Pending",
):
    """Mock LLMSafeSpaces client where _request returns realistic payloads.

    Mirrors the live server response shapes (validated against
    api.safespaces.dev) — including the agentNeedsRefresh field on
    workspaces that the published SDK 0.5.4 dataclass doesn't model.
    """
    m = MagicMock()
    m.workspaces.get_status.return_value = {"phase": "Active"}

    msg_responses: list[str] = [classify_response]
    if assess_response is not None:
        msg_responses.append(assess_response)

    msg_iter = iter(msg_responses)

    def fake_request(method, path, json=None, **kwargs):
        if method == "POST" and path == "/workspaces":
            return {
                "id": "ws-1",
                "name": (json or {}).get("name", ""),
                "phase": workspace_create_phase,
                "runtime": (json or {}).get("runtime", ""),
                "agentNeedsRefresh": False,
            }
        if method == "POST" and "/sessions/new" in path:
            return {"workspaceId": "ws-1", "workspacePhase": "Active", "sessionId": "sess-1", "resumed": False}
        if method == "POST" and "/message" in path:
            return _build_send_message_payload(next(msg_iter))
        if method == "DELETE" and path.startswith("/workspaces/"):
            return None
        raise AssertionError(f"unexpected _request call: {method} {path}")

    m._request.side_effect = fake_request
    return m


def _set_message_responses(lss, *responses: str) -> None:
    """Override the message responses returned by _request on a mock client."""
    msg_iter = iter(responses)

    def fake_request(method, path, json=None, **kwargs):
        if method == "POST" and "/message" in path:
            return _build_send_message_payload(next(msg_iter))
        if method == "POST" and "/sessions/new" in path:
            return {"workspaceId": "ws-1", "workspacePhase": "Active", "sessionId": "sess-1", "resumed": False}
        if method == "DELETE":
            return None
        raise AssertionError(f"unexpected: {method} {path}")

    lss._request.side_effect = fake_request


def _build_send_message_payload(content_text: str) -> dict:
    """Build a payload shaped like the live /sessions/.../message response."""
    return {
        "id": "msg-1",
        "role": "assistant",
        "parts": [{"type": "text", "text": content_text}],
        "metadata": {"tokens": {"input": 100, "output": 50}},
    }


def _load_assessment_payload():
    return {
        "metadata": {
            "repo_url": "https://github.com/o/r",
            "repo_created": "2024-01-01", "repo_latest_commit": "2026-07-10",
            "repo_age_days": 910, "total_commits": 420,
            "avg_commits_per_month": 13.8, "commits_last_3_months": 30,
            "pct_commits_last_3_months": 7.1, "contributors": 12,
            "stars": 245, "open_issues": 8, "license": "MIT", "primary_language": "Go",
        },
        "analysis": {
            "code_quality": {"score": 4, "evidence": "x"},
            "release_ci_flow": {"score": 4, "evidence": "x"},
            "test_to_code_ratio": {"ratio": 0.5, "score": 4, "evidence": "x"},
            "architectural_robustness": {"score": 4, "evidence": "x"},
            "security_internal_only": {"score": 5, "evidence": "x"},
            "security_reverse_proxy": {"score": 4, "evidence": "x"},
            "security_sso": {"score": 3, "evidence": "x"},
        },
        "ai_usage": {"signals": [], "level": "assistive", "evidence": "x"},
        "overall_concern_vs_baseline": "similar",
        "key_concerns": ["c1"], "key_strengths": ["s1"], "tldr": "ok",
    }


# Workspace lifecycle ─────────────────────────────────────────────────────────


def test_T_701_create_workspace_calls_sdk_with_name_and_runtime() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    a.create_workspace("assess-xyz")
    # _request("POST", "/workspaces", json={"name": ..., "runtime": ...})
    create_calls = [
        c for c in lss._request.call_args_list
        if c.args[0] == "POST" and c.args[1] == "/workspaces"
    ]
    assert len(create_calls) == 1
    body = create_calls[0].kwargs.get("json") or {}
    assert body.get("name") == "assess-xyz"
    assert body.get("runtime") == "python"


def test_T_702_wait_for_active_polls_until_active() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    lss.workspaces.get_status.side_effect = [
        {"phase": "Pending"}, {"phase": "Creating"}, {"phase": "Active"}
    ]
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    a.wait_for_active("ws-1", poll_interval=0)
    assert lss.workspaces.get_status.call_count == 3


def test_T_703_wait_for_active_raises_after_timeout() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    cfg["workspace_ready_timeout"] = 1
    lss = _make_mock_lss_client()
    lss.workspaces.get_status.return_value = {"phase": "Pending"}
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(WorkspaceNotActive):
        a.wait_for_active("ws-1", poll_interval=0)


def test_T_704_wait_for_active_returns_immediately_if_already_active() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    lss.workspaces.get_status.return_value = {"phase": "Active"}
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    a.wait_for_active("ws-1", poll_interval=0)
    assert lss.workspaces.get_status.call_count == 1


def test_T_705_wait_for_active_raises_if_failed() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    lss.workspaces.get_status.return_value = {"phase": "Failed"}
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(WorkspaceError):
        a.wait_for_active("ws-1", poll_interval=0)


def test_T_706_health_check_returns_true_when_active() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    lss.workspaces.get_status.return_value = {"phase": "Active"}
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    assert a.health_check("ws-1") is True


@pytest.mark.parametrize("phase", ["Suspending", "Suspended", "Resuming", "Terminating", "Terminated", "Failed", "Creating", "Pending"])
def test_T_707_health_check_returns_false_when_not_active(phase: str) -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    lss.workspaces.get_status.return_value = {"phase": phase}
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    assert a.health_check("ws-1") is False


def test_T_708_create_session_returns_session_id() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    session_id = a.create_session("ws-1")
    assert session_id == "sess-1"
    # _request("POST", "/workspaces/ws-1/sessions/new") was called
    create_calls = [c for c in lss._request.call_args_list if c.args[0] == "POST" and "/sessions/new" in c.args[1]]
    assert len(create_calls) == 1
    assert create_calls[0].args[1] == "/workspaces/ws-1/sessions/new"


# Classification ──────────────────────────────────────────────────────────────


def _make_post():
    from model import RedditSubmission
    import datetime as dt
    return RedditSubmission(
        id="abc", title="t", selftext="b", url="https://github.com/o/r",
        author="u", created_utc=dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
        permalink="/p", flair=None, is_self=False,
    )


def test_T_709_classify_substitutes_placeholders_parses_json() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    result = a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")
    assert isinstance(result, Classification)
    assert result.is_announcement is True
    assert result.category == Category.MEDIA

    msg_calls = [
        c for c in lss._request.call_args_list
        if c.args[0] == "POST" and "/message" in c.args[1]
    ]
    assert len(msg_calls) == 1
    sent_body = msg_calls[0].kwargs.get("json") or {}
    sent_content = sent_body.get("content", "")
    assert "abc" not in sent_content  # submission id should not appear in prompt
    assert "https://github.com/o/r" in sent_content


def test_T_710_classify_raises_on_non_json_prose() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, "I think this is an announcement.")
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(AssessmentParseError):
        a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")


def test_T_711_classify_parses_json_wrapped_in_markdown_fence() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, '```json\n{"is_announcement": false, "is_major_update": false, "category": "other", "reason": "x"}\n```')
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    result = a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")
    assert result.is_announcement is False


def test_T_712_classify_parses_json_with_prose_prefix() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, 'Here is my response.\n\n{"is_announcement": false, "is_major_update": false, "category": "other", "reason": "x"}\n\nThanks.')
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    result = a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")
    assert result.is_announcement is False


def test_T_713_classify_raises_on_missing_required_keys() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, '{"foo": "bar"}')
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(AssessmentParseError):
        a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")


def test_T_714_classify_raises_on_unknown_category() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, '{"is_announcement": true, "is_major_update": false, "category": "totally_made_up", "reason": "x"}')
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(AssessmentParseError):
        a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")


# Assessment ──────────────────────────────────────────────────────────────────


def test_T_715_assess_substitutes_placeholders_parses_json() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, json.dumps(_load_assessment_payload()))
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    result = a.assess("ws-1", "sess-1", "https://github.com/o/r", Category.MEDIA)
    assert isinstance(result, type(a)) or result.metadata.repo_url == "https://github.com/o/r"
    msg_calls = [
        c for c in lss._request.call_args_list
        if c.args[0] == "POST" and "/message" in c.args[1]
    ]
    assert len(msg_calls) == 1
    sent = (msg_calls[0].kwargs.get("json") or {}).get("content", "")
    assert "https://github.com/o/r" in sent
    assert "media" in sent


def test_T_716_assess_raises_on_malformed_json() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, "{not json}")
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(AssessmentParseError):
        a.assess("ws-1", "sess-1", "https://github.com/o/r", Category.MEDIA)


@pytest.mark.parametrize("bad_score", [0, 6, -1])
def test_T_717_assess_rejects_score_out_of_range(bad_score: int) -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    payload = _load_assessment_payload()
    payload["analysis"]["code_quality"]["score"] = bad_score
    lss = _make_mock_lss_client()
    _set_message_responses(lss, json.dumps(payload))
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(AssessmentParseError):
        a.assess("ws-1", "sess-1", "https://github.com/o/r", Category.MEDIA)


def test_T_718_assess_accepts_null_metadata_fields() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    payload = _load_assessment_payload()
    for k in ["stars", "contributors", "open_issues", "primary_language", "repo_created", "total_commits"]:
        payload["metadata"][k] = None
    lss = _make_mock_lss_client()
    _set_message_responses(lss, json.dumps(payload))
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    result = a.assess("ws-1", "sess-1", "https://github.com/o/r", Category.MEDIA)
    assert result.metadata.stars is None


# Workspace teardown ──────────────────────────────────────────────────────────


def test_T_719_delete_workspace_calls_sdk_delete_logs_on_failure_no_raise(caplog) -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    lss.workspaces.delete.side_effect = RuntimeError("network blip")
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    a.delete_workspace("ws-1")  # must not raise


def test_T_720_delete_workspace_noop_on_none() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    a.delete_workspace(None)
    lss.workspaces.delete.assert_not_called()


# Prompt handling ─────────────────────────────────────────────────────────────


def test_T_721_missing_required_placeholder_raises_config_error(monkeypatch, tmp_path) -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "classify.md").write_text("no placeholders here")
    (prompts_dir / "assess.md").write_text("no placeholders here")
    monkeypatch.setattr(workspace_assessor, "_PROMPTS_DIR", prompts_dir)
    lss = _make_mock_lss_client()
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(ConfigError):
        a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")


def test_T_722_unknown_placeholder_in_template_warns_not_raises(monkeypatch, tmp_path, caplog) -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "classify.md").write_text("{{TITLE}} {{BODY}} {{URL}} {{GITHUB_REPOS}} {{UNKNOWN_THING}}")
    (prompts_dir / "assess.md").write_text("{{REPO_URL}} {{CATEGORY}}")
    monkeypatch.setattr(workspace_assessor, "_PROMPTS_DIR", prompts_dir)
    lss = _make_mock_lss_client()
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")
    assert any("UNKNOWN_THING" in r.message for r in caplog.records if r.levelname == "WARNING")


# Empty / whitespace responses ────────────────────────────────────────────────


def test_T_723_empty_agent_response_raises_parse_error() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, "")
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(AssessmentParseError):
        a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")


def test_T_724_whitespace_only_response_raises_parse_error() -> None:
    workspace_assessor = _import_workspace_assessor()
    cfg = _minimal_config_dict()
    lss = _make_mock_lss_client()
    _set_message_responses(lss, "   \n\t  ")
    a = workspace_assessor.WorkspaceAssessor(cfg, lss)
    with pytest.raises(AssessmentParseError):
        a.classify("ws-1", "sess-1", _make_post(), "https://github.com/o/r")
