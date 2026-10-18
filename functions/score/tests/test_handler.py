import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "score"))


class TestControlPort:
    def test_sleeve_weights_math(self):
        from trading_common.control import sleeve_weights
        weights, cash = sleeve_weights({"weights_pct": {"VOO": 90.0, "CASH": 10.0}})
        assert cash == 0.1
        assert weights == {"VOO": 1.0}

    def test_control_idempotent_per_day(self, monkeypatch):
        import datetime as dt
        from trading_common import control
        st = {"shares": {"VOO": 100}, "anchor_date": "2026-09-29",
              "cash_pct": 0.1, "history": [
                  {"date": "2026-10-16", "val_date": "2026-10-15", "equity": 101000.0}]}
        res, st2 = control.run(
            now=dt.datetime(2026, 10, 16, 23, 0, tzinfo=dt.timezone.utc), st=st)
        assert res["already_recorded"] and len(st2["history"]) == 1

    def test_control_weekend_skip(self):
        import datetime as dt
        from trading_common import control
        res, _ = control.run(now=dt.datetime(2026, 10, 17, 23, 0, tzinfo=dt.timezone.utc),
                             st={"shares": {}, "history": []})
        assert res.get("skipped") == "weekend"

    def test_control_marks_and_anchors(self, monkeypatch):
        import datetime as dt
        from trading_common import control
        closes = {"VOO": {"2026-10-16": 500.0}}
        monkeypatch.setattr(control, "fetch_closes", lambda syms: closes)
        sleeve = {"weights_pct": {"VOO": 90.0, "CASH": 10.0}}
        res, st = control.run(
            now=dt.datetime(2026, 10, 16, 23, 0, tzinfo=dt.timezone.utc),
            sleeve=sleeve, st={"shares": {}, "anchor_date": None, "history": []})
        assert res["ok"]
        assert st["anchor_date"] == "2026-10-16"
        assert len(st["history"]) == 1
        # day-0: cash 10k + VOO shares worth exactly 90k
        assert st["history"][0]["equity"] == 100000.0 or res["equity"] == 100000.0


class TestScoreHandler:
    def test_load_sleeve_falls_back_to_db(self, monkeypatch):
        import main as score
        from conftest import FakeDB
        db = FakeDB()
        monkeypatch.setattr(score.gitstate, "read_json", lambda p: None)
        monkeypatch.setattr(score, "TradingDB", lambda: db)
        db.record_decision("1970-01-01", "sleeve_weights", {"weights_pct": {"VOO": 100.0}})
        sleeve = score.load_sleeve(db)
        assert sleeve == {"weights_pct": {"VOO": 100.0}}

    def test_score_idempotent(self, monkeypatch):
        import main as score
        from conftest import FakeDB
        from trading_common.db import utc_today
        db = FakeDB()
        db.steps.add((utc_today(), "score"))
        monkeypatch.setattr(score, "TradingDB", lambda: db)
        out = score.main()
        assert out["already_done"] and out["ok"]
