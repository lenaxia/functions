"""Mount-path contract for trading_common.secrets (review Finding 1).

Each trading Function CR mounts exactly one secret, trading-<fn>, at
/secrets/fission/trading-<fn>/. The reader must discover that mount —
NOT a hardcoded `trading` name — else every function specializes and
then fails at runtime on empty credentials.

These tests pin the discovery against a fake mount tree.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "include"))

from trading_common import secrets as ts  # noqa: E402


@pytest.fixture()
def fake_mount(tmp_path, monkeypatch):
    """Fake /secrets/fission/trading-debate/ mount; returns the dir."""
    base = tmp_path / "fission"
    d = base / "trading-debate"
    d.mkdir(parents=True)
    monkeypatch.setattr(ts, "SECRET_BASE_DIR", base)
    return d


class TestDiscovery:
    def test_key_read_from_trading_prefixed_mount(self, fake_mount, monkeypatch):
        (fake_mount / "PGPASSWORD").write_text("s3cret\n")
        (fake_mount / "SHADOW").write_text("0")
        monkeypatch.delenv("PGPASSWORD", raising=False)
        monkeypatch.delenv("SHADOW", raising=False)
        assert ts.secret("PGPASSWORD") == "s3cret"
        assert ts.flag("SHADOW") is False

    def test_env_fallback_when_key_absent_from_mount(self, fake_mount, monkeypatch):
        monkeypatch.setenv("PGPASSWORD", "from-env")
        assert ts.secret("PGPASSWORD") == "from-env"

    def test_default_when_nowhere(self, fake_mount, monkeypatch):
        monkeypatch.delenv("PGPASSWORD", raising=False)
        assert ts.secret("PGPASSWORD", "dflt") == "dflt"

    def test_flag_defaults_shadow_on(self, fake_mount, monkeypatch):
        monkeypatch.delenv("SHADOW", raising=False)
        assert ts.flag("SHADOW") is True  # deployment default: shadow

    def test_no_mount_at_all_falls_back_to_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ts, "SECRET_BASE_DIR", tmp_path / "nonexistent")
        monkeypatch.setenv("PGPASSWORD", "env-only")
        assert ts.secret("PGPASSWORD") == "env-only"

    def test_multiple_dirs_prefers_unique_trading_prefix(self, tmp_path, monkeypatch):
        base = tmp_path / "fission"
        (base / "other-secret").mkdir(parents=True)
        d = base / "trading-debate"
        d.mkdir()
        (d / "PGPASSWORD").write_text("picked")
        monkeypatch.setattr(ts, "SECRET_BASE_DIR", base)
        monkeypatch.delenv("PGPASSWORD", raising=False)
        assert ts.secret("PGPASSWORD") == "picked"

    def test_mount_desc_names_the_mount(self, fake_mount):
        assert "trading-debate" in ts.mount_desc()
