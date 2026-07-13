"""Tests for config.py — T-101 through T-112."""

from __future__ import annotations

import pytest


def _minimal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the bare-minimum required env vars."""
    required = {
        "REDDIT_CLIENT_ID": "client_id",
        "REDDIT_CLIENT_SECRET": "client_secret",
        "REDDIT_USERNAME": "bot_user",
        "REDDIT_PASSWORD": "bot_pass",
        "REDDIT_USER_AGENT": "repo-assessor/0.1 by bot_user",
        "LLMSAFESPACES_URL": "https://lss.example.com",
        "LLMSAFESPACES_API_KEY": "lsp_xxx",
    }
    for k, v in required.items():
        monkeypatch.setenv(k, v)


def _import_config():
    import config
    return config


def test_T_101_all_required_present_constructs(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    config = _import_config()
    cfg = config.load_config()
    assert cfg.reddit_client_id == "client_id"
    assert cfg.llmsafespaces_url == "https://lss.example.com"
    assert cfg.llmsafespaces_api_key == "lsp_xxx"


def test_T_102_missing_required_raises_with_var_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.delenv("LLMSAFESPACES_API_KEY", raising=False)
    config = _import_config()
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_config()
    assert "LLMSAFESPACES_API_KEY" in str(exc_info.value)


def test_T_102_missing_any_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.delenv("REDDIT_USERNAME", raising=False)
    config = _import_config()
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_T_103_baseline_default_category_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("BASELINE_DEFAULT_CATEGORY", "security")
    config = _import_config()
    cfg = config.load_config()
    assert cfg.baseline_default_category.value == "security"


def test_T_104_invalid_baseline_default_category_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("BASELINE_DEFAULT_CATEGORY", "foo")
    config = _import_config()
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_T_105_shadow_mode_without_target_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("SHADOW_MODE", "true")
    config = _import_config()
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_config()
    assert "SHADOW_TARGET_SUBREDDIT" in str(exc_info.value)


def test_T_105_shadow_mode_with_target_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("SHADOW_MODE", "true")
    monkeypatch.setenv("SHADOW_TARGET_SUBREDDIT", "shadowtest")
    config = _import_config()
    cfg = config.load_config()
    assert cfg.shadow_mode is True
    assert cfg.shadow_target_subreddit == "shadowtest"


def test_T_106_postgres_without_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "postgres")
    config = _import_config()
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_config()
    assert "STATE_DATABASE_URL" in str(exc_info.value)


def test_T_107_comma_separated_flair_lists_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("SOURCE_FLAIR_INCLUDE", "Show-off,Project")
    monkeypatch.setenv("SOURCE_FLAIR_EXCLUDE", "Removed,Meme")
    config = _import_config()
    cfg = config.load_config()
    assert cfg.source_flair_include == ["Show-off", "Project"]
    assert cfg.source_flair_exclude == ["Removed", "Meme"]


def test_T_107_empty_flair_list_parses_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    config = _import_config()
    cfg = config.load_config()
    assert cfg.source_flair_include == []
    assert cfg.source_flair_exclude == []


def test_T_108_whitespace_trimmed_in_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("SOURCE_FLAIR_INCLUDE", "  Show-off ,  Project  ")
    config = _import_config()
    cfg = config.load_config()
    assert cfg.source_flair_include == ["Show-off", "Project"]


def test_T_109_booleans_accept_canonical_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    config = _import_config()
    for true_val in ("true", "True", "TRUE", "1", "yes", "YES", "on", "ON"):
        monkeypatch.setenv("DRY_RUN", true_val)
        cfg = config.load_config()
        assert cfg.dry_run is True, f"{true_val!r} should parse to True"
    for false_val in ("false", "False", "FALSE", "0", "no", "NO", "off", "OFF", ""):
        monkeypatch.setenv("DRY_RUN", false_val)
        cfg = config.load_config()
        assert cfg.dry_run is False, f"{false_val!r} should parse to False"


def test_T_109_boolean_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "maybe")
    config = _import_config()
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_T_110_int_rejects_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("REDDIT_NEW_LIMIT", "abc")
    config = _import_config()
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_T_111_int_range_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    config = _import_config()
    for bad in ("0", "101", "-1"):
        monkeypatch.setenv("REDDIT_NEW_LIMIT", bad)
        with pytest.raises(config.ConfigError):
            config.load_config()
    for good in ("1", "100", "25"):
        monkeypatch.setenv("REDDIT_NEW_LIMIT", good)
        cfg = config.load_config()
        assert cfg.reddit_new_limit == int(good)


def test_T_112_defaults_applied_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    config = _import_config()
    cfg = config.load_config()
    assert cfg.reddit_subreddit == "selfhosted"
    assert cfg.reddit_new_limit == 25
    assert cfg.llmsafespaces_runtime == "python"
    assert cfg.workspace_ready_timeout == 300
    assert cfg.workspace_session_concurrency == 3
    assert cfg.workspace_health_poll_interval == 30
    assert cfg.sticky_author == "asimovs-auditor"
    assert cfg.max_post_age_hours == 24
    assert cfg.state_backend == "json"
    assert cfg.state_path == "/state/repo-assessor.json"
    assert cfg.state_prune_hours == 48
    assert cfg.metrics_port == 8080
    assert cfg.log_json is False
    assert cfg.log_level == "INFO"
    assert cfg.dry_run is False
    assert cfg.shadow_mode is False
    assert cfg.shadow_distinguish_sticky is False


def test_T_112_state_path_default_for_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "sqlite")
    config = _import_config()
    cfg = config.load_config()
    assert cfg.state_path == "/state/repo-assessor.db"


def test_T_112_state_path_explicit_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("STATE_PATH", "/custom/path.json")
    config = _import_config()
    cfg = config.load_config()
    assert cfg.state_path == "/custom/path.json"


def test_T_106_invalid_state_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _minimal_env(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "redis")
    config = _import_config()
    with pytest.raises(config.ConfigError):
        config.load_config()
