"""Shared fixtures + helpers for the violetscans test suite.

Test design notes:

- `main.py` performs runtime secret discovery via `_init_secret_source()`,
  which inspects the module-level `SECRET_BASE_DIR` (`/secrets/fission`). All
  tests that exercise `main()` must either:
    (a) reset module-level state via `reset_secret_state` and provide values
        via environment variables (the `_secret()` helper falls back to env
        when no secret mount is discovered), OR
    (b) point `SECRET_BASE_DIR` at a temporary directory and seed it with
        the expected per-series files.
  The `_reset_secret_state` autouse fixture handles (a) for every test.

- The CI build step (`function-builder.yaml`) runs `pytest tests/`. Because
  the build also installs `requirements.txt` into the function directory
  prior to packaging, any `tests/` import of `bs4`, `requests`, etc. must
  resolve from the locally-installed deps. Tests therefore avoid importing
  the network layer at module scope where possible.
"""

import os
import sys
from pathlib import Path

import pytest

# Make `import main` work from any test file. Insert the function directory
# (one level up from tests/) onto sys.path.
_FUNCTION_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_FUNCTION_DIR))

import main  # noqa: E402 — sys.path manipulation must precede import


# Env vars that main() and the classes read. Used by fixtures to scrub state.
_RELEVANT_ENV_VARS = (
    "SERIES_NAME",
    "VIOLET_URL",
    "KOMGA_API_URL",
    "KOMGA_API_KEY",
    "KOMGA_LIBRARY_ID",
    "SCRATCH_PATH",
    "SCRATCH_SUBDIR",
    "DRY_RUN",
    "TEST_MODE",
    "FISSION_SECRET_NAME",
)


@pytest.fixture(autouse=True)
def _reset_secret_state(monkeypatch, tmp_path):
    """Reset module-level state and isolate every test.

    - Clears `_SECRET_DIR` and `_SECRET_NAME` so each test starts from a
      pristine discovery state.
    - Repoints `SECRET_BASE_DIR` to a per-test tmp directory so the module
      can never accidentally find a real `/secrets/fission/` mount during
      local development.
    - Clears all relevant env vars.
    """
    main._SECRET_DIR = None
    main._SECRET_NAME = None

    fake_secret_base = tmp_path / "secrets" / "fission"
    fake_secret_base.mkdir(parents=True)
    monkeypatch.setattr(main, "SECRET_BASE_DIR", fake_secret_base)

    for key in _RELEVANT_ENV_VARS:
        monkeypatch.delenv(key, raising=False)

    yield


@pytest.fixture
def required_env(monkeypatch):
    """Set the minimum env vars that `main()` needs to proceed past validation.

    Tests that don't want to error on missing SERIES_NAME / VIOLET_URL /
    KOMGA_API_KEY should request this fixture.
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
