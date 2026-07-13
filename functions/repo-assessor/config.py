"""Environment-driven configuration for repo-assessor.

Single source of truth for runtime config. All values come from env vars
(Fission mounts secrets as env vars by convention). Validates required
vars and value ranges at load time; raises ConfigError naming the offending
var on any failure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from model import Category, ConfigError


_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off", ""})
_VALID_STATE_BACKENDS = frozenset({"json", "sqlite", "postgres"})
_DEFAULT_STATE_PATH = {
    "json": "/state/repo-assessor.json",
    "sqlite": "/state/repo-assessor.db",
    "postgres": "",
}


@dataclass(frozen=True)
class Config:
    reddit_client_id: str
    reddit_client_secret: str
    reddit_username: str
    reddit_password: str
    reddit_user_agent: str
    reddit_subreddit: str
    reddit_new_limit: int
    reddit_api_max_retries: int
    llmsafespaces_url: str
    llmsafespaces_api_key: str
    llmsafespaces_runtime: str
    workspace_ready_timeout: int
    workspace_session_concurrency: int
    workspace_health_poll_interval: int
    sticky_author: str
    sticky_text_regex: str
    baseline_default_category: Category
    max_post_age_hours: int
    source_flair_include: list[str]
    source_flair_exclude: list[str]
    shadow_mode: bool
    shadow_target_subreddit: str | None
    shadow_distinguish_sticky: bool
    state_backend: str
    state_path: str
    state_database_url: str | None
    state_prune_hours: int
    metrics_port: int
    log_json: bool
    log_level: str
    dry_run: bool
    bot_source_url: str
    bot_issues_url: str
    llmsafespaces_footer_url: str


def load_config() -> Config:
    required = (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USERNAME",
        "REDDIT_PASSWORD",
        "REDDIT_USER_AGENT",
        "LLMSAFESPACES_URL",
        "LLMSAFESPACES_API_KEY",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ConfigError(f"Missing required env vars: {', '.join(missing)}")

    state_backend = _get_str("STATE_BACKEND", "json")
    if state_backend not in _VALID_STATE_BACKENDS:
        raise ConfigError(
            f"STATE_BACKEND={state_backend!r} must be one of {sorted(_VALID_STATE_BACKENDS)}"
        )

    state_path_default = _DEFAULT_STATE_PATH[state_backend]
    state_path = _get_str("STATE_PATH", state_path_default) if state_path_default else ""

    state_database_url: str | None = None
    if state_backend == "postgres":
        state_database_url = os.getenv("STATE_DATABASE_URL")
        if not state_database_url:
            raise ConfigError("STATE_BACKEND=postgres requires STATE_DATABASE_URL")

    shadow_mode = _get_bool("SHADOW_MODE", False)
    shadow_target_subreddit: str | None = None
    if shadow_mode:
        shadow_target_subreddit = os.getenv("SHADOW_TARGET_SUBREDDIT")
        if not shadow_target_subreddit:
            raise ConfigError("SHADOW_MODE=true requires SHADOW_TARGET_SUBREDDIT")

    default_category_raw = _get_str("BASELINE_DEFAULT_CATEGORY", "media")
    try:
        default_category = Category(default_category_raw)
    except ValueError as e:
        valid = sorted(c.value for c in Category)
        raise ConfigError(
            f"BASELINE_DEFAULT_CATEGORY={default_category_raw!r} must be one of {valid}"
        ) from e

    return Config(
        reddit_client_id=_require("REDDIT_CLIENT_ID"),
        reddit_client_secret=_require("REDDIT_CLIENT_SECRET"),
        reddit_username=_require("REDDIT_USERNAME"),
        reddit_password=_require("REDDIT_PASSWORD"),
        reddit_user_agent=_require("REDDIT_USER_AGENT"),
        reddit_subreddit=_get_str("REDDIT_SUBREDDIT", "selfhosted"),
        reddit_new_limit=_get_int("REDDIT_NEW_LIMIT", 25, min_value=1, max_value=100),
        reddit_api_max_retries=_get_int("REDDIT_API_MAX_RETRIES", 3, min_value=0, max_value=10),
        llmsafespaces_url=_require("LLMSAFESPACES_URL"),
        llmsafespaces_api_key=_require("LLMSAFESPACES_API_KEY"),
        llmsafespaces_runtime=_get_str("LLMSAFESPACES_RUNTIME", "python"),
        workspace_ready_timeout=_get_int("WORKSPACE_READY_TIMEOUT", 300, min_value=10, max_value=3600),
        workspace_session_concurrency=_get_int("WORKSPACE_SESSION_CONCURRENCY", 3, min_value=1, max_value=20),
        workspace_health_poll_interval=_get_int("WORKSPACE_HEALTH_POLL_INTERVAL", 30, min_value=5, max_value=300),
        sticky_author=_get_str("STICKY_AUTHOR", "asimovs-auditor"),
        sticky_text_regex=_get_str("STICKY_TEXT_REGEX", r"(?i)how AI was used"),
        baseline_default_category=default_category,
        max_post_age_hours=_get_int("MAX_POST_AGE_HOURS", 24, min_value=1, max_value=720),
        source_flair_include=_get_list("SOURCE_FLAIR_INCLUDE"),
        source_flair_exclude=_get_list("SOURCE_FLAIR_EXCLUDE"),
        shadow_mode=shadow_mode,
        shadow_target_subreddit=shadow_target_subreddit,
        shadow_distinguish_sticky=_get_bool("SHADOW_DISTINGUISH_STICKY", False),
        state_backend=state_backend,
        state_path=state_path,
        state_database_url=state_database_url,
        state_prune_hours=_get_int("STATE_PRUNE_HOURS", 48, min_value=1, max_value=720),
        metrics_port=_get_int("METRICS_PORT", 8080, min_value=1, max_value=65535),
        log_json=_get_bool("LOG_JSON", False),
        log_level=_get_str("LOG_LEVEL", "INFO"),
        dry_run=_get_bool("DRY_RUN", False),
        bot_source_url=_get_str(
            "BOT_SOURCE_URL",
            "https://github.com/lenaxia/functions/tree/main/functions/repo-assessor",
        ),
        bot_issues_url=_get_str(
            "BOT_ISSUES_URL",
            "https://github.com/lenaxia/functions/issues",
        ),
        llmsafespaces_footer_url=_get_str(
            "LLMSAFESPACES_URL_FOOTER",
            "https://github.com/lenaxia/llmsafespaces",
        ),
    )


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required env var: {name}")
    return value


def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def _get_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name}={raw!r} must be an integer") from e
    if parsed < min_value or parsed > max_value:
        raise ConfigError(
            f"{name}={parsed} out of range [{min_value}, {max_value}]"
        )
    return parsed


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name}={raw!r} must be one of true/false/1/0/yes/no/on/off (case-insensitive)")


def _get_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]
