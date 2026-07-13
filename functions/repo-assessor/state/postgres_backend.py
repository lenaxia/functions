"""Postgres StateStore implementation.

Single-table schema with JSONB columns for flexibility. Schema created
idempotently on first connect. Connection retry on transient errors.
Uses parameterised queries throughout (no string interpolation of user data).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

from model import (
    Decision,
    DecisionRecord,
    InFlight,
    ShadowMapping,
    StateError,
)


_LOG = logging.getLogger(__name__)

_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS repo_assessor_state (
        submission_id       TEXT PRIMARY KEY,
        in_flight           JSONB,
        decision            TEXT,
        decision_reason     TEXT,
        decided_at          TIMESTAMPTZ,
        shadow_submission_id TEXT,
        schema_version      INT DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_in_flight ON repo_assessor_state (in_flight) WHERE in_flight IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_decided_at ON repo_assessor_state (decided_at)",
]


def _load_psycopg():
    try:
        import psycopg
    except ImportError as e:
        raise StateError(
            "psycopg not installed; install psycopg[binary] or change STATE_BACKEND"
        ) from e
    return psycopg


_OPERATIONAL_ERROR: type
try:
    import psycopg as _pg_for_init  # type: ignore[import]
    _OPERATIONAL_ERROR = _pg_for_init.OperationalError  # type: ignore[attr-defined]
except ImportError:
    _OPERATIONAL_ERROR = Exception


class PostgresState:
    def __init__(
        self,
        database_url: str,
        *,
        connect_retries: int = 3,
        connect_retry_delay: float = 1.0,
    ) -> None:
        self._closed = False
        psycopg = _load_psycopg()
        global _OPERATIONAL_ERROR
        _OPERATIONAL_ERROR = psycopg.OperationalError

        last_err: Exception | None = None
        for attempt in range(connect_retries):
            try:
                self._conn = psycopg.connect(database_url, autocommit=True)
                break
            except psycopg.OperationalError as e:
                last_err = e
                _LOG.warning(
                    "postgres connect attempt %d/%d failed: %s",
                    attempt + 1, connect_retries, e,
                )
                if attempt < connect_retries - 1:
                    time.sleep(connect_retry_delay)
        else:
            raise StateError(f"postgres connect failed after {connect_retries} attempts: {last_err}")

        try:
            for stmt in _SCHEMA_SQL:
                with self._conn.cursor() as cur:
                    cur.execute(stmt)
        except Exception as e:
            raise StateError(f"postgres schema init failed: {e}") from e

    def mark_in_flight(self, submission_id: str, workspace_id: str, session_id: str) -> None:
        self._require_open()
        payload = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self._exec(
            "INSERT INTO repo_assessor_state (submission_id, in_flight) VALUES (%s, %s) "
            "ON CONFLICT (submission_id) DO UPDATE SET in_flight = EXCLUDED.in_flight",
            (submission_id, _to_jsonb(payload)),
        )

    def clear_in_flight(self, submission_id: str) -> None:
        self._require_open()
        self._exec(
            "UPDATE repo_assessor_state SET in_flight = NULL WHERE submission_id = %s",
            (submission_id,),
        )

    def get_in_flight(self, submission_id: str) -> InFlight | None:
        row = self._fetchone(
            "SELECT in_flight FROM repo_assessor_state WHERE submission_id = %s",
            (submission_id,),
        )
        if row is None or row[0] is None:
            return None
        record = row[0]
        if isinstance(record, str):
            import json
            record = json.loads(record)
        return InFlight(
            submission_id=submission_id,
            workspace_id=record["workspace_id"],
            session_id=record["session_id"],
            started_at=_parse_dt(record["started_at"]),
        )

    def list_stale_in_flight(self, older_than: dt.timedelta) -> list[InFlight]:
        cutoff_iso = (dt.datetime.now(dt.timezone.utc) - older_than).isoformat()
        rows = self._fetchall(
            "SELECT submission_id, in_flight FROM repo_assessor_state "
            "WHERE in_flight IS NOT NULL AND (in_flight->>'started_at')::timestamptz < %s::timestamptz",
            (cutoff_iso,),
        )
        results: list[InFlight] = []
        for sid, raw in rows:
            if isinstance(raw, str):
                import json
                raw = json.loads(raw)
            results.append(
                InFlight(sid, raw["workspace_id"], raw["session_id"], _parse_dt(raw["started_at"]))
            )
        return results

    def set_decision(self, submission_id: str, decision: Decision, reason: str) -> None:
        self._require_open()
        self._exec(
            "INSERT INTO repo_assessor_state (submission_id, decision, decision_reason, decided_at) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (submission_id) DO UPDATE SET "
            "  decision = EXCLUDED.decision, "
            "  decision_reason = EXCLUDED.decision_reason, "
            "  decided_at = EXCLUDED.decided_at",
            (
                submission_id,
                decision.value,
                reason,
                dt.datetime.now(dt.timezone.utc),
            ),
        )

    def get_decision(self, submission_id: str) -> DecisionRecord | None:
        row = self._fetchone(
            "SELECT submission_id, decision, decision_reason, decided_at "
            "FROM repo_assessor_state WHERE submission_id = %s",
            (submission_id,),
        )
        if row is None or row[1] is None:
            return None
        return DecisionRecord(
            submission_id=row[0],
            decision=Decision(row[1]),
            reason=row[2],
            at=_as_utc_dt(row[3]),
        )

    def set_shadow_mapping(self, source_id: str, shadow_id: str) -> None:
        self._require_open()
        self._exec(
            "INSERT INTO repo_assessor_state (submission_id, shadow_submission_id) "
            "VALUES (%s, %s) "
            "ON CONFLICT (submission_id) DO UPDATE SET "
            "  shadow_submission_id = EXCLUDED.shadow_submission_id",
            (source_id, shadow_id),
        )

    def get_shadow_mapping(self, source_id: str) -> ShadowMapping | None:
        row = self._fetchone(
            "SELECT submission_id, shadow_submission_id FROM repo_assessor_state "
            "WHERE submission_id = %s AND shadow_submission_id IS NOT NULL",
            (source_id,),
        )
        if row is None:
            return None
        return ShadowMapping(
            source_submission_id=row[0],
            shadow_submission_id=row[1],
            at=dt.datetime.fromtimestamp(0, tz=dt.timezone.utc),
        )

    def prune(self, older_than: dt.timedelta) -> int:
        self._require_open()
        cutoff = dt.datetime.now(dt.timezone.utc) - older_than
        cur = self._exec(
            "DELETE FROM repo_assessor_state "
            "WHERE shadow_submission_id IS NULL "
            "  AND (decided_at IS NULL OR decided_at < %s) "
            "  AND (in_flight IS NULL OR (in_flight->>'started_at')::timestamptz < %s)",
            (cutoff, cutoff),
        )
        return int(cur.rowcount or 0)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._conn.close()
            except Exception:
                pass

    def _require_open(self) -> None:
        if self._closed:
            raise StateError("state store is closed")

    def _exec(self, sql: str, params: tuple) -> Any:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur
        except Exception as e:
            raise StateError(f"postgres write failed: {e}") from e

    def _fetchone(self, sql: str, params: tuple) -> tuple | None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        except Exception as e:
            raise StateError(f"postgres read failed: {e}") from e

    def _fetchall(self, sql: str, params: tuple) -> list[tuple]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except Exception as e:
            raise StateError(f"postgres read failed: {e}") from e


def _to_jsonb(payload: dict) -> str:
    import json
    return json.dumps(payload)


def _parse_dt(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _as_utc_dt(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)
    if isinstance(value, str):
        return _parse_dt(value)
    raise StateError(f"unexpected datetime type: {type(value).__name__}")
