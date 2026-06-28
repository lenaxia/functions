"""Shared fixtures + helpers for the matriarch test suite.

Unlike violetscans (which has runtime secret discovery), matriarch reads
its secret from a hardcoded path: /secrets/fission/matriarch/. Tests work
purely via env-var injection — the `_secret()` helper falls back to env
when the secret file doesn't exist.
"""

import os
import sys
from pathlib import Path

import pytest

# Make `import main` work from any test file.
_FUNCTION_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_FUNCTION_DIR))

import main  # noqa: E402 — sys.path manipulation must precede import


_RELEVANT_ENV_VARS = (
    "SERIES_NAME",
    "VIOLET_URL",
    "KOMGA_API_URL",
    "KOMGA_API_KEY",
    "KOMGA_LIBRARY_ID",
    "SCRATCH_PATH",
    "DRY_RUN",
    "TEST_MODE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Scrub all matriarch-relevant env vars before each test."""
    for key in _RELEVANT_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def required_env(monkeypatch):
    """Set the minimum env vars main() needs to proceed past validation.

    matriarch only requires KOMGA_API_KEY — everything else has a default —
    but tests typically want all three so they're more representative.
    """
    monkeypatch.setenv("SERIES_NAME", "Test Series")
    monkeypatch.setenv("VIOLET_URL", "https://violetscans.org/comics/test/")
    monkeypatch.setenv("KOMGA_API_KEY", "test-key-12345")
    monkeypatch.setenv("KOMGA_API_URL", "http://komga.example.com")


def make_response(
    text: str = "",
    status: int = 200,
    content_type: str = "image/jpeg",
    content: bytes = b"FAKEIMG",
):
    """Build a mock `requests.Response` with the canonical attributes."""
    from unittest.mock import Mock

    r = Mock()
    r.text = text
    r.status_code = status
    r.raise_for_status = Mock()
    r.headers = {"Content-Type": content_type}
    r.content = content
    r.json = Mock(return_value={})
    return r
