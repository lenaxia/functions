"""StateStore factory — selects backend implementation from config."""

from __future__ import annotations

from model import StateStore


def state_from_config(config) -> StateStore:
    backend = config.state_backend
    if backend == "json":
        from state.json_backend import JsonState
        return JsonState(config.state_path)
    if backend == "sqlite":
        from state.sqlite_backend import SqliteState
        return SqliteState(config.state_path)
    if backend == "postgres":
        from state.postgres_backend import PostgresState
        if not config.state_database_url:
            raise ValueError("STATE_DATABASE_URL is required for postgres backend")
        return PostgresState(config.state_database_url)
    raise ValueError(f"unknown state backend: {backend!r}")
