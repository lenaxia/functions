"""JSON-file StateStore implementation.

Single-writer model (Fission deployment guarantees concurrency=1).
Atomic writes via temp-file + os.rename in the same directory.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path

from model import (
    Decision,
    DecisionRecord,
    InFlight,
    ShadowMapping,
    StateError,
)


class JsonState:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._closed = False
        self._state = self._read_or_init()

    def mark_in_flight(self, submission_id: str, workspace_id: str, session_id: str) -> None:
        self._require_open()
        self._state["in_flight"][submission_id] = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self._write_payload(self._state)

    def clear_in_flight(self, submission_id: str) -> None:
        self._require_open()
        if submission_id in self._state["in_flight"]:
            del self._state["in_flight"][submission_id]
            self._write_payload(self._state)

    def get_in_flight(self, submission_id: str) -> InFlight | None:
        record = self._state["in_flight"].get(submission_id)
        if record is None:
            return None
        return InFlight(
            submission_id=submission_id,
            workspace_id=record["workspace_id"],
            session_id=record["session_id"],
            started_at=dt.datetime.fromisoformat(record["started_at"]),
        )

    def list_stale_in_flight(self, older_than: dt.timedelta) -> list[InFlight]:
        cutoff = dt.datetime.now(dt.timezone.utc) - older_than
        results: list[InFlight] = []
        for sid, record in self._state["in_flight"].items():
            started = dt.datetime.fromisoformat(record["started_at"])
            if started < cutoff:
                results.append(InFlight(sid, record["workspace_id"], record["session_id"], started))
        return results

    def set_decision(self, submission_id: str, decision: Decision, reason: str) -> None:
        self._require_open()
        self._state["decisions"][submission_id] = {
            "decision": decision.value,
            "reason": reason,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self._write_payload(self._state)

    def get_decision(self, submission_id: str) -> DecisionRecord | None:
        record = self._state["decisions"].get(submission_id)
        if record is None:
            return None
        return DecisionRecord(
            submission_id=submission_id,
            decision=Decision(record["decision"]),
            reason=record["reason"],
            at=dt.datetime.fromisoformat(record["at"]),
        )

    def set_shadow_mapping(self, source_id: str, shadow_id: str) -> None:
        self._require_open()
        self._state["shadow_mappings"][source_id] = {
            "shadow_submission_id": shadow_id,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self._write_payload(self._state)

    def get_shadow_mapping(self, source_id: str) -> ShadowMapping | None:
        record = self._state["shadow_mappings"].get(source_id)
        if record is None:
            return None
        return ShadowMapping(
            source_submission_id=source_id,
            shadow_submission_id=record["shadow_submission_id"],
            at=dt.datetime.fromisoformat(record["at"]),
        )

    def prune(self, older_than: dt.timedelta) -> int:
        self._require_open()
        cutoff = dt.datetime.now(dt.timezone.utc) - older_than
        pruned = 0

        to_remove_decisions = []
        for sid, record in self._state["decisions"].items():
            if dt.datetime.fromisoformat(record["at"]) < cutoff:
                to_remove_decisions.append(sid)
        for sid in to_remove_decisions:
            del self._state["decisions"][sid]
            pruned += 1

        to_remove_inflight = []
        for sid, record in self._state["in_flight"].items():
            if dt.datetime.fromisoformat(record["started_at"]) < cutoff:
                to_remove_inflight.append(sid)
        for sid in to_remove_inflight:
            del self._state["in_flight"][sid]
            pruned += 1

        if pruned > 0:
            self._write_payload(self._state)
        return pruned

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise StateError("state store is closed")

    def _read_or_init(self) -> dict:
        if not self._path.exists():
            return {"in_flight": {}, "decisions": {}, "shadow_mappings": {}}
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError as e:
            raise StateError(f"corrupt state file {self._path}: {e}") from e
        data.setdefault("in_flight", {})
        data.setdefault("decisions", {})
        data.setdefault("shadow_mappings", {})
        return data

    def _write_payload(self, payload: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".state.json.tmp.", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(payload, f, indent=None, separators=(",", ":"))
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
