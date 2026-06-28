"""Tests for the runtime secret-discovery mechanism — violetscans-specific.

This is the key feature that lets one shared Package CR be reused by many
per-series Function CRs. Each Function mounts its Secret at
/secrets/fission/<name>/. At startup the function scans for the active
mount by looking for a directory containing both VIOLET_URL and
KOMGA_API_KEY.

These tests cover the discovery logic, override precedence, and the
env-var fallback path used during local development.
"""

from pathlib import Path

import main


def _seed_secret(base: Path, name: str, **keys: str) -> Path:
    """Create a fake mounted secret directory and return its path."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    for k, v in keys.items():
        (d / k).write_text(v)
    return d


# ───────────────────────────── _discover_secret_dir ────────────────────────


class TestDiscoverSecretDir:
    def test_returns_none_when_base_dir_missing(self, monkeypatch, tmp_path):
        """If /secrets/fission doesn't exist at all (e.g. local dev outside k8s),
        discovery must return None gracefully — main() falls back to env vars."""
        monkeypatch.setattr(main, "SECRET_BASE_DIR", tmp_path / "nonexistent")
        assert main._discover_secret_dir() is None

    def test_returns_none_when_no_candidates(self, monkeypatch, tmp_path):
        """Base dir exists but contains no usable secret subdirs."""
        base = tmp_path / "secrets" / "fission"
        base.mkdir(parents=True, exist_ok=True)
        # An empty dir + one with wrong keys: neither qualifies.
        (base / "empty-secret").mkdir()
        _seed_secret(base, "different-tool", OTHER_KEY="x")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        assert main._discover_secret_dir() is None

    def test_picks_dir_with_both_required_keys(self, monkeypatch, tmp_path):
        base = tmp_path / "secrets" / "fission"
        target = _seed_secret(base, "may-i-please", VIOLET_URL="u", KOMGA_API_KEY="k")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        assert main._discover_secret_dir() == target

    def test_ignores_dir_missing_violet_url(self, monkeypatch, tmp_path):
        """KOMGA_API_KEY alone is not enough — VIOLET_URL also required."""
        base = tmp_path / "secrets" / "fission"
        _seed_secret(base, "incomplete", KOMGA_API_KEY="k")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        assert main._discover_secret_dir() is None

    def test_ignores_dir_missing_api_key(self, monkeypatch, tmp_path):
        """VIOLET_URL alone is not enough — KOMGA_API_KEY also required."""
        base = tmp_path / "secrets" / "fission"
        _seed_secret(base, "incomplete", VIOLET_URL="u")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        assert main._discover_secret_dir() is None

    def test_ignores_files_at_base_level(self, monkeypatch, tmp_path):
        """A file (not a dir) at the base level shouldn't crash discovery."""
        base = tmp_path / "secrets" / "fission"
        base.mkdir(parents=True, exist_ok=True)
        (base / "VIOLET_URL").write_text("loose file, not a secret")  # decoy
        target = _seed_secret(base, "real-secret", VIOLET_URL="u", KOMGA_API_KEY="k")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        assert main._discover_secret_dir() == target

    def test_multiple_candidates_returns_first(self, monkeypatch, tmp_path, caplog):
        """If two secrets are accidentally co-mounted, log a warning and pick one.

        Real-world this shouldn't happen — Fission mounts only the secrets
        listed in `function.spec.secrets`. But defensive: still produce a
        result rather than crash.
        """
        import logging

        base = tmp_path / "secrets" / "fission"
        _seed_secret(base, "series-a", VIOLET_URL="u", KOMGA_API_KEY="k")
        _seed_secret(base, "series-b", VIOLET_URL="u", KOMGA_API_KEY="k")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        with caplog.at_level(logging.WARNING, logger="main"):
            found = main._discover_secret_dir()

        assert found is not None
        assert found.name in {"series-a", "series-b"}
        assert any("Multiple candidate" in r.message for r in caplog.records), (
            "expected warning when multiple candidates exist"
        )


# ───────────────────────────── _init_secret_source ─────────────────────────


