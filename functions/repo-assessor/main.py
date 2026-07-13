"""Fission entry point for repo-assessor.

Polls r/selfhosted for posts with GitHub links, classifies whether the
post is announcing a project, assesses the repo in an ephemeral
LLMSafeSpaces workspace, and posts a structured comment under the
canonical sticky (or, in shadow mode, mirrors to a private target sub).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config as config_mod
import metrics as metrics_mod
from baselines import load_baselines, pick_baseline
from comment_formatter import format_comment
from config import Config
from github_urls import extract_first_github_url
from model import (
    Category,
    Decision,
    RedditAPIError,
    RedditRateLimit,
    RepoAssessorError,
    RunSummary,
    WorkspaceError,
    WorkspaceNotActive,
)
from reddit_client import RedditClient
from state import state_from_config
from workspace_assessor import WorkspaceAssessor


_LOG = logging.getLogger(__name__)
_BASELINES_PATH = Path(__file__).resolve().parent / "baselines.json"
_MAX_REDDIT_429_RETRIES = 3
_429_BACKOFF_BASE = 2.0
_REDDIT_NEW_FALLBACK_LIMIT = 25


@dataclass(frozen=True)
class _StickyHandle:
    id: str


def main() -> dict[str, Any]:
    """Fission entry point."""
    cfg = config_mod.load_config()
    _setup_logging(cfg)
    _LOG.info("repo-assessor starting; subreddit=%s shadow=%s", cfg.reddit_subreddit, cfg.shadow_mode)

    try:
        metrics = metrics_mod.Metrics({"metrics_port": cfg.metrics_port, "reddit_subreddit": cfg.reddit_subreddit})
    except Exception as e:
        _LOG.warning("metrics init failed (non-fatal): %s", e)
        metrics = None

    reddit = RedditClient(cfg.__dict__)
    state = state_from_config(cfg)

    from llmsafespaces import LLMSafeSpaces
    lss_client = LLMSafeSpaces(cfg.llmsafespaces_url, api_key=cfg.llmsafespaces_api_key)
    assessor = WorkspaceAssessor(cfg.__dict__, lss_client)

    try:
        summary = _run(cfg, reddit, lss_client, assessor, state, metrics)
        _LOG.info("run complete: %s", summary.to_dict() if hasattr(summary, "to_dict") else summary)
        if metrics:
            metrics.runs.labels(result="success").inc()
        return summary if isinstance(summary, dict) else summary.to_dict()
    except Exception as e:
        _LOG.error("run failed: %s", e)
        if metrics:
            metrics.runs.labels(result="error").inc()
        return {"error": str(e)}
    finally:
        try:
            state.close()
        except Exception:
            pass


def _run(
    cfg: Config,
    reddit: RedditClient,
    lss_client: Any,
    assessor: WorkspaceAssessor,
    state: Any,
    metrics: Any,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    started_at = dt.datetime.now(dt.timezone.utc)

    # Crash recovery: clean up stale workspaces from prior crashed runs.
    _cleanup_stale_in_flight(state, assessor)
    try:
        state.prune(dt.timedelta(hours=cfg.state_prune_hours))
    except Exception as e:
        _LOG.warning("state.prune failed (non-fatal): %s", e)

    # Fetch submissions with 429 backoff
    submissions = _get_new_with_backoff(reddit, cfg, metrics)
    if metrics:
        for s in submissions:
            pass  # no-op; metric recorded at fetch time

    baselines = load_baselines(str(_BASELINES_PATH))

    candidates, filter_breakdown = _filter_candidates(submissions, cfg, reddit, state, metrics)
    _LOG.info("polled=%d candidates=%d", len(submissions), len(candidates))

    posts_filtered_breakdown: dict[str, int] = dict(filter_breakdown)
    classified: dict[str, int] = {}
    posted = 0
    shadow_posted = 0
    errored = 0
    assessed_count = 0
    errors: list[str] = []

    if not candidates:
        ended_at = dt.datetime.now(dt.timezone.utc)
        return _summary(run_id, started_at, ended_at, None, len(submissions),
                        posts_filtered_breakdown, classified, 0, posted, shadow_posted, errored, errors)

    workspace_id: str | None = None
    workspace_created = False
    try:
        try:
            workspace = assessor.create_workspace(f"assess-{run_id}")
            workspace_id = getattr(workspace, "id", None)
            if not workspace_id:
                raise WorkspaceError(f"create_workspace returned no id: {workspace!r}")
            workspace_created = True
            if metrics:
                metrics.workspace_creations.labels(result="success").inc()
            assessor.wait_for_active(workspace_id)
        except (WorkspaceNotActive, Exception) as e:
            _LOG.error("workspace create/active failed: %s", e)
            if metrics:
                metrics.workspace_creations.labels(result="failure").inc()
            errors.append(f"workspace create failed: {e}")
            ended_at = dt.datetime.now(dt.timezone.utc)
            return _summary(run_id, started_at, ended_at, workspace_id, len(submissions),
                            posts_filtered_breakdown, classified, 0, posted, shadow_posted, errored, errors)

        active_started = dt.datetime.now(dt.timezone.utc)

        with ThreadPoolExecutor(max_workers=cfg.workspace_session_concurrency) as pool:
            futures = {
                pool.submit(_process_post, post, cfg, reddit, assessor, state, workspace_id, baselines, metrics): post
                for post in candidates
            }
            for future in as_completed(futures):
                post = futures[future]
                try:
                    outcome = future.result()
                except Exception as e:
                    outcome = ("error", str(e), None)
                    _LOG.exception("post %s errored: %s", post.id, e)

                kind = outcome[0]
                if kind == "filtered":
                    posts_filtered_breakdown[outcome[1]] = posts_filtered_breakdown.get(outcome[1], 0) + 1
                elif kind == "not_announcement":
                    classified["not_announcement"] = classified.get("not_announcement", 0) + 1
                elif kind == "announcement":
                    classified["announcement"] = classified.get("announcement", 0) + 1
                elif kind == "assessed":
                    assessed_count += 1
                elif kind == "posted":
                    posted += 1
                elif kind == "shadow_posted":
                    shadow_posted += 1
                elif kind == "error":
                    errored += 1
                    if len(errors) < 20:
                        errors.append(f"post {post.id}: {outcome[1]}")

        if metrics:
            metrics.workspace_active_seconds.observe(
                (dt.datetime.now(dt.timezone.utc) - active_started).total_seconds()
            )

    finally:
        if workspace_created and workspace_id:
            assessor.delete_workspace(workspace_id)

    ended_at = dt.datetime.now(dt.timezone.utc)
    return _summary(
        run_id, started_at, ended_at, workspace_id, len(submissions),
        posts_filtered_breakdown, classified, assessed_count, posted, shadow_posted, errored, errors,
    )


def _process_post(
    post: Any,
    cfg: Config,
    reddit: RedditClient,
    assessor: WorkspaceAssessor,
    state: Any,
    workspace_id: str,
    baselines: dict,
    metrics: Any,
) -> tuple[str, Any, Any]:
    """Process a single candidate post within a workspace. Returns outcome tuple."""
    github_url = extract_first_github_url(post.title, post.selftext, post.url)
    if not github_url:
        return ("filtered", "no_github_url", None)

    session_id = assessor.create_session(workspace_id)
    state.mark_in_flight(post.id, workspace_id, session_id)
    if metrics:
        metrics.in_flight_gauge.inc()

    try:
        classification = assessor.classify(workspace_id, session_id, post, github_url)
        if metrics:
            metrics.classification_decisions.labels(
                decision="announcement" if classification.is_announcement else "not_announcement"
            ).inc()

        if not classification.is_announcement:
            state.set_decision(post.id, Decision.NOT_ANNOUNCEMENT, classification.reason)
            return ("not_announcement", classification.reason, None)

        try:
            assessment = assessor.assess(workspace_id, session_id, github_url, classification.category)
        except Exception as e:
            state.set_decision(post.id, Decision.ERROR, f"assess failed: {e}")
            if metrics:
                metrics.posts.labels(outcome="error").inc()
            return ("error", f"assess failed: {e}", None)

        if metrics:
            _record_assessment_scores(metrics, assessment)

        baseline = pick_baseline(
            baselines, classification.category, default=cfg.baseline_default_category
        )
        comment_md = format_comment(assessment, baseline, cfg.__dict__)

        if cfg.shadow_mode:
            shadow_id = _ensure_shadow_post(reddit, state, post, cfg, github_url)
            if not shadow_id:
                state.set_decision(post.id, Decision.ERROR, "shadow submit failed")
                return ("error", "shadow submit failed", None)
            shadow_comments = reddit.get_comments(_strip_t3(shadow_id))
            sim_sticky = _find_or_create_simulated_sticky(reddit, shadow_id, shadow_comments, cfg)
            if not sim_sticky:
                state.set_decision(post.id, Decision.ERROR, "no simulated sticky")
                return ("error", "no simulated sticky", None)
            _post_reply(reddit, sim_sticky.id, comment_md, cfg)
            if cfg.dry_run:
                return ("filtered", "dry_run", None)
            state.set_decision(post.id, Decision.SHADOW_POSTED, f"shadow post {shadow_id}")
            if metrics:
                metrics.posts.labels(outcome="shadow_posted").inc()
            return ("shadow_posted", shadow_id, None)

        # Non-shadow: re-fetch comments and find sticky (filter already verified existence)
        comments = reddit.get_comments(post.id)
        sticky = reddit.find_canonical_sticky(comments, cfg.sticky_author, cfg.sticky_text_regex)
        if not sticky:
            return ("filtered", "no_sticky", None)
        if reddit.has_bot_reply(comments, cfg.reddit_username):
            state.set_decision(post.id, Decision.POSTED, "race guard")
            return ("posted", "race_guard", None)

        _post_reply(reddit, sticky.id, comment_md, cfg)
        if cfg.dry_run:
            _LOG.info("DRY_RUN: would have replied to %s", sticky.id)
            return ("filtered", "dry_run", None)
        state.set_decision(post.id, Decision.POSTED, "posted under sticky")
        if metrics:
            metrics.posts.labels(outcome="posted").inc()
        return ("posted", sticky.id, None)

    finally:
        state.clear_in_flight(post.id)
        if metrics:
            metrics.in_flight_gauge.dec()


def _filter_candidates(
    submissions: list,
    cfg: Config,
    reddit: RedditClient,
    state: Any,
    metrics: Any,
) -> tuple[list, dict[str, int]]:
    """Pre-filter submissions before workspace creation. Returns (post-pass list, breakdown).

    Cheap filters (no Reddit I/O) run first. Then for posts still in
    contention, fetch comments to check has_bot_reply and (for non-shadow)
    canonical-sticky presence. The Reddit I/O is only paid for posts that
    pass the cheap filters.
    """
    now = dt.datetime.now(dt.timezone.utc)
    candidates = []
    breakdown: dict[str, int] = {}
    for s in submissions:
        if cfg.source_flair_include and s.flair not in cfg.source_flair_include:
            breakdown["flair_include"] = breakdown.get("flair_include", 0) + 1
            continue
        if cfg.source_flair_exclude and s.flair in cfg.source_flair_exclude:
            breakdown["flair_exclude"] = breakdown.get("flair_exclude", 0) + 1
            continue
        if (now - s.created_utc).total_seconds() > cfg.max_post_age_hours * 3600:
            breakdown["age"] = breakdown.get("age", 0) + 1
            continue
        if not extract_first_github_url(s.title, s.selftext, s.url):
            breakdown["no_github"] = breakdown.get("no_github", 0) + 1
            continue
        decision = state.get_decision(s.id)
        if decision is not None:
            breakdown["already_decided"] = breakdown.get("already_decided", 0) + 1
            continue
        if state.get_in_flight(s.id):
            breakdown["in_flight"] = breakdown.get("in_flight", 0) + 1
            continue
        # Per-post Reddit I/O — only after cheap filters pass
        try:
            comments = reddit.get_comments(s.id)
        except RedditAPIError as e:
            _LOG.warning("get_comments failed for %s: %s", s.id, e)
            breakdown["comments_error"] = breakdown.get("comments_error", 0) + 1
            continue
        if reddit.has_bot_reply(comments, cfg.reddit_username):
            breakdown["already_replied"] = breakdown.get("already_replied", 0) + 1
            continue
        if not cfg.shadow_mode:
            sticky = reddit.find_canonical_sticky(comments, cfg.sticky_author, cfg.sticky_text_regex)
            if not sticky:
                breakdown["no_sticky"] = breakdown.get("no_sticky", 0) + 1
                continue
        candidates.append(s)
    return candidates, breakdown


def _get_new_with_backoff(reddit: RedditClient, cfg: Config, metrics: Any) -> list:
    last_err: Exception | None = None
    for attempt in range(cfg.reddit_api_max_retries):
        try:
            return reddit.get_new()
        except RedditRateLimit as e:
            last_err = e
            wait = _429_BACKOFF_BASE ** attempt
            _LOG.warning("reddit 429, backing off %.1fs (attempt %d/%d)", wait, attempt + 1, cfg.reddit_api_max_retries)
            import time
            time.sleep(wait)
        except RedditAPIError as e:
            _LOG.error("reddit API error on get_new: %s", e)
            raise
    raise RedditRateLimit(f"get_new exhausted {cfg.reddit_api_max_retries} retries: {last_err}")


def _post_reply(reddit: RedditClient, parent_id: str, markdown: str, cfg: Config) -> str:
    if cfg.dry_run:
        _LOG.info("DRY_RUN: would reply to %s with %d chars", parent_id, len(markdown))
        return "t1_dryrun"
    return reddit.reply_to_comment(parent_id, markdown)


def _ensure_shadow_post(reddit: RedditClient, state: Any, post: Any, cfg: Config, github_url: str) -> str | None:
    existing = state.get_shadow_mapping(post.id)
    if existing:
        return existing.shadow_submission_id
    title = _truncate_shadow_title(post.title)
    body = _build_shadow_body(post, github_url, cfg)
    try:
        shadow_id = reddit.submit_text_post(cfg.shadow_target_subreddit, title, body)
        state.set_shadow_mapping(post.id, shadow_id)
        return shadow_id
    except RedditAPIError as e:
        _LOG.error("shadow submit failed: %s", e)
        return None


def _find_or_create_simulated_sticky(reddit: RedditClient, shadow_id: str, comments: list, cfg: Config) -> Any:
    for c in comments:
        if getattr(c, "author", None) == cfg.reddit_username and "shadow" in (getattr(c, "body", "") or "").lower():
            return c
    if cfg.dry_run:
        _LOG.info("DRY_RUN: would create simulated sticky on %s", shadow_id)
        return _StickyHandle(id="t1_dryrun_sticky")
    try:
        sticky_id = reddit.reply_to_comment(shadow_id, _simulated_sticky_text())
        if cfg.shadow_distinguish_sticky:
            reddit.distinguish_comment(sticky_id)
        return _StickyHandle(id=sticky_id)
    except RedditAPIError as e:
        _LOG.error("failed to create simulated sticky: %s", e)
        return None


def _simulated_sticky_text() -> str:
    return (
        "Expand the replies to this comment to learn how AI was used in this post/project.\n\n"
        "*(Simulated sticky — shadow mode. The real r/selfhosted sticky is posted by u/asimovs-auditor.)*"
    )


def _truncate_shadow_title(title: str) -> str:
    prefix = "[shadow] "
    max_orig = 300 - len(prefix)
    if len(title) > max_orig:
        return f"{prefix}{title[: max_orig - 1]}…"
    return f"{prefix}{title}"


def _build_shadow_body(post: Any, github_url: str, cfg: Config) -> str:
    parts: list[str] = []
    if post.selftext:
        quoted = "\n".join(f"> {line}" for line in post.selftext.splitlines() or [""])
        parts.append(quoted)
        parts.append("")
    parts.append(f"**GitHub:** {github_url}")
    parts.append("")
    parts.append(f"**Original post:** [r/{cfg.reddit_subreddit}](https://www.reddit.com{post.permalink})")
    parts.append("")
    parts.append(
        "*Automated shadow-mode mirror for assessment testing. Not visible to the original poster. "
        "Reply below the simulated sticky with assessment feedback.*"
    )
    return "\n\n".join(parts)


def _strip_t3(submission_id: str) -> str:
    return submission_id[3:] if submission_id.startswith("t3_") else submission_id


def _cleanup_stale_in_flight(state: Any, assessor: WorkspaceAssessor) -> None:
    stale = state.list_stale_in_flight(dt.timedelta(hours=24))
    for entry in stale:
        try:
            assessor.delete_workspace(entry.workspace_id)
        except Exception:
            pass
        state.clear_in_flight(entry.submission_id)


def _record_assessment_scores(metrics: Any, assessment: Any) -> None:
    fields = {
        "code_quality": assessment.code_quality.score,
        "release_ci_flow": assessment.release_ci_flow.score,
        "test_to_code_ratio": assessment.test_to_code_ratio[1].score,
        "architectural_robustness": assessment.architectural_robustness.score,
        "security_internal_only": assessment.security.internal_only.score,
        "security_reverse_proxy": assessment.security.reverse_proxy.score,
        "security_sso": assessment.security.sso.score,
    }
    for dim, score in fields.items():
        metrics.assessment_scores.labels(dimension=dim).observe(score)


def _summary(
    run_id: str,
    started_at: dt.datetime,
    ended_at: dt.datetime,
    workspace_id: str | None,
    posts_polled: int,
    posts_filtered: dict[str, int],
    posts_classified: dict[str, int],
    posts_assessed: int,
    posts_posted: int,
    posts_shadow_posted: int,
    posts_errored: int,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "workspace_id": workspace_id,
        "posts_polled": posts_polled,
        "posts_filtered": posts_filtered,
        "posts_classified": posts_classified,
        "posts_assessed": posts_assessed,
        "posts_posted": posts_posted,
        "posts_shadow_posted": posts_shadow_posted,
        "posts_errored": posts_errored,
        "errors": errors[:20],
    }


def _setup_logging(cfg: Config) -> None:
    level = getattr(logging, cfg.log_level.upper(), logging.INFO)
    if cfg.log_json:
        import json as _json
        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return _json.dumps({
                    "ts": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                })
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        root.handlers = [handler]
        root.setLevel(level)
    else:
        logging.basicConfig(level=level, stream=sys.stdout,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2, default=str))
