"""Tests for state/sqlite_backend.py — contract + SQLite-specific (T-306..T-309)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import state_contract
from model import Decision, StateError


def _import_sqlite_backend():
    from state.sqlite_backend import SqliteState
    return SqliteState


class TestSqliteBackendContract(state_contract.StateStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path):
        SqliteState = _import_sqlite_backend()
        path = tmp_path / "state.db"
        s = SqliteState(str(path))
        yield s
        try:
            s.close()
        except StateError:
            pass


# SQLite-specific tests ────────────────────────────────────────────────────────


def test_T_306_schema_created_idempotently_on_init(tmp_path: Path) -> None:
    SqliteState = _import_sqlite_backend()
    path = tmp_path / "state.db"
    s1 = SqliteState(str(path))
    s1.set_decision("s1", Decision.POSTED, "first")
    s1.close()
    s2 = SqliteState(str(path))
    s2.set_decision("s2", Decision.POSTED, "second")
    s2.close()

    conn = sqlite3.connect(str(path))
    rows = conn.execute("SELECT submission_id FROM decisions ORDER BY submission_id").fetchall()
    conn.close()
    assert rows == [("s1",), ("s2",)]


def test_T_307_wal_mode_enabled(tmp_path: Path) -> None:
    SqliteState = _import_sqlite_backend()
    path = tmp_path / "state.db"
    s = SqliteState(str(path))
    s.close()
    conn = sqlite3.connect(str(path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()
    conn.close()
    assert mode[0].lower() == "wal"


def test_T_308_missing_parent_dir_created(tmp_path: Path) -> None:
    SqliteState = _import_sqlite_backend()
    nested = tmp_path / "a" / "b" / "state.db"
    s = SqliteState(str(nested))
    s.set_decision("s1", Decision.POSTED, "ok")
    s.close()
    assert nested.exists()


def test_T_309_locked_db_raises_after_timeout(tmp_path: Path, monkeypatch) -> None:
    SqliteState = _import_sqlite_backend()
    path = tmp_path / "state.db"
    s1 = SqliteState(str(path))

    blocker = sqlite3.connect(str(path), timeout=0.1)
    blocker.execute("BEGIN EXCLUSIVE")
    blocker.execute("SELECT * FROM decisions")

    monkeypatch.setattr("state.sqlite_backend._BUSY_TIMEOUT", 0.2)
    with pytest.raises(StateError):
        s2 = SqliteState(str(path), busy_timeout_ms=200)
        s2.set_decision("blocked", Decision.POSTED, "x")

    blocker.rollback()
    blocker.close()
    s1.close()
