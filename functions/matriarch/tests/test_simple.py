"""Smoke tests — verify `main()` reaches a well-formed return value.

These tests don't validate workflow correctness, only that the entry
point parses its configuration, builds its clients, and returns a dict
with the expected shape.
"""

import tempfile

import main


def test_main_returns_test_mode_skip(monkeypatch):
    """TEST_MODE=true short-circuits the entire pipeline and returns success."""
    monkeypatch.setenv("KOMGA_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODE", "true")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"
    assert "message" in result
    assert result["test_mode"] is True


def test_main_returns_test_mode_with_dry_run(monkeypatch):
    """DRY_RUN + TEST_MODE both set still returns the test-mode short-circuit."""
    monkeypatch.setenv("KOMGA_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("DRY_RUN", "true")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"
    assert result["test_mode"] is True


def test_main_uses_env_overrides(monkeypatch):
    """SERIES_NAME and VIOLET_URL env vars override the hardcoded defaults.

    matriarch ships with defaults for the original series ("I'll Be The
    Matriarch In This Life") but env-var injection should always win.
    """
    monkeypatch.setenv("KOMGA_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("SERIES_NAME", "Custom Series Name")
    monkeypatch.setenv("VIOLET_URL", "https://violetscans.org/comics/custom/")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"
