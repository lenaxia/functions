import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "digest"))


class TestDigest:
    def test_watchdog_weekday(self):
        import main as digest
        from conftest import FakeDB
        import datetime as dt
        db = FakeDB()
        db.steps.add(("2026-10-19", "debate"))
        db.steps.add(("2026-10-19", "decide"))
        missing = digest.watchdog(db, "2026-10-19", True)
        assert missing == ["execute", "score"]

    def test_watchdog_weekend_clean(self):
        import main as digest
        from conftest import FakeDB
        db = FakeDB()
        assert digest.watchdog(db, "2026-10-17", False) == []

    def test_summary_reports_missing_and_trades(self):
        import main as digest
        from conftest import FakeDB
        db = FakeDB()
        db.record_decision("2026-10-19", "decide", {
            "ok": True, "debate_fallback": True,
            "exits": [{"symbol": "T", "quantity": 5, "reason": "stop loss: -6% <= -5%"}],
            "entries": [{"symbol": "NVDA", "quantity": 10, "est_cost": 1000.0,
                         "reason": "momentum"}],
            "gate_log": [{"tag": "DD_VETO", "line": "AMD overextended"}],
            "account": {"equity": 100000.0, "cash": 25000.0, "positions_held": 3},
        }, status="ok")
        db.record_decision("2026-10-19", "execute", {
            "mode": "shadow", "note": "shadow: paper-read-only — 1 order(s) logged, none placed",
            "orders": [{"side": "buy", "symbol": "NVDA", "qty": 10, "assumed_price": 100.0}],
            "rejections": [],
        }, status="shadow")
        text = digest.build_summary(db, "2026-10-19", ["execute", "score"])
        assert "missing steps today: execute, score" in text
        assert "fallback flag" in text
        assert "EXIT T" in text and "ENTRY NVDA" in text
        assert "DD_VETO: AMD" in text
        assert "WOULD ORDER (shadow): buy NVDA" in text
        assert "Book: equity $100000" in text

    def test_digest_idempotent(self, monkeypatch):
        import main as digest
        from conftest import FakeDB
        from trading_common.db import utc_today
        db = FakeDB()
        db.steps.add((utc_today(), "digest"))
        monkeypatch.setattr(digest, "TradingDB", lambda: db)
        out = digest.main()
        assert out["already_done"] and out["ok"]
