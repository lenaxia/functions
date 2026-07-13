"""Tests for state/postgres_backend.py — T-310..T-314 (mocked psycopg).

Behavioural contract tests (T-201..T-215) are NOT run against the Postgres
backend with mocks because mocking out the database turns the contract
tests into mock-introspection tests rather than behavioural ones. The
real contract validation is T-315 in test_state_live.py, gated on
TEST_POSTGRES_URL.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from model import Decision, StateError


@pytest.fixture
def mock_psycopg(monkeypatch: pytest.MonkeyPatch):
    pg_mod = types.ModuleType("psycopg")
    pg_mod.OperationalError = type("OperationalError", (Exception,), {})

    captured_sql: list[tuple[str, tuple]] = []

    class _Cursor:
        def __init__(self):
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            captured_sql.append((sql, tuple(params or ())))

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class _Conn:
        def __init__(self):
            self._closed = False

        def cursor(self):
            return _Cursor()

        def close(self):
            self._closed = True

    def _connect(dsn, **kwargs):
        return _Conn()

    pg_mod.connect = MagicMock(side_effect=_connect)
    monkeypatch.setitem(sys.modules, "psycopg", pg_mod)
    return pg_mod, captured_sql


def _import_postgres_backend():
    from state.postgres_backend import PostgresState
    return PostgresState


def test_T_310_schema_created_on_init(mock_psycopg) -> None:
    PostgresState = _import_postgres_backend()
    _, sql = mock_psycopg
    s = PostgresState("postgresql://u:p@h/d")
    s.close()
    create_calls = [c[0] for c in sql if "CREATE TABLE" in c[0]]
    assert len(create_calls) >= 1
    assert "repo_assessor_state" in create_calls[0]


def test_T_310_schema_creation_is_idempotent(mock_psycopg) -> None:
    PostgresState = _import_postgres_backend()
    s1 = PostgresState("postgresql://u:p@h/d")
    s1.close()
    s2 = PostgresState("postgresql://u:p@h/d")
    s2.close()
    # CREATE TABLE IF NOT EXISTS runs twice; harmless by design.


def test_T_311_indexes_created(mock_psycopg) -> None:
    PostgresState = _import_postgres_backend()
    _, sql = mock_psycopg
    s = PostgresState("postgresql://u:p@h/d")
    s.close()
    index_calls = [c[0] for c in sql if "CREATE INDEX" in c[0]]
    assert len(index_calls) >= 2


def test_T_312_connection_retry_on_transient_error(mock_psycopg) -> None:
    PostgresState = _import_postgres_backend()
    pg_mod, _ = mock_psycopg
    OpErr = pg_mod.OperationalError

    call_count = {"n": 0}

    def flaky_connect(dsn, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise OpErr("transient")

        class _C:
            def cursor(self): return _StubCursor()
            def close(self): pass

        class _StubCursor:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, sql, params=None): pass

        return _C()

    pg_mod.connect = MagicMock(side_effect=flaky_connect)
    s = PostgresState("postgresql://u:p@h/d", connect_retries=5, connect_retry_delay=0)
    assert call_count["n"] == 3
    s.close()


def test_T_313_persistent_connection_failure_raises(mock_psycopg) -> None:
    PostgresState = _import_postgres_backend()
    pg_mod, _ = mock_psycopg
    pg_mod.connect = MagicMock(side_effect=pg_mod.OperationalError("permanent"))
    with pytest.raises(StateError):
        PostgresState("postgresql://u:p@h/d", connect_retries=2, connect_retry_delay=0)


def test_T_314_sql_uses_placeholders_not_format_strings(mock_psycopg) -> None:
    PostgresState = _import_postgres_backend()
    _, sql = mock_psycopg
    s = PostgresState("postgresql://u:p@h/d")
    s.set_decision("s1", Decision.POSTED, "reason")
    s.close()

    insert_calls = [c for c in sql if "INSERT INTO repo_assessor_state" in c[0]]
    assert len(insert_calls) == 1
    sql_stmt, params = insert_calls[0]
    assert "?" not in sql_stmt, "SQLite-style placeholders should not appear"
    assert isinstance(params, tuple)
    assert "s1" in params
    assert "posted" in params
    assert "reason" in params


def test_T_314_no_string_interpolation_of_user_data(mock_psycopg) -> None:
    PostgresState = _import_postgres_backend()
    _, sql = mock_psycopg
    s = PostgresState("postgresql://u:p@h/d")
    malicious_id = "'; DROP TABLE repo_assessor_state; --"
    s.set_decision(malicious_id, Decision.POSTED, "ok")
    s.close()
    inserts = [c for c in sql if "INSERT" in c[0]]
    assert all(malicious_id in c[1] for c in inserts), "malicious id should be passed as param, not in SQL"
    assert all(malicious_id not in c[0] for c in inserts), "malicious id must not appear in SQL string"
