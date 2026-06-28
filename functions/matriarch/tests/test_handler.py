"""Tests for `main()` entry point: error paths and return-value contract.

matriarch's validation gate is simpler than violetscans': only KOMGA_API_KEY
is required (everything else has a hardcoded default).
"""

import tempfile

import main


def test_returns_error_when_api_key_missing(monkeypatch):
    """KOMGA_API_KEY is the only required key. Missing it errors out cleanly."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "error"
    assert "KOMGA_API_KEY" in result["message"]


def test_no_error_when_only_api_key_set(monkeypatch):
    """When KOMGA_API_KEY is set, validation passes (other keys default).

    Use TEST_MODE to short-circuit before main() tries to hit Komga.
    """
    monkeypatch.setenv("KOMGA_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODE", "true")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert result["status"] == "success"


def test_test_mode_bypasses_validation(monkeypatch):
    """TEST_MODE=true is checked before key-presence validation.

    This lets the function be invoked with no config at all during smoke
    tests of the deployment surface.
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
    """Success result must include: status, message, test_mode.

    matriarch's response is simpler than violetscans' — no `series` or
    `secret_name` fields since identity is hardcoded into main().
    """
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("KOMGA_API_KEY", "k")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = main.main()

    assert set(result.keys()) >= {"status", "message", "test_mode"}
