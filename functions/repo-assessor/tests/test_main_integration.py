"""Integration tests for main.py — T-901 through T-943 (subset covering key paths)."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from model import (
    AIUsageAssessment,
    AIUsageLevel,
    Assessment,
    AssessmentParseError,
    Category,
    Classification,
    ConcernLevel,
    Decision,
    RepoMetadata,
    ScoreEvidence,
    SecurityScore,
)


# Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_state_path(tmp_path: Path) -> str:
    return str(tmp_path / "state.json")


@pytest.fixture
def fixed_now() -> dt.datetime:
    return dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def fresh_submission_data():
    return {
        "id": "fresh1",
        "title": "I built a thing",
        "selftext": "see https://github.com/example/project",
        "url": "https://github.com/example/project",
        "author": "op",
        "created_utc": time.time() - 3600,
        "permalink": "/r/selfhosted/comments/fresh1/i_built_a_thing/",
        "link_flair_text": None,
        "is_self": False,
    }


@pytest.fixture
def old_submission_data():
    return {
        "id": "old1",
        "title": "old post",
        "selftext": "https://github.com/example/old",
        "url": "https://github.com/example/old",
        "author": "op",
        "created_utc": time.time() - 86400 * 7,
        "permalink": "/r/selfhosted/comments/old1/old_post/",
        "link_flair_text": None,
        "is_self": False,
    }


def _set_required_env(monkeypatch: pytest.MonkeyPatch, tmp_state_path: str) -> None:
    for k, v in {
        "REDDIT_CLIENT_ID": "x", "REDDIT_CLIENT_SECRET": "x",
        "REDDIT_USERNAME": "bot_user", "REDDIT_PASSWORD": "x",
        "REDDIT_USER_AGENT": "test/0.1",
        "LLMSAFESPACES_URL": "https://lss.example.com",
        "LLMSAFESPACES_API_KEY": "lsp_xxx",
        "STATE_BACKEND": "json",
        "STATE_PATH": tmp_state_path,
        "WORKSPACE_READY_TIMEOUT": "10",
        "WORKSPACE_HEALTH_POLL_INTERVAL": "5",
        "METRICS_PORT": "9090",
    }.items():
        monkeypatch.setenv(k, v)


def _build_assessment() -> Assessment:
    return Assessment(
        metadata=RepoMetadata(
            repo_url="https://github.com/example/project",
            repo_created=None, repo_latest_commit=None,
            repo_age_days=10, total_commits=5,
            avg_commits_per_month=15.0, commits_last_3_months=4,
            pct_commits_last_3_months=80.0, contributors=1,
            stars=0, open_issues=0, license="MIT", primary_language="Go",
        ),
        code_quality=ScoreEvidence(score=3, evidence="e1"),
        release_ci_flow=ScoreEvidence(score=3, evidence="e2"),
        test_to_code_ratio=(0.1, ScoreEvidence(score=2, evidence="e3")),
        architectural_robustness=ScoreEvidence(score=3, evidence="e4"),
        security=SecurityScore(
            internal_only=ScoreEvidence(score=4, evidence="e5"),
            reverse_proxy=ScoreEvidence(score=3, evidence="e6"),
            sso=ScoreEvidence(score=2, evidence="e7"),
        ),
        ai_usage=AIUsageAssessment(signals=[], level=AIUsageLevel.UNREVIEWED, evidence="e8"),
        overall_concern_vs_baseline=ConcernLevel.ELEVATED,
        key_concerns=["x"], key_strengths=["y"], tldr="t",
    )


def _make_mocks(fresh_submission_data, classification=None, assessment=None):
    """Build a fully-mocked Reddit + LLMSafeSpaces + Metrics."""
    import reddit_client as rc_mod
    import workspace_assessor as wa_mod

    reddit = MagicMock(spec=rc_mod.RedditClient)
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = [_make_sticky()]
    reddit.has_bot_reply.return_value = False
    reddit.reply_to_comment.return_value = "t1_newreply"

    lss = MagicMock()
    lss.workspaces.create.return_value = MagicMock(id="ws-1", phase="Active")
    lss.workspaces.get_status.return_value = {"phase": "Active"}
    lss.workspaces.delete.return_value = None
    lss.sessions.ensure.return_value = MagicMock(sessionId="sess-1")

    if classification is None:
        classification = Classification(
            is_announcement=True, is_major_update=False,
            category=Category.MEDIA, reason="test announcement",
        )
    if assessment is None:
        assessment = _build_assessment()

    classify_response = MagicMock()
    classify_response.content = json.dumps({
        "is_announcement": classification.is_announcement,
        "is_major_update": classification.is_major_update,
        "category": classification.category.value,
        "reason": classification.reason,
    })
    assess_response = MagicMock()
    assess_response.content = json.dumps(_assessment_to_dict(assessment))

    lss.sessions.send_message.side_effect = [classify_response, assess_response]

    metrics = MagicMock()

    return reddit, lss, metrics, classification, assessment


def _make_sub(data: dict):
    from model import RedditSubmission
    return RedditSubmission(
        id=data["id"], title=data["title"], selftext=data["selftext"],
        url=data["url"], author=data["author"],
        created_utc=dt.datetime.fromtimestamp(data["created_utc"], tz=dt.timezone.utc),
        permalink=data["permalink"], flair=data["link_flair_text"],
        is_self=data["is_self"],
    )


def _make_sticky():
    from model import RedditComment
    return RedditComment(
        id="sticky1", author="asimovs-auditor",
        body="Expand replies to learn how AI was used in this post/project.",
        distinguished="moderator", stickied=True,
        created_utc=dt.datetime(2026, 7, 11, tzinfo=dt.timezone.utc),
    )


def _assessment_to_dict(a: Assessment) -> dict:
    return {
        "metadata": {
            "repo_url": a.metadata.repo_url,
            "repo_created": a.metadata.repo_created,
            "repo_latest_commit": a.metadata.repo_latest_commit,
            "repo_age_days": a.metadata.repo_age_days,
            "total_commits": a.metadata.total_commits,
            "avg_commits_per_month": a.metadata.avg_commits_per_month,
            "commits_last_3_months": a.metadata.commits_last_3_months,
            "pct_commits_last_3_months": a.metadata.pct_commits_last_3_months,
            "contributors": a.metadata.contributors,
            "stars": a.metadata.stars,
            "open_issues": a.metadata.open_issues,
            "license": a.metadata.license,
            "primary_language": a.metadata.primary_language,
        },
        "analysis": {
            "code_quality": {"score": a.code_quality.score, "evidence": a.code_quality.evidence},
            "release_ci_flow": {"score": a.release_ci_flow.score, "evidence": a.release_ci_flow.evidence},
            "test_to_code_ratio": {
                "ratio": a.test_to_code_ratio[0],
                "score": a.test_to_code_ratio[1].score,
                "evidence": a.test_to_code_ratio[1].evidence,
            },
            "architectural_robustness": {"score": a.architectural_robustness.score, "evidence": a.architectural_robustness.evidence},
            "security_internal_only": {"score": a.security.internal_only.score, "evidence": a.security.internal_only.evidence},
            "security_reverse_proxy": {"score": a.security.reverse_proxy.score, "evidence": a.security.reverse_proxy.evidence},
            "security_sso": {"score": a.security.sso.score, "evidence": a.security.sso.evidence},
        },
        "ai_usage": {
            "signals": a.ai_usage.signals,
            "level": a.ai_usage.level.value,
            "evidence": a.ai_usage.evidence,
        },
        "overall_concern_vs_baseline": a.overall_concern_vs_baseline.value,
        "key_concerns": a.key_concerns,
        "key_strengths": a.key_strengths,
        "tldr": a.tldr,
    }


# Tests ───────────────────────────────────────────────────────────────────────


def test_T_901_empty_poll_no_workspace_created(monkeypatch, tmp_state_path) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod
    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = []
    lss = MagicMock()
    metrics = MagicMock()
    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0

    summary = main_mod._run(cfg, reddit, lss, MagicMock(), state, metrics)
    assert summary["posts_polled"] == 0
    lss.workspaces.create.assert_not_called()


def test_T_903_all_filtered_by_age(monkeypatch, tmp_state_path, old_submission_data) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod
    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(old_submission_data)]
    lss = MagicMock()
    metrics = MagicMock()
    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0

    summary = main_mod._run(cfg, reddit, lss, MagicMock(), state, metrics)
    assert summary["posts_polled"] == 1
    assert summary["posts_filtered"].get("age", 0) == 1
    lss.workspaces.create.assert_not_called()


def test_T_909_classification_not_announcement_no_comment(
    monkeypatch, tmp_state_path, fresh_submission_data, fixed_now
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit, lss, metrics, _, _ = _make_mocks(
        fresh_submission_data,
        classification=Classification(
            is_announcement=False, is_major_update=False,
            category=Category.OTHER, reason="not an announcement",
        ),
    )
    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    assessor.classify.return_value = Classification(
        is_announcement=False, is_major_update=False,
        category=Category.OTHER, reason="not an announcement",
    )
    assessor.delete_workspace.return_value = None
    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    summary = main_mod._run(cfg, reddit, lss, assessor, state, metrics)
    assert summary["posts_classified"].get("not_announcement", 0) == 1
    assert summary["posts_posted"] == 0
    reddit.reply_to_comment.assert_not_called()
    state.set_decision.assert_called_with("fresh1", Decision.NOT_ANNOUNCEMENT, "not an announcement")
    assessor.assess.assert_not_called()


def test_T_910_full_happy_path(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = [_make_sticky()]
    reddit.has_bot_reply.return_value = False
    reddit.reply_to_comment.return_value = "t1_new"

    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    assessor.classify.return_value = Classification(
        is_announcement=True, is_major_update=False,
        category=Category.MEDIA, reason="announcing project",
    )
    assessor.assess.return_value = _build_assessment()
    assessor.delete_workspace.return_value = None

    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    summary = main_mod._run(cfg, reddit, MagicMock(), assessor, state, MagicMock())
    assert summary["posts_posted"] == 1
    reddit.reply_to_comment.assert_called_once()
    state.set_decision.assert_any_call("fresh1", Decision.POSTED, "posted under sticky")
    assessor.delete_workspace.assert_called_once_with("ws-1")


def test_T_911_dry_run_does_not_post(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    monkeypatch.setenv("DRY_RUN", "true")
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = [_make_sticky()]
    reddit.has_bot_reply.return_value = False

    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    assessor.classify.return_value = Classification(
        is_announcement=True, is_major_update=False,
        category=Category.MEDIA, reason="x",
    )
    assessor.assess.return_value = _build_assessment()
    assessor.delete_workspace.return_value = None

    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    summary = main_mod._run(cfg, reddit, MagicMock(), assessor, state, MagicMock())
    assert summary["posts_posted"] == 0
    reddit.reply_to_comment.assert_not_called()


def test_T_908_sticky_not_found_skips_without_marking(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = []  # no sticky
    reddit.has_bot_reply.return_value = False
    reddit.find_canonical_sticky.return_value = None  # sticky not found

    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    assessor.classify.return_value = Classification(
        is_announcement=True, is_major_update=False,
        category=Category.MEDIA, reason="x",
    )
    assessor.assess.return_value = _build_assessment()
    assessor.delete_workspace.return_value = None

    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    summary = main_mod._run(cfg, reddit, MagicMock(), assessor, state, MagicMock())
    assert summary["posts_posted"] == 0
    reddit.reply_to_comment.assert_not_called()
    # No decision recorded; will retry next poll
    posted_calls = [c for c in state.set_decision.call_args_list if c.args[1] == Decision.POSTED]
    assert posted_calls == []


def test_T_925_workspace_always_deleted_on_success(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = [_make_sticky()]
    reddit.has_bot_reply.return_value = False

    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    assessor.classify.return_value = Classification(
        is_announcement=True, is_major_update=False,
        category=Category.MEDIA, reason="x",
    )
    assessor.assess.return_value = _build_assessment()
    assessor.delete_workspace.return_value = None

    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    main_mod._run(cfg, reddit, MagicMock(), assessor, state, MagicMock())
    assessor.delete_workspace.assert_called_once_with("ws-1")


def test_T_925_workspace_deleted_on_error_path(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = [_make_sticky()]
    reddit.has_bot_reply.return_value = False

    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    assessor.classify.side_effect = AssessmentParseError("bad json")
    assessor.delete_workspace.return_value = None

    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    summary = main_mod._run(cfg, reddit, MagicMock(), assessor, state, MagicMock())
    assert summary["posts_errored"] >= 1
    assessor.delete_workspace.assert_called_once_with("ws-1")


def test_T_905_already_decided_skipped_without_workspace(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    lss = MagicMock()
    assessor = MagicMock()
    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = MagicMock(decision=Decision.POSTED)

    summary = main_mod._run(cfg, reddit, lss, assessor, state, MagicMock())
    assert summary["posts_filtered"].get("already_decided", 0) == 1
    lss.workspaces.create.assert_not_called()
    assessor.create_workspace.assert_not_called()


def test_T_907_already_replied_skipped(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = [_make_sticky()]
    reddit.has_bot_reply.return_value = True
    lss = MagicMock()
    assessor = MagicMock()
    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    summary = main_mod._run(cfg, reddit, lss, assessor, state, MagicMock())
    assert summary["posts_filtered"].get("already_replied", 0) == 1
    assessor.create_workspace.assert_not_called()


def test_T_912_shadow_mode_full_path(
    monkeypatch, tmp_state_path, fresh_submission_data
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    monkeypatch.setenv("SHADOW_MODE", "true")
    monkeypatch.setenv("SHADOW_TARGET_SUBREDDIT", "shadowtest")
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    reddit.get_new.return_value = [_make_sub(fresh_submission_data)]
    reddit.get_comments.return_value = []
    reddit.has_bot_reply.return_value = False
    reddit.submit_text_post.return_value = "t3_shadow1"
    reddit.reply_to_comment.return_value = "t1_simsticky"

    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    assessor.classify.return_value = Classification(
        is_announcement=True, is_major_update=False,
        category=Category.MEDIA, reason="x",
    )
    assessor.assess.return_value = _build_assessment()
    assessor.delete_workspace.return_value = None

    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None
    state.get_shadow_mapping.return_value = None

    summary = main_mod._run(cfg, reddit, MagicMock(), assessor, state, MagicMock())
    assert summary["posts_shadow_posted"] == 1
    assert summary["posts_posted"] == 0
    reddit.submit_text_post.assert_called_once()
    state.set_decision.assert_any_call("fresh1", Decision.SHADOW_POSTED, "shadow post t3_shadow1")
    state.set_shadow_mapping.assert_called_with("fresh1", "t3_shadow1")


def test_T_929_run_summary_errors_capped_at_20(
    monkeypatch, tmp_state_path
) -> None:
    _set_required_env(monkeypatch, tmp_state_path)
    import config as cfg_mod
    import main as main_mod

    cfg = cfg_mod.load_config()
    reddit = MagicMock()
    submissions = []
    for i in range(50):
        submissions.append(_make_sub({
            "id": f"sub{i}", "title": "t", "selftext": "https://github.com/o/r",
            "url": "https://github.com/o/r", "author": "x",
            "created_utc": time.time() - 3600, "permalink": f"/p{i}",
            "link_flair_text": None, "is_self": False,
        }))
    reddit.get_new.return_value = submissions
    reddit.get_comments.return_value = [_make_sticky()]
    reddit.has_bot_reply.return_value = False

    assessor = MagicMock()
    assessor.create_workspace.return_value = MagicMock(id="ws-1")
    assessor.wait_for_active.return_value = MagicMock()
    assessor.create_session.return_value = "sess-1"
    # Every classify fails
    assessor.classify.side_effect = AssessmentParseError("boom")
    assessor.delete_workspace.return_value = None

    state = MagicMock()
    state.list_stale_in_flight.return_value = []
    state.prune.return_value = 0
    state.get_decision.return_value = None
    state.get_in_flight.return_value = None

    summary = main_mod._run(cfg, reddit, MagicMock(), assessor, state, MagicMock())
    assert len(summary["errors"]) <= 20
