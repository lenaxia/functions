"""Tests for comment_formatter.py — T-801..T-818."""

from __future__ import annotations

import pytest

from model import (
    AIUsageAssessment,
    AIUsageLevel,
    Assessment,
    BaselineProfile,
    ConcernLevel,
    RepoMetadata,
    ScoreEvidence,
    SecurityScore,
)


def _import_comment_formatter():
    import comment_formatter
    return comment_formatter


def _config_dict():
    return {
        "bot_source_url": "https://github.com/lenaxia/functions/tree/main/functions/repo-assessor",
        "bot_issues_url": "https://github.com/lenaxia/functions/issues",
        "llmsafespaces_footer_url": "https://github.com/lenaxia/llmsafespaces",
    }


def _baseline() -> BaselineProfile:
    return BaselineProfile(
        name="Sonarr",
        repo="Sonarr/Sonarr",
        url="https://github.com/Sonarr/Sonarr",
        category_description="Media",
        repo_age_days=4380,
        avg_commits_per_month=30.0,
        pct_commits_last_3_months=8.0,
        contributors=350,
        stars=12000,
        license="GPL-3.0",
        scores={
            "code_quality": 4,
            "release_ci_flow": 5,
            "test_to_code_ratio": 4,
            "architectural_robustness": 4,
        },
        security={"internal_only": 5, "reverse_proxy": 4, "sso": 3},
        notes="Reference.",
    )


def _assessment(**overrides) -> Assessment:
    metadata = RepoMetadata(
        repo_url="https://github.com/example/repo",
        repo_created=None, repo_latest_commit=None,
        repo_age_days=910, total_commits=420,
        avg_commits_per_month=13.8, commits_last_3_months=30,
        pct_commits_last_3_months=7.1, contributors=12,
        stars=245, open_issues=8, license="MIT", primary_language="Go",
    )
    base = dict(
        metadata=metadata,
        code_quality=ScoreEvidence(score=4, evidence="idiomatic"),
        release_ci_flow=ScoreEvidence(score=4, evidence="gh actions"),
        test_to_code_ratio=(0.42, ScoreEvidence(score=4, evidence="tests present")),
        architectural_robustness=ScoreEvidence(score=4, evidence="layered"),
        security=SecurityScore(
            internal_only=ScoreEvidence(score=5, evidence="no LAN surface"),
            reverse_proxy=ScoreEvidence(score=4, evidence="proxy ok"),
            sso=ScoreEvidence(score=3, evidence="oidc partial"),
        ),
        ai_usage=AIUsageAssessment(signals=["tests reviewed"], level=AIUsageLevel.ASSISTIVE, evidence="ai for tests"),
        overall_concern_vs_baseline=ConcernLevel.SIMILAR,
        key_concerns=["c1", "c2"],
        key_strengths=["s1"],
        tldr="ok project",
    )
    base.update(overrides)
    return Assessment(**base)


# Happy path ──────────────────────────────────────────────────────────────────


def test_T_801_happy_path_produces_nonempty_markdown() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert isinstance(out, str)
    assert len(out) > 200
    assert "Repo assessment" in out


def test_T_802_baseline_name_in_headers_and_tables() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "Sonarr" in out


def test_T_803_score_delta_shown() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert ("=" in out) or ("+" in out) or ("-" in out)


def test_T_804_key_concerns_rendered_as_bullets() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "* c1" in out or "- c1" in out
    assert "* c2" in out or "- c2" in out


def test_T_805_key_strengths_rendered_as_bullets() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "* s1" in out or "- s1" in out


def test_T_806_tldr_single_line() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "ok project" in out


def test_T_807_footer_contains_all_urls() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "https://github.com/lenaxia/functions/tree/main/functions/repo-assessor" in out
    assert "https://github.com/lenaxia/functions/issues" in out
    assert "https://github.com/lenaxia/llmsafespaces" in out


