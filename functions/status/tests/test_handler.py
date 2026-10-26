import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "status"))


class TestStatus:
    def _run(self, monkeypatch, db=None):
        import main as st
        from conftest import FakeDB as _F
        db = db or _F()
        monkeypatch.setattr(st, "TradingDB", lambda: db)
        return st.main(), db

    def test_empty_db_shape(self, monkeypatch):
        result, _ = self._run(monkeypatch)
        assert result["ok"] is True
        assert result["system"]["halt"]["halted"] is False
        assert result["book"]["positions"] == 0
        assert result["book"]["cash"] is None
        assert result["steps"] == [] and result["recent_events"] == []

    def test_halt_state_from_events(self, monkeypatch):
        result, _ = self._run(monkeypatch)
        # FakeDB.add_event only appends; halt_state queries via db.one —
        # FakeDB lacks one(): patch minimal behaviour instead
        import main as st
        from conftest import FakeDB
        db = FakeDB()

        class OneOf:
            def __call__(self, sql, params=()):
                if "halt" in sql:
                    return ("halt_set",)
                return None

        db.one = OneOf()
        result, _ = self._run(monkeypatch, db)
        assert result["system"]["halt"] == {"halted": True, "gates_open": False}

    def test_book_and_equity(self, monkeypatch):
        from conftest import FakeDB
        db = FakeDB()
        db.positions_state = {"AAPL": {"qty": 10}, "MSFT": {"qty": 0}}
        db.record_decision("1970-01-01", "book_state", {"cash": 100000.0})
        db.upsert_equity("control", "2026-10-23", 98449.93)
        db.upsert_equity("fwd:SPY", "2026-10-23", 99263.33)
        result, _ = self._run(monkeypatch, db)
        assert result["book"]["positions"] == 1
        assert result["book"]["symbols"] == ["AAPL"]
        assert result["book"]["cash"] == 100000.0
        assert result["equity"]["control"]["equity"] == 98449.93
        assert result["equity"]["fwd:SPY"]["as_of"] == "2026-10-23"
