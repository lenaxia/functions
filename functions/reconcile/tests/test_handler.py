import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "reconcile"))


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestReconcile:
    def _run(self, monkeypatch, db_positions=None, db_cash=100000.0,
             acct=None, acct_error=None, done_steps=(), weekday=0):
        import datetime as dt
        import main as rec
        from conftest import FakeDB
        db = FakeDB()
        db.positions_state = db_positions or {}
        db.record_decision("1970-01-01", "book_state", {"cash": db_cash})
        for step in done_steps:
            db.steps.add((rec.utc_today(), step))
        monkeypatch.setattr(rec, "TradingDB", lambda: db)
        monkeypatch.setattr(rec.alpaca, "Alpaca", lambda: object())

        def fake_state(api):
            if acct_error:
                return {"ok": False, "error": acct_error}
            return acct

        monkeypatch.setattr(rec.alpaca, "account_state", fake_state)
        monkeypatch.setattr(rec, "alert", lambda *a, **k: True)

        monkeypatch.setattr(rec, "datetime", type("D", (dt.datetime,), {
            "now": classmethod(lambda cls, tz=None: dt.datetime(
                2026, 10, 26, 23, 0, tzinfo=tz))}))

        return rec.main(), db

    def test_clean_reconcile(self, monkeypatch):
        pos = {"AAPL": {"qty": 10}, "MSFT": {"qty": 2}}
        acct = {"ok": True, "held": {"AAPL": 10.0, "MSFT": 2.0},
                "cash": 100000.0, "equity": 130000.0, "open": True}
        result, db = self._run(monkeypatch, pos, 100000.0, acct,
                               done_steps=("debate", "decide", "execute", "score"))
        assert result["all_good"] is True
        assert result["checks"]["drift"]["ok"] is True
        assert result["checks"]["schedule"]["ok"] is True
        assert db.events[-1][0] == "reconcile"

    def test_qty_drift_alerts(self, monkeypatch):
        pos = {"AAPL": {"qty": 10}}
        acct = {"ok": True, "held": {"AAPL": 4.0}, "cash": 100000.0,
                "equity": 90000.0, "open": True}
        result, _ = self._run(monkeypatch, pos, 100000.0, acct,
                              done_steps=("debate", "decide", "execute", "score"))
        assert result["checks"]["drift"]["ok"] is False
        assert result["checks"]["drift"]["qty_drift"]["AAPL"] == {"db": 10.0, "alpaca": 4.0}
        assert result["all_good"] is False

    def test_cash_drift_alerts(self, monkeypatch):
        acct = {"ok": True, "held": {}, "cash": 99000.0,
                "equity": 99000.0, "open": True}
        result, _ = self._run(monkeypatch, {}, 100000.0, acct,
                              done_steps=("debate", "decide", "execute", "score"))
        assert result["checks"]["drift"]["cash_drift"] is True
        assert result["all_good"] is False

    def test_broker_unreachable(self, monkeypatch):
        result, _ = self._run(monkeypatch, {}, 100000.0, None,
                              acct_error="boom",
                              done_steps=("debate", "decide", "execute", "score"))
        assert result["checks"]["broker"]["ok"] is False
        assert "drift" not in result["checks"]
        assert result["all_good"] is False

    def test_schedule_gap(self, monkeypatch):
        acct = {"ok": True, "held": {}, "cash": 100000.0,
                "equity": 100000.0, "open": True}
        result, _ = self._run(monkeypatch, {}, 100000.0, acct, done_steps=())
        assert result["checks"]["schedule"]["ok"] is False
        assert set(result["checks"]["schedule"]["missing"]) == {
            "debate", "decide", "execute", "score"}
        assert result["all_good"] is False
