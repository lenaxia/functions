"""Tests for types.py — T-001 through T-007."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest


def _import_types():
    import model
    return model


def test_T_001_decision_enum_has_expected_values() -> None:
    t = _import_types()
    expected = {"announcement", "not_announcement", "error", "posted", "shadow_posted"}
    actual = {d.value for d in t.Decision}
    assert actual == expected


def test_T_001_category_enum_has_expected_values() -> None:
    t = _import_types()
    expected = {"media", "security", "dashboard", "networking", "monitoring", "other"}
    actual = {c.value for c in t.Category}
    assert actual == expected


def test_T_001_concern_level_enum_values() -> None:
    t = _import_types()
    expected = {"lower", "similar", "elevated", "high"}
    actual = {c.value for c in t.ConcernLevel}
    assert actual == expected


def test_T_001_ai_usage_level_enum_values() -> None:
    t = _import_types()
    expected = {"none", "assistive", "substantial_reviewed", "unreviewed", "reckless"}
    actual = {a.value for a in t.AIUsageLevel}
    assert actual == expected


def test_T_001_decision_serialises_as_plain_string() -> None:
    t = _import_types()
    assert json.dumps(t.Decision.POSTED) == '"posted"'


def test_T_003_score_evidence_rejects_out_of_range_score() -> None:
    t = _import_types()
    with pytest.raises(ValueError):
        t.ScoreEvidence(score=0, evidence="x")
    with pytest.raises(ValueError):
        t.ScoreEvidence(score=6, evidence="x")


def test_T_004_score_evidence_rejects_empty_evidence() -> None:
    t = _import_types()
    with pytest.raises(ValueError):
        t.ScoreEvidence(score=3, evidence="")
    with pytest.raises(ValueError):
        t.ScoreEvidence(score=3, evidence="   ")


def test_T_003_assessment_rejects_score_out_of_range_via_score_evidence() -> None:
    t = _import_types()
    metadata = t.RepoMetadata(
        repo_url="x", repo_created=None, repo_latest_commit=None, repo_age_days=None,
        total_commits=None, avg_commits_per_month=None, commits_last_3_months=None,
        pct_commits_last_3_months=None, contributors=None, stars=None, open_issues=None,
        license="MIT", primary_language=None,
    )
    ai = t.AIUsageAssessment(signals=[], level=t.AIUsageLevel.NONE, evidence="n/a")
    with pytest.raises(ValueError):
        t.Assessment(
            metadata=metadata,
            code_quality=t.ScoreEvidence(score=7, evidence="bad"),
            release_ci_flow=t.ScoreEvidence(score=3, evidence="ok"),
            test_to_code_ratio=(0.5, t.ScoreEvidence(score=3, evidence="ok")),
            architectural_robustness=t.ScoreEvidence(score=3, evidence="ok"),
            security=t.SecurityScore(
                internal_only=t.ScoreEvidence(score=3, evidence="ok"),
                reverse_proxy=t.ScoreEvidence(score=3, evidence="ok"),
                sso=t.ScoreEvidence(score=3, evidence="ok"),
            ),
            ai_usage=ai,
            overall_concern_vs_baseline=t.ConcernLevel.SIMILAR,
            key_concerns=[], key_strengths=[], tldr="x",
        )


def test_T_005_classification_accepts_not_announcement_with_any_category() -> None:
    t = _import_types()
    for cat in t.Category:
        c = t.Classification(
            is_announcement=False, is_major_update=False,
            category=cat, reason="irrelevant",
        )
        assert c.category == cat


def test_T_006_dataclasses_hashable() -> None:
    t = _import_types()
    hashable_instances = [
        t.InFlight("s1", "w1", "sess1", dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)),
        t.DecisionRecord("s1", t.Decision.POSTED, "ok", dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)),
        t.ShadowMapping("s1", "s2", dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)),
        t.ScoreEvidence(score=3, evidence="ok"),
        t.Classification(False, False, t.Category.OTHER, "x"),
        t.ConcernLevel.SIMILAR,
        t.Category.MEDIA,
    ]
    for inst in hashable_instances:
        hash(inst)

    with pytest.raises(TypeError):
        hash(t.AIUsageAssessment(signals=[], level=t.AIUsageLevel.NONE, evidence="n/a"))


def test_T_007_run_summary_errors_capped_at_20() -> None:
    t = _import_types()
    summary = t.RunSummary(
        run_id="r1",
        started_at=dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc),
        ended_at=dt.datetime(2026, 7, 13, 12, 5, 0, tzinfo=dt.timezone.utc),
        workspace_id="w1",
        posts_polled=10,
        posts_filtered={},
        posts_classified={},
        posts_assessed=0,
        posts_posted=0,
        posts_shadow_posted=0,
        posts_errored=0,
        errors=[f"err {i}" for i in range(50)],
    )
    assert len(summary.errors) == 20


def test_in_flight_requires_iso_timestamps() -> None:
    t = _import_types()
    rec = t.InFlight("s1", "w1", "sess1", dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc))
    assert rec.started_at.tzinfo is not None


def test_repo_metadata_allows_null_fields() -> None:
    t = _import_types()
    m = t.RepoMetadata(
        repo_url="x",
        repo_created=None, repo_latest_commit=None, repo_age_days=None,
        total_commits=None, avg_commits_per_month=None, commits_last_3_months=None,
        pct_commits_last_3_months=None, contributors=None, stars=None, open_issues=None,
        license="unknown", primary_language=None,
    )
    assert m.stars is None
    assert m.license == "unknown"