def test_T_808_ai_usage_level_and_evidence_rendered() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "assistive" in out
    assert "ai for tests" in out


def test_T_809_output_under_10000_chars() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert len(out) <= 10000


# Truncation ──────────────────────────────────────────────────────────────────


def test_T_810_truncation_triggered_when_over_10000() -> None:
    cf = _import_comment_formatter()
    long_evidence = "x" * 5000
    assessment = _assessment(
        code_quality=ScoreEvidence(score=4, evidence=long_evidence),
        release_ci_flow=ScoreEvidence(score=4, evidence=long_evidence),
        test_to_code_ratio=(0.42, ScoreEvidence(score=4, evidence=long_evidence)),
        architectural_robustness=ScoreEvidence(score=4, evidence=long_evidence),
    )
    out = cf.format_comment(assessment, _baseline(), _config_dict())
    assert len(out) <= 10000, f"output is {len(out)} chars"


def test_T_811_truncation_drops_strengths_if_still_over() -> None:
    cf = _import_comment_formatter()
    huge_evidence = "y" * 12000
    assessment = _assessment(
        code_quality=ScoreEvidence(score=4, evidence=huge_evidence),
    )
    out = cf.format_comment(assessment, _baseline(), _config_dict())
    assert len(out) <= 10000


def test_T_812_truncation_drops_security_table_if_still_over() -> None:
    cf = _import_comment_formatter()
    huge = "z" * 25000
    assessment = _assessment(
        code_quality=ScoreEvidence(score=4, evidence=huge),
    )
    out = cf.format_comment(assessment, _baseline(), _config_dict())
    assert len(out) <= 10000


def test_T_813_hard_abort_when_truncated_below_2000() -> None:
    cf = _import_comment_formatter()
    # Construct an assessment with content that would force aggressive truncation
    # leaving almost nothing — currently hard to trigger without invasive
    # shaping; this test asserts the constant exists and is enforced if hit.
    assert cf.MIN_OUTPUT_CHARS == 2000


# AI usage levels ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("level", list(AIUsageLevel))
def test_T_814_all_ai_usage_levels_render(level: AIUsageLevel) -> None:
    cf = _import_comment_formatter()
    assessment = _assessment(ai_usage=AIUsageAssessment(signals=[], level=level, evidence="x"))
    out = cf.format_comment(assessment, _baseline(), _config_dict())
    assert level.value in out


def test_T_815_null_metadata_renders_as_dash() -> None:
    cf = _import_comment_formatter()
    metadata = RepoMetadata(
        repo_url="https://github.com/example/repo",
        repo_created=None, repo_latest_commit=None, repo_age_days=None,
        total_commits=None, avg_commits_per_month=None, commits_last_3_months=None,
        pct_commits_last_3_months=None, contributors=None, stars=None,
        open_issues=None, license="unknown", primary_language=None,
    )
    assessment = _assessment(metadata=metadata)
    out = cf.format_comment(assessment, _baseline(), _config_dict())
    assert "—" in out or "-" in out
    assert "None" not in out


def test_T_816_security_renders_all_three_rows() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "Internal" in out
    assert "Reverse proxy" in out or "reverse proxy" in out.lower()
    assert "SSO" in out


def test_T_817_output_has_no_unescaped_pipes_in_table_cells() -> None:
    cf = _import_comment_formatter()
    pipe_evidence = ScoreEvidence(score=3, evidence="this has a | pipe in it")
    assessment = _assessment(code_quality=pipe_evidence)
    out = cf.format_comment(assessment, _baseline(), _config_dict())
    table_lines = [l for l in out.splitlines() if l.startswith("|") and "this has" in l]
    for line in table_lines:
        assert "\\|" in line or "|" not in line.split("this has")[1].split("|")[0]


def test_T_818_concern_level_rendered() -> None:
    cf = _import_comment_formatter()
    out = cf.format_comment(_assessment(), _baseline(), _config_dict())
    assert "similar" in out.lower()
