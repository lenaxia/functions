"""SQLite StateStore implementation.

WAL mode for concurrent-read/single-write safety; embedded single file.
Schema created idempotently on init. Single-writer model is enforced by
Fission deployment (concurrency=1); an internal threading.Lock prevents
database corruption if two threads of the same process race.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path

from model import (
    Decision,
    DecisionRecord,
    InFlight,
    ShadowMapping,
    StateError,
)


_BUSY_TIMEOUT = 5000


class SqliteState:
    def __init__(self, path: str, *, busy_timeout_ms: int = _BUSY_TIMEOUT) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(
                str(self._path),
                timeout=busy_timeout_ms / 1000.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._init_schema()
        except sqlite3.OperationalError as e:
            raise StateError(f"sqlite open/init failed for {self._path}: {e}") from e

    def mark_in_flight(self, submission_id: str, workspace_id: str, session_id: str) -> None:
        self._require_open()
        self._exec_writes(
            "INSERT INTO in_flight (submission_id, workspace_id, session_id, started_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(submission_id) DO UPDATE SET "
            "  workspace_id = excluded.workspace_id, "
            "  session_id = excluded.session_id, "
            "  started_at = excluded.started_at",
            (
                submission_id,
                workspace_id,
                session_id,
                dt.datetime.now(dt.timezone.utc).isoformat(),
            ),
        )

    def clear_in_flight(self, submission_id: str) -> None:
        self._require_open()
        self._exec_writes(
            "DELETE FROM in_flight WHERE submission_id = ?", (submission_id,)
        )

    def get_in_flight(self, submission_id: str) -> InFlight | None:
        row = self._conn.execute(
            "SELECT submission_id, workspace_id, session_id, started_at "
            "FROM in_flight WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        if row is None:
            return None
        return InFlight(
            submission_id=row[0],
            workspace_id=row[1],
            session_id=row[2],
            started_at=_parse_dt(row[3]),
        )

    def list_stale_in_flight(self, older_than: dt.timedelta) -> list[InFlight]:
        cutoff = (dt.datetime.now(dt.timezone.utc) - older_than).isoformat()
        rows = self._conn.execute(
            "SELECT submission_id, workspace_id, session_id, started_at "
            "FROM in_flight WHERE started_at < ? ORDER BY started_at",
            (cutoff,),
        ).fetchall()
        return [
            InFlight(
                submission_id=r[0],
                workspace_id=r[1],
                session_id=r[2],
                started_at=_parse_dt(r[3]),
            )
            for r in rows
        ]

    def set_decision(self, submission_id: str, decision: Decision, reason: str) -> None:
        self._require_open()
        self._exec_writes(
            "INSERT INTO decisions (submission_id, decision, reason, decided_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(submission_id) DO UPDATE SET "
            "  decision = excluded.decision, "
            "  reason = excluded.reason, "
            "  decided_at = excluded.decided_at",
            (
                submission_id,
                decision.value,
                reason,
                dt.datetime.now(dt.timezone.utc).isoformat(),
            ),
        )

    def get_decision(self, submission_id: str) -> DecisionRecord | None:
        row = self._conn.execute(
            "SELECT submission_id, decision, reason, decided_at "
            "FROM decisions WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        if row is None:
            return None
        return DecisionRecord(
            submission_id=row[0],
            decision=Decision(row[1]),
            reason=row[2],
            at=_parse_dt(row[3]),
        )

    def set_shadow_mapping(self, source_id: str, shadow_id: str) -> None:
        self._require_open()
        self._exec_writes(
            "INSERT INTO shadow_mappings (source_submission_id, shadow_submission_id, at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(source_submission_id) DO UPDATE SET "
            "  shadow_submission_id = excluded.shadow_submission_id, "
            "  at = excluded.at",
            (
                source_id,
                shadow_id,
                dt.datetime.now(dt.timezone.utc).isoformat(),
            ),
        )

    def get_shadow_mapping(self, source_id: str) -> ShadowMapping | None:
        row = self._conn.execute(
            "SELECT source_submission_id, shadow_submission_id, at "
            "FROM shadow_mappings WHERE source_submission_id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return ShadowMapping(
            source_submission_id=row[0],
            shadow_submission_id=row[1],
            at=_parse_dt(row[2]),
        )

    def prune(self, older_than: dt.timedelta) -> int:
        self._require_open()
        cutoff = (dt.datetime.now(dt.timezone.utc) - older_than).isoformat()
        cur1 = self._exec_writes(
            "DELETE FROM decisions WHERE decided_at < ?", (cutoff,)
        )
        cur2 = self._exec_writes(
            "DELETE FROM in_flight WHERE started_at < ?", (cutoff,)
        )
        return int(cur1.rowcount or 0) + int(cur2.rowcount or 0)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def _require_open(self) -> None:
        if self._closed:
            raise StateError("state store is closed")

    def _exec_writes(self, sql: str, params: tuple) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self._conn.execute(sql, params)
            except sqlite3.OperationalError as e:
                raise StateError(f"sqlite write failed: {e}") from e

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS in_flight (
                submission_id TEXT PRIMARY KEY,
                workspace_id  TEXT NOT NULL,
                session_id    TEXT NOT NULL,
                started_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                submission_id TEXT PRIMARY KEY,
                decision      TEXT NOT NULL,
                reason        TEXT NOT NULL,
                decided_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_mappings (
                source_submission_id  TEXT PRIMARY KEY,
                shadow_submission_id  TEXT NOT NULL,
                at                    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_in_flight_started ON in_flight (started_at);
            CREATE INDEX IF NOT EXISTS idx_decisions_at ON decisions (decided_at);
            """
        )


def _parse_dt(iso: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed
