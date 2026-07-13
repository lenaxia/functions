"""Tests for state/json_backend.py — contract + JSON-specific (T-201..T-215, T-301..T-305)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import state_contract
from model import Decision, StateError


def _import_json_backend():
    from state.json_backend import JsonState
    return JsonState


class TestJsonBackendContract(state_contract.StateStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path):
        JsonState = _import_json_backend()
        path = tmp_path / "state.json"
        s = JsonState(str(path))
        yield s
        try:
            s.close()
        except StateError:
            pass


# JSON-specific tests ─────────────────────────────────────────────────────────


def test_T_301_atomic_write_uses_temp_then_rename(tmp_path: Path) -> None:
    JsonState = _import_json_backend()
    path = tmp_path / "state.json"
    s = JsonState(str(path))
    s.set_decision("s1", Decision.POSTED, "ok")
    s.close()
    assert path.exists()
    data = json.loads(path.read_text())
    assert "s1" in data["decisions"]
    tmp_files = list(tmp_path.glob(".state.json.tmp*"))
    assert tmp_files == []


def test_T_302_partial_write_does_not_corrupt_existing(tmp_path: Path, monkeypatch) -> None:
    JsonState = _import_json_backend()
    path = tmp_path / "state.json"
    s = JsonState(str(path))
    s.set_decision("survive", Decision.POSTED, "must-survive")
    s.close()

    s2 = JsonState(str(path))

    def boom(self, payload):
        raise RuntimeError("simulated mid-write crash")

    monkeypatch.setattr(JsonState, "_write_payload", boom)
    with pytest.raises(RuntimeError):
        s2.set_decision("new", Decision.POSTED, "should-not-appear")
    s2.close()

    data = json.loads(path.read_text())
    assert "survive" in data["decisions"]
    assert "new" not in data["decisions"]


def test_T_303_missing_parent_dir_created(tmp_path: Path) -> None:
    JsonState = _import_json_backend()
    nested = tmp_path / "a" / "b" / "c" / "state.json"
    s = JsonState(str(nested))
    s.set_decision("s1", Decision.POSTED, "ok")
    s.close()
    assert nested.exists()


def test_T_304_missing_file_reads_as_empty(tmp_path: Path) -> None:
    JsonState = _import_json_backend()
    path = tmp_path / "never_existed.json"
    s = JsonState(str(path))
    assert s.get_decision("anything") is None
    assert s.get_in_flight("anything") is None
    assert s.list_stale_in_flight(__import__("datetime").timedelta(days=1)) == []


def test_T_305_corrupt_json_raises_with_path(tmp_path: Path) -> None:
    JsonState = _import_json_backend()
    path = tmp_path / "state.json"
    path.write_text("{ this is not json")
    with pytest.raises(StateError) as exc_info:
        JsonState(str(path))
    assert str(path) in str(exc_info.value)
