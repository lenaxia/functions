"""Shared test fixtures for the repo-assessor test suite."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

import pytest

_FUNCTION_DIR = Path(__file__).resolve().parent.parent
if str(_FUNCTION_DIR) not in sys.path:
    sys.path.insert(0, str(_FUNCTION_DIR))


RELEVANT_ENV_VARS = (
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
    "REDDIT_USER_AGENT",
    "LLMSAFESPACES_URL",
    "LLMSAFESPACES_API_KEY",
    "STATE_BACKEND",
    "STATE_PATH",
    "STATE_DATABASE_URL",
    "SHADOW_MODE",
    "SHADOW_TARGET_SUBREDDIT",
)


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in RELEVANT_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fixed_now() -> dt.datetime:
    return dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def sample_repo_metadata() -> dict[str, Any]:
    return {
        "repo_url": "https://github.com/example/project",
        "repo_created": "2024-01-15",
        "repo_latest_commit": "2026-07-10",
        "repo_age_days": 910,
        "total_commits": 420,
        "avg_commits_per_month": 13.8,
        "commits_last_3_months": 30,
        "pct_commits_last_3_months": 7.1,
        "contributors": 12,
        "stars": 245,
        "open_issues": 8,
        "license": "MIT",
        "primary_language": "Go",
    }


@pytest.fixture
def sample_assessment(sample_repo_metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": sample_repo_metadata,
        "analysis": {
            "code_quality": {"score": 4, "evidence": "idiomatic Go, consistent structure"},
            "release_ci_flow": {"score": 4, "evidence": "GitHub Actions on PRs and main"},
            "test_to_code_ratio": {"ratio": 0.42, "score": 4, "evidence": "_test.go files present"},
            "architectural_robustness": {"score": 4, "evidence": "clear layering"},
            "security_internal_only": {"score": 5, "evidence": "no auth surface on LAN"},
            "security_reverse_proxy": {"score": 4, "evidence": "proxy headers respected"},
            "security_sso": {"score": 3, "evidence": "OIDC supported but untested"},
        },
        "ai_usage": {
            "signals": ["tests reviewed"],
            "level": "assistive",
            "evidence": "AI used for tests, logic human-written",
        },
        "overall_concern_vs_baseline": "similar",
        "key_concerns": ["SSO path untested", "no SBOM"],
        "key_strengths": ["active commits", "clean architecture"],
        "tldr": "Solid mid-maturity project, comparable to baseline.",
    }


@pytest.fixture
def sample_classification_announcement() -> dict[str, Any]:
    return {
        "is_announcement": True,
        "is_major_update": False,
        "category": "media",
        "reason": "OP announces a new media server with github link",
    }


@pytest.fixture
def sample_classification_not_announcement() -> dict[str, Any]:
    return {
        "is_announcement": False,
        "is_major_update": False,
        "category": "other",
        "reason": "Question post, github link is reference not subject",
    }


@pytest.fixture
def sample_baseline_sonarr() -> dict[str, Any]:
    return {
        "name": "Sonarr",
        "repo": "Sonarr/Sonarr",
        "url": "https://github.com/Sonarr/Sonarr",
        "category_description": "Self-hosted media management",
        "profile": {
            "repo_age_days": 4380,
            "avg_commits_per_month": 30,
            "pct_commits_last_3_months": 8,
            "contributors": 350,
            "stars": 12000,
            "license": "GPL-3.0",
            "scores": {
                "code_quality": 4,
                "release_ci_flow": 5,
                "test_to_code_ratio": 4,
                "architectural_robustness": 4,
            },
            "security": {"internal_only": 5, "reverse_proxy": 4, "sso": 3},
        },
        "notes": "Mature .NET Smart PVR.",
    }


@pytest.fixture
def sample_submission_link() -> dict[str, Any]:
    return {
        "id": "abc123",
        "title": "I built a thing",
        "selftext": "",
        "url": "https://github.com/example/project",
        "author": "someuser",
        "created_utc": 1783900000.0,
        "permalink": "/r/selfhosted/comments/abc123/i_built_a_thing/",
        "flair": None,
        "is_self": False,
    }


@pytest.fixture
def sample_submission_text() -> dict[str, Any]:
    return {
        "id": "def456",
        "title": "Show: my homelab dashboard",
        "selftext": "Source at https://github.com/example/dash",
        "url": "",
        "author": "anotheruser",
        "created_utc": 1783900600.0,
        "permalink": "/r/selfhosted/comments/def456/show_my_homelab_dashboard/",
        "flair": "Show-off",
        "is_self": True,
    }
