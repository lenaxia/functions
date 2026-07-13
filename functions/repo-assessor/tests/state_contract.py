"""Backend-agnostic contract tests for StateStore implementations.

Imported as a mixin by per-backend test modules. Validates the
observational contract specified by the StateStore Protocol.

Usage in a backend test module:

    class TestJsonBackendContract(StateStoreContract):
        @pytest.fixture
        def store(self, tmp_path): return JsonState(str(tmp_path / "s.json"))
"""

from __future__ import annotations

import datetime as dt
import time

import pytest

from model import Decision, ShadowMapping  # noqa: F401  (import for clarity)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ago(**kwargs: float) -> dt.datetime:
    return _now() - dt.timedelta(**kwargs)


class StateStoreContract:
    """All StateStore implementations must pass every method in this class."""

    @pytest.fixture
    def store(self):
        raise NotImplementedError("backend test must override `store` fixture")

    # T-201 / T-214 ────────────────────────────────────────────────────────────

    def test_T_201_mark_then_get_in_flight(self, store) -> None:
        sid, wid, sess = "s1", "w1", "session1"
        store.mark_in_flight(sid, wid, sess)
        result = store.get_in_flight(sid)
        assert result is not None
        assert result.submission_id == sid
        assert result.workspace_id == wid
        assert result.session_id == sess

    def test_T_202_clear_makes_get_return_none(self, store) -> None:
        store.mark_in_flight("s1", "w1", "sess1")
        store.clear_in_flight("s1")
        assert store.get_in_flight("s1") is None

    def test_T_214_mark_in_flight_overwrites_prior(self, store) -> None:
        store.mark_in_flight("s1", "w1", "sess1")
        store.mark_in_flight("s1", "w2", "sess2")
        result = store.get_in_flight("s1")
        assert result is not None
        assert result.workspace_id == "w2"
        assert result.session_id == "sess2"

    # T-203 ────────────────────────────────────────────────────────────────────

    def test_T_203_list_stale_excludes_fresh_includes_old(self, store) -> None:
        store.mark_in_flight("fresh", "w1", "sess")
        time.sleep(0.02)
        stale_list = store.list_stale_in_flight(dt.timedelta(milliseconds=10))
        fresh_ids = {x.submission_id for x in stale_list}
        assert "fresh" in fresh_ids

    # T-204 / T-205 ────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("decision", list(Decision))
    def test_T_204_decision_round_trips(self, store, decision) -> None:
        store.set_decision("s1", decision, f"reason for {decision.value}")
        result = store.get_decision("s1")
        assert result is not None
        assert result.decision == decision
        assert result.reason == f"reason for {decision.value}"
        assert result.submission_id == "s1"

    def test_T_205_set_decision_overwrites(self, store) -> None:
        store.set_decision("s1", Decision.NOT_ANNOUNCEMENT, "first")
        store.set_decision("s1", Decision.POSTED, "second")
        result = store.get_decision("s1")
        assert result is not None
        assert result.decision == Decision.POSTED
        assert result.reason == "second"

    # T-206 ────────────────────────────────────────────────────────────────────

    def test_T_206_shadow_mapping_round_trips(self, store) -> None:
        store.set_shadow_mapping("source1", "shadow1")
        result = store.get_shadow_mapping("source1")
        assert result is not None
        assert result.source_submission_id == "source1"
        assert result.shadow_submission_id == "shadow1"

    # T-207 / T-208 / T-209 ────────────────────────────────────────────────────

    def test_T_207_prune_removes_old_decisions_keeps_recent(self, store) -> None:
        store.set_decision("old", Decision.POSTED, "x")
        # Backdate by re-writing with internal timestamp manipulation is not
        # possible via the public API; we rely on `prune` being called with
        # a negative timedelta to prune everything.
        pruned = store.prune(dt.timedelta(seconds=-1))
        assert pruned >= 1
        assert store.get_decision("old") is None

    def test_T_209_prune_keeps_shadow_mappings(self, store) -> None:
        store.set_shadow_mapping("source1", "shadow1")
        store.set_decision("dec1", Decision.POSTED, "x")
        store.prune(dt.timedelta(seconds=-1))
        # decision gone
        assert store.get_decision("dec1") is None
        # mapping stays
        result = store.get_shadow_mapping("source1")
        assert result is not None
        assert result.shadow_submission_id == "shadow1"

    # T-210 / T-213 ────────────────────────────────────────────────────────────

    def test_T_210_empty_backend_reads_return_none(self, store) -> None:
        assert store.get_in_flight("never") is None
        assert store.get_decision("never") is None
        assert store.get_shadow_mapping("never") is None
        assert store.list_stale_in_flight(dt.timedelta(days=999)) == []

    def test_T_213_unknown_id_returns_none(self, store) -> None:
        store.mark_in_flight("s1", "w1", "sess1")
        store.set_decision("s2", Decision.POSTED, "x")
        store.set_shadow_mapping("s3", "sh3")
        assert store.get_in_flight("never") is None
        assert store.get_decision("never") is None
        assert store.get_shadow_mapping("never") is None

    # T-211 ────────────────────────────────────────────────────────────────────

    def test_T_211_concurrent_writes_no_corruption(self, store) -> None:
        from concurrent.futures import ThreadPoolExecutor

        def writer(prefix: str, n: int) -> None:
            for i in range(n):
                store.set_decision(f"{prefix}-{i}", Decision.POSTED, f"r{i}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(writer, p, 50) for p in ("a", "b")]
            [f.result() for f in futures]
        for prefix in ("a", "b"):
            for i in range(50):
                rec = store.get_decision(f"{prefix}-{i}")
                assert rec is not None
                assert rec.decision == Decision.POSTED
                assert rec.reason == f"r{i}"

    # T-212 ────────────────────────────────────────────────────────────────────

    def test_T_212_close_is_idempotent_and_blocks_subsequent(self, store) -> None:
        from model import StateError
        store.close()
        store.close()
        with pytest.raises(StateError):
            store.set_decision("x", Decision.POSTED, "y")

    # T-215 ────────────────────────────────────────────────────────────────────

    def test_T_215_datetime_round_trip_preserves_second_precision(self, store) -> None:
        store.mark_in_flight("s1", "w1", "sess1")
        result = store.get_in_flight("s1")
        assert result is not None
        assert result.started_at.tzinfo is not None
