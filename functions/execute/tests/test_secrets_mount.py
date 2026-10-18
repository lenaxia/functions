"""execute-side mount-path contract (mirror of debate's test_secrets_mount).

Covers the Alpaca paper keys + PILOT_NOTIONAL int parsing through the
same discovered-mount reader.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "include"))

from trading_common import secrets as ts  # noqa: E402


@pytest.fixture()
def fake_mount(tmp_path, monkeypatch):
    base = tmp_path / "fission"
    d = base / "trading-execute"
    d.mkdir(parents=True)
    monkeypatch.setattr(ts, "SECRET_BASE_DIR", base)
    return d


class TestExecuteKeys:
    def test_alpaca_keys_from_mount(self, fake_mount, monkeypatch):
        (fake_mount / "ALPACA_PAPER_API_KEY").write_text("PKKEY\n")
        (fake_mount / "ALPACA_PAPER_API_SECRET").write_text("PKSEC\n")
        (fake_mount / "ALPACA_PAPER_API_ENDPOINT").write_text(
            "https://paper-api.alpaca.markets\n")
        (fake_mount / "PILOT_NOTIONAL").write_text("135000")
        (fake_mount / "SHADOW").write_text("0")
        for k in ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET",
                  "ALPACA_PAPER_API_ENDPOINT", "PILOT_NOTIONAL", "SHADOW"):
            monkeypatch.delenv(k, raising=False)
        assert ts.secret("ALPACA_PAPER_API_KEY") == "PKKEY"
        assert ts.secret("ALPACA_PAPER_API_SECRET") == "PKSEC"
        assert ts.secret("ALPACA_PAPER_API_ENDPOINT") == "https://paper-api.alpaca.markets"
        assert ts.secret_int("PILOT_NOTIONAL", 0) == 135000
        assert ts.flag("SHADOW") is False  # cutover flip is honored

    def test_shadow_flip_not_silently_ignored(self, fake_mount, monkeypatch):
        # regression for the cutover footgun: SHADOW=0 in the secret must
        # win over the default when the env does not override it
        (fake_mount / "SHADOW").write_text("0")
        monkeypatch.delenv("SHADOW", raising=False)
        assert ts.flag("SHADOW") is False

    def test_missing_everything_still_fails_closed_to_shadow(self, fake_mount, monkeypatch):
        monkeypatch.delenv("SHADOW", raising=False)
        assert ts.flag("SHADOW") is True

    def test_two_mounts_resolves_shared_trading_mount(self, tmp_path, monkeypatch):
        # shared-secret deploy: exact `trading` dir must win over any
        # unrelated mount
        base = tmp_path / "fission"
        (base / "unrelated").mkdir(parents=True)
        d = base / "trading"
        d.mkdir()
        (d / "ALPACA_PAPER_API_KEY").write_text("from-shared-mount")
        monkeypatch.setattr(ts, "SECRET_BASE_DIR", base)
        monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
        assert ts.secret("ALPACA_PAPER_API_KEY") == "from-shared-mount"