class TestInitSecretSource:
    def test_no_secret_mount_leaves_state_none(self, monkeypatch, tmp_path):
        """When nothing is found, both module-level vars stay None."""
        monkeypatch.setattr(main, "SECRET_BASE_DIR", tmp_path / "empty")
        main._init_secret_source()
        assert main._SECRET_DIR is None
        assert main._SECRET_NAME is None

    def test_discovery_sets_both_dir_and_name(self, monkeypatch, tmp_path):
        base = tmp_path / "secrets" / "fission"
        _seed_secret(base, "my-series", VIOLET_URL="u", KOMGA_API_KEY="k")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        main._init_secret_source()

        assert main._SECRET_NAME == "my-series"
        assert main._SECRET_DIR is not None
        assert main._SECRET_DIR.name == "my-series"

    def test_explicit_override_wins(self, monkeypatch, tmp_path):
        """FISSION_SECRET_NAME env var should pin the discovered mount.

        This is the disambiguation hook for the (unlikely) case where multiple
        secrets are mounted into the same pod.
        """
        base = tmp_path / "secrets" / "fission"
        _seed_secret(base, "auto-pick", VIOLET_URL="u", KOMGA_API_KEY="k")
        _seed_secret(base, "explicit", VIOLET_URL="u", KOMGA_API_KEY="k2")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        monkeypatch.setenv("FISSION_SECRET_NAME", "explicit")
        main._init_secret_source()

        assert main._SECRET_NAME == "explicit"

    def test_explicit_override_falls_back_to_discovery_when_dir_missing(
        self, monkeypatch, tmp_path
    ):
        """If FISSION_SECRET_NAME points at a non-existent dir, the discovery
        fallback should still find the real mount instead of erroring."""
        base = tmp_path / "secrets" / "fission"
        _seed_secret(base, "actual-mount", VIOLET_URL="u", KOMGA_API_KEY="k")

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)
        monkeypatch.setenv("FISSION_SECRET_NAME", "ghost-that-doesnt-exist")
        main._init_secret_source()

        assert main._SECRET_NAME == "actual-mount", (
            "should fall back to auto-discovery when explicit name is invalid"
        )


# ───────────────────────────────── _secret ─────────────────────────────────


class TestSecret:
    def test_reads_from_mounted_secret(self, monkeypatch, tmp_path):
        """When _SECRET_DIR is set, _secret() reads from its files."""
        secret_dir = tmp_path / "mount"
        secret_dir.mkdir()
        (secret_dir / "SERIES_NAME").write_text("From Secret\n")

        monkeypatch.setattr(main, "_SECRET_DIR", secret_dir)
        # Env var should be IGNORED when secret file exists.
        monkeypatch.setenv("SERIES_NAME", "From Env (should not win)")

        assert main._secret("SERIES_NAME") == "From Secret"

    def test_strips_whitespace_from_secret_value(self, monkeypatch, tmp_path):
        """Kubernetes secret files often have a trailing newline. Strip it."""
        secret_dir = tmp_path / "mount"
        secret_dir.mkdir()
        (secret_dir / "VIOLET_URL").write_text("  https://example.com/  \n\n")

        monkeypatch.setattr(main, "_SECRET_DIR", secret_dir)
        assert main._secret("VIOLET_URL") == "https://example.com/"

    def test_falls_back_to_env_when_secret_missing(self, monkeypatch, tmp_path):
        """If a specific key isn't in the secret dir, _secret() reads env."""
        secret_dir = tmp_path / "mount"
        secret_dir.mkdir()
        # SERIES_NAME exists in secret, but KOMGA_LIBRARY_ID doesn't.
        (secret_dir / "SERIES_NAME").write_text("x")

        monkeypatch.setattr(main, "_SECRET_DIR", secret_dir)
        monkeypatch.setenv("KOMGA_LIBRARY_ID", "from-env")

        assert main._secret("KOMGA_LIBRARY_ID") == "from-env"

    def test_returns_default_when_neither_secret_nor_env(self, monkeypatch):
        """Default is returned when nothing else provides a value."""
        # _SECRET_DIR is None from the autouse fixture in conftest.
        monkeypatch.delenv("MISSING_KEY", raising=False)
        assert main._secret("MISSING_KEY", "fallback") == "fallback"

    def test_no_secret_dir_reads_pure_env(self, monkeypatch):
        """Local-dev path: no secret mount, value comes from env."""
        monkeypatch.setenv("SERIES_NAME", "Local Dev Series")
        assert main._secret("SERIES_NAME") == "Local Dev Series"
