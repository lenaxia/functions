"""Smoke tests for the generic violetscans Fission function.

These are unit-level checks — they don't hit the network or Komga. Build/CI
runs these via `pytest tests/`.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as violetscans  # noqa: E402


def _clear_secret_state():
    """Reset module-level secret discovery state between tests."""
    violetscans._SECRET_DIR = None
    violetscans._SECRET_NAME = None


def test_chapter_str_strips_trailing_zero():
    assert violetscans._chapter_str(47.0) == "47"
    assert violetscans._chapter_str(47.5) == "47.5"
    assert violetscans._chapter_str(0.0) == "0"


def test_main_short_circuits_in_test_mode(monkeypatch):
    """TEST_MODE=true should return success without making any network calls."""
    _clear_secret_state()
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("SERIES_NAME", "Test Series")
    monkeypatch.setenv("VIOLET_URL", "https://violetscans.org/comics/test/")
    monkeypatch.setenv("KOMGA_API_KEY", "test-key")

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = violetscans.main()

    assert result["status"] == "success"
    assert result.get("test_mode") is True


def test_main_errors_when_required_keys_missing(monkeypatch):
    """Without SERIES_NAME, VIOLET_URL, or KOMGA_API_KEY we should fail cleanly."""
    _clear_secret_state()
    # Ensure env is clean
    for k in ("SERIES_NAME", "VIOLET_URL", "KOMGA_API_KEY", "TEST_MODE"):
        monkeypatch.delenv(k, raising=False)

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SCRATCH_PATH", tmp)
        result = violetscans.main()

    assert result["status"] == "error"
    assert "Required secret key" in result["message"]


def test_discover_secret_dir_picks_dir_with_required_keys(monkeypatch, tmp_path):
    """The discovery function should pick the subdir containing both required keys."""
    fake_base = tmp_path / "secrets" / "fission"
    fake_base.mkdir(parents=True)

    # Decoy: a secret dir without the required keys (e.g. matriarch with
    # different keys would still be picked up — both matriarch and violetscans
    # secrets share KOMGA_API_KEY + VIOLET_URL though, so co-mounting is
    # unsupported by design).
    decoy = fake_base / "unrelated"
    decoy.mkdir()
    (decoy / "OTHER_KEY").write_text("x")

    target = fake_base / "may-i-please"
    target.mkdir()
    (target / "VIOLET_URL").write_text("https://violetscans.org/comics/x/")
    (target / "KOMGA_API_KEY").write_text("abc")

    monkeypatch.setattr(violetscans, "SECRET_BASE_DIR", fake_base)
    found = violetscans._discover_secret_dir()
    assert found == target


def test_init_secret_source_honours_override(monkeypatch, tmp_path):
    """FISSION_SECRET_NAME override should win over auto-discovery."""
    fake_base = tmp_path / "secrets" / "fission"
    explicit = fake_base / "explicit-choice"
    explicit.mkdir(parents=True)
    (explicit / "VIOLET_URL").write_text("u")
    (explicit / "KOMGA_API_KEY").write_text("k")

    monkeypatch.setattr(violetscans, "SECRET_BASE_DIR", fake_base)
    monkeypatch.setenv("FISSION_SECRET_NAME", "explicit-choice")
    _clear_secret_state()
    violetscans._init_secret_source()

    assert violetscans._SECRET_NAME == "explicit-choice"
    assert violetscans._SECRET_DIR == explicit
