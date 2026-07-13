"""Tests for state/__init__.py factory."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from model import StateError


def _minimal_config(monkeypatch):
    for k, v in {
        "REDDIT_CLIENT_ID": "x", "REDDIT_CLIENT_SECRET": "x",
        "REDDIT_USERNAME": "x", "REDDIT_PASSWORD": "x",
        "REDDIT_USER_AGENT": "x",
        "LLMSAFESPACES_URL": "x", "LLMSAFESPACES_API_KEY": "x",
    }.items():
        monkeypatch.setenv(k, v)


def test_factory_creates_json_backend(monkeypatch, tmp_path: Path) -> None:
    _minimal_config(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "json")
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))
    import config as cfg_mod
    from state import state_from_config
    cfg = cfg_mod.load_config()
    store = state_from_config(cfg)
    from state.json_backend import JsonState
    assert isinstance(store, JsonState)
    store.close()


def test_factory_creates_sqlite_backend(monkeypatch, tmp_path: Path) -> None:
    _minimal_config(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "sqlite")
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.db"))
    import config as cfg_mod
    from state import state_from_config
    cfg = cfg_mod.load_config()
    store = state_from_config(cfg)
    from state.sqlite_backend import SqliteState
    assert isinstance(store, SqliteState)
    store.close()


def test_factory_creates_postgres_backend(monkeypatch) -> None:
    _minimal_config(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "postgres")
    monkeypatch.setenv("STATE_DATABASE_URL", "postgresql://u:p@h/d")

    pg_mod = types.ModuleType("psycopg")
    pg_mod.OperationalError = type("OperationalError", (Exception,), {})

    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *a, **k): pass
    class _Conn:
        def cursor(self): return _Cursor()
        def close(self): pass
    pg_mod.connect = MagicMock(return_value=_Conn())
    monkeypatch.setitem(sys.modules, "psycopg", pg_mod)

    import config as cfg_mod
    from state import state_from_config
    cfg = cfg_mod.load_config()
    store = state_from_config(cfg)
    from state.postgres_backend import PostgresState
    assert isinstance(store, PostgresState)
    store.close()


def test_factory_unknown_backend_raises(monkeypatch, tmp_path: Path) -> None:
    _minimal_config(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "redis")
    monkeypatch.setenv("STATE_DATABASE_URL", "x")
    import config as cfg_mod
    from state import state_from_config
    with pytest.raises(cfg_mod.ConfigError):
        cfg = cfg_mod.load_config()
