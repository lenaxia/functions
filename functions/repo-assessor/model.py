"""Typed domain models for repo-assessor.

Frozen dataclasses throughout — values are constructed once, never mutated.
Score-bearing types enforce 1..5 invariants at construction time so callers
cannot represent an invalid assessment.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


MAX_RUN_SUMMARY_ERRORS = 20
MIN_SCORE = 1
MAX_SCORE = 5


class Decision(StrEnum):
    ANNOUNCEMENT = "announcement"
    NOT_ANNOUNCEMENT = "not_announcement"
    ERROR = "error"
    POSTED = "posted"
    SHADOW_POSTED = "shadow_posted"


class Category(StrEnum):
    MEDIA = "media"
    SECURITY = "security"
    DASHBOARD = "dashboard"
    NETWORKING = "networking"
    MONITORING = "monitoring"
    OTHER = "other"


class AIUsageLevel(StrEnum):
    NONE = "none"
    ASSISTIVE = "assistive"
    SUBSTANTIAL_REVIEWED = "substantial_reviewed"
    UNREVIEWED = "unreviewed"
    RECKLESS = "reckless"


class ConcernLevel(StrEnum):
    LOWER = "lower"
    SIMILAR = "similar"
    ELEVATED = "elevated"
    HIGH = "high"


@dataclass(frozen=True)
class InFlight:
    submission_id: str
    workspace_id: str
    session_id: str
    started_at: dt.datetime


@dataclass(frozen=True)
class DecisionRecord:
    submission_id: str
    decision: Decision
    reason: str
    at: dt.datetime


@dataclass(frozen=True)
class ShadowMapping:
    source_submission_id: str
    shadow_submission_id: str
    at: dt.datetime


@dataclass(frozen=True)
class ScoreEvidence:
    score: int
    evidence: str

    def __post_init__(self) -> None:
        if not isinstance(self.score, int) or isinstance(self.score, bool):
            raise ValueError(f"score must be int, got {type(self.score).__name__}")
        if self.score < MIN_SCORE or self.score > MAX_SCORE:
            raise ValueError(f"score must be {MIN_SCORE}..{MAX_SCORE}, got {self.score}")
        if not self.evidence or not self.evidence.strip():
            raise ValueError("evidence must be non-empty")


@dataclass(frozen=True)
class SecurityScore:
    internal_only: ScoreEvidence
    reverse_proxy: ScoreEvidence
    sso: ScoreEvidence


@dataclass(frozen=True)
class RepoMetadata:
    repo_url: str
    repo_created: dt.date | None
    repo_latest_commit: dt.date | None
    repo_age_days: int | None
    total_commits: int | None
    avg_commits_per_month: float | None
    commits_last_3_months: int | None
    pct_commits_last_3_months: float | None
    contributors: int | None
    stars: int | None
    open_issues: int | None
    license: str
    primary_language: str | None


@dataclass(frozen=True)
class AIUsageAssessment:
    signals: list[str]
    level: AIUsageLevel
    evidence: str


@dataclass(frozen=True)
class Assessment:
    metadata: RepoMetadata
    code_quality: ScoreEvidence
    release_ci_flow: ScoreEvidence
    test_to_code_ratio: tuple[float, ScoreEvidence]
    architectural_robustness: ScoreEvidence
    security: SecurityScore
    ai_usage: AIUsageAssessment
    overall_concern_vs_baseline: ConcernLevel
    key_concerns: list[str]
    key_strengths: list[str]
    tldr: str


@dataclass(frozen=True)
class BaselineProfile:
    name: str
    repo: str
    url: str
    category_description: str
    repo_age_days: int
    avg_commits_per_month: float
    pct_commits_last_3_months: float
    contributors: int
    stars: int
    license: str
    scores: dict[str, int]
    security: dict[str, int]
    notes: str


@dataclass(frozen=True)
class Classification:
    is_announcement: bool
    is_major_update: bool
    category: Category
    reason: str


@dataclass(frozen=True)
class RedditSubmission:
    id: str
    title: str
    selftext: str
    url: str
    author: str | None
    created_utc: dt.datetime
    permalink: str
    flair: str | None
    is_self: bool


@dataclass(frozen=True)
class RedditComment:
    id: str
    author: str | None
    body: str
    distinguished: str | None
    stickied: bool
    created_utc: dt.datetime


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    started_at: dt.datetime
    ended_at: dt.datetime
    workspace_id: str | None
    posts_polled: int
    posts_filtered: dict[str, int]
    posts_classified: dict[str, int]
    posts_assessed: int
    posts_posted: int
    posts_shadow_posted: int
    posts_errored: int
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.errors) > MAX_RUN_SUMMARY_ERRORS:
            object.__setattr__(self, "errors", list(self.errors[:MAX_RUN_SUMMARY_ERRORS]))


class StateStore(Protocol):
    def mark_in_flight(self, submission_id: str, workspace_id: str, session_id: str) -> None: ...
    def clear_in_flight(self, submission_id: str) -> None: ...
    def get_in_flight(self, submission_id: str) -> InFlight | None: ...
    def list_stale_in_flight(self, older_than: dt.timedelta) -> list[InFlight]: ...
    def set_decision(self, submission_id: str, decision: Decision, reason: str) -> None: ...
    def get_decision(self, submission_id: str) -> DecisionRecord | None: ...
    def set_shadow_mapping(self, source_id: str, shadow_id: str) -> None: ...
    def get_shadow_mapping(self, source_id: str) -> ShadowMapping | None: ...
    def prune(self, older_than: dt.timedelta) -> int: ...
    def close(self) -> None: ...


class RepoAssessorError(Exception):
    pass


class ConfigError(RepoAssessorError):
    pass


class StateError(RepoAssessorError):
    pass


class RedditAPIError(RepoAssessorError):
    pass


class RedditRateLimit(RedditAPIError):
    pass


class WorkspaceError(RepoAssessorError):
    pass


class WorkspaceNotActive(WorkspaceError):
    pass


class AssessmentParseError(RepoAssessorError):
    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw
