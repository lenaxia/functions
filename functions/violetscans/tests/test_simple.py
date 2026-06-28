"""Smoke tests — verify `main()` reaches a well-formed return value.

These tests don't validate workflow correctness, only that the entry
point parses its configuration, builds its clients, and returns a dict
with the expected shape.
"""

import tempfile
from pathlib import Path

import main


def test_main_returns_test_mode_skip(required_env, monkeypatch):
    """TEST_MODE=true short-circuits the entire pipeline and returns success.

    Verifies the contract: result is a dict with status, message, test_mode,
    and the discovered secret name (None when no secret mount was found).
    """
    monkeypatch.setenv("TEST_MODE", "true")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"
    assert "message" in result
    assert result["test_mode"] is True
    # No secret directory was seeded → discovery returned None.
    assert result["secret_name"] is None


def test_main_returns_test_mode_with_dry_run(required_env, monkeypatch):
    """DRY_RUN + TEST_MODE both set still returns the test-mode short-circuit.

    The test-mode check happens before dry-run evaluation, so TEST_MODE wins.
    """
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("DRY_RUN", "true")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"
    assert result["test_mode"] is True


def test_main_picks_up_env_when_no_secret_mount(required_env, monkeypatch):
    """When no secret mount is discovered, all values come from env vars.

    This is the local-dev / test-mode path and mirrors how matriarch-vy's
    tests configure the function.
    """
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("SERIES_NAME", "Custom Series Name")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"


def test_main_uses_discovered_secret_name_when_available(
    required_env, monkeypatch, tmp_path
):
    """When a secret mount IS present, `secret_name` reflects the mount dir."""
    fake_base = tmp_path / "secrets" / "fission"
    fake_base.mkdir(parents=True, exist_ok=True)
    secret_dir = fake_base / "my-series"
    secret_dir.mkdir()
    (secret_dir / "VIOLET_URL").write_text("https://violetscans.org/comics/x/")
    (secret_dir / "KOMGA_API_KEY").write_text("from-secret")
    (secret_dir / "SERIES_NAME").write_text("Series From Secret")
    (secret_dir / "TEST_MODE").write_text("true")

    monkeypatch.setattr(main, "SECRET_BASE_DIR", fake_base)

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"
    assert result["secret_name"] == "my-series"
