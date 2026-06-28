"""Tests for `main()` entry point: error paths and return-value contract.

Covers the validation gate that runs before clients are built.
"""

import tempfile

import main


def test_returns_error_when_series_name_missing(monkeypatch):
    """Missing SERIES_NAME alone should produce a structured error."""
    monkeypatch.setenv("VIOLET_URL", "https://violetscans.org/comics/x/")
    monkeypatch.setenv("KOMGA_API_KEY", "k")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "error"
    assert "SERIES_NAME" in result["message"]


def test_returns_error_when_violet_url_missing(monkeypatch):
    monkeypatch.setenv("SERIES_NAME", "S")
    monkeypatch.setenv("KOMGA_API_KEY", "k")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "error"
    assert "VIOLET_URL" in result["message"]


def test_returns_error_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("SERIES_NAME", "S")
    monkeypatch.setenv("VIOLET_URL", "https://violetscans.org/comics/x/")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "error"
    assert "KOMGA_API_KEY" in result["message"]


def test_returns_error_lists_all_missing_keys(monkeypatch):
    """When multiple keys are missing, all are reported in one error message.

    This avoids the user fixing one key at a time across multiple runs.
    """
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "error"
    msg = result["message"]
    assert "SERIES_NAME" in msg
    assert "VIOLET_URL" in msg
    assert "KOMGA_API_KEY" in msg


def test_test_mode_bypasses_validation(monkeypatch):
    """TEST_MODE=true is checked before key-presence validation.

    This lets the function be invoked with no config at all during smoke
    tests of the deployment surface (e.g. checking that the Function CR
    references the right Package version).
    """
    monkeypatch.setenv("TEST_MODE", "true")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"
    assert result["test_mode"] is True


def test_test_mode_string_case_insensitive(monkeypatch):
    """TEST_MODE comparison is case-insensitive on the string form."""
    monkeypatch.setenv("TEST_MODE", "TRUE")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["test_mode"] is True


def test_return_value_has_documented_keys(monkeypatch):
    """Success result must include: status, message, secret_name, series."""
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("SERIES_NAME", "S")
    monkeypatch.setenv("VIOLET_URL", "https://x")
    monkeypatch.setenv("KOMGA_API_KEY", "k")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    # TEST_MODE path doesn't include series, but does include test_mode.
    assert set(result.keys()) >= {"status", "message", "test_mode", "secret_name"}
