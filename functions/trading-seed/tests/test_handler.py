import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "trading-seed"))


class TestSeed:
    def _git(self, monkeypatch, files):
        import main as seed
        monkeypatch.setattr(seed.gitstate, "read_json",
                            lambda p: files.get(p))
        return seed

    def test_seed_full(self, monkeypatch):
        import main as seed
        from conftest import FakeDB
        db = FakeDB()
        files = {
            "state/book.json": {"cash": 25666.2, "positions": {
                "VOO": {"qty": 25, "entry": 701.08},
                "AMZN": {"qty": 0, "entry": 0.0}},
                "meta": {"origin": "test"}},
            "control_state.json": {"anchor_date": "2026-09-29", "shares": {"VOO": 90},
                                   "cash_pct": 0.027,
                                   "history": [
                                       {"date": "2026-09-29", "equity": 99994.82},
                                       {"date": "2026-09-30", "equity": 100299.98}]},
            "forward/forward_state.json": {
                "meta": {"forward_start": "2026-09-29", "start_equity": 100000,
                         "cost_bps_per_side": 5, "last_data_date": "2026-09-30",
                         "universe_traded": ["NVDA"]},
                "benchmark": {"SPY": {"series": [{"date": "2026-09-29",
                                                  "equity": 99815.84}]}},
                "variants": {"don_p10_s8": {
                    "equity_series": [{"date": "2026-09-29", "equity": 99816.57,
                                       "exposure": 1.0}]}}},
            "personal_sleeve.json": {"as_of": "2026-09-29",
                                     "weights_pct": {"VOO": 100.0}},
        }
        monkeypatch.setattr(seed.gitstate, "read_json", lambda p: files.get(p))
        monkeypatch.setattr(seed, "TradingDB", lambda: db)
        out = seed.main()
        assert out["ok"]
        assert db.positions() == {"VOO": {"qty": 25, "entry": 701.08}}  # qty>0 only
        assert db.get_decision("1970-01-01", "book_state")["payload"]["cash"] == 25666.2
        assert db.equity[("control", "2026-09-29")] == 99994.82
        assert db.equity[("control", "2026-09-30")] == 100299.98
        assert db.equity[("fwd:don_p10_s8", "2026-09-29")] == 99816.57
        assert db.equity[("fwd:SPY", "2026-09-29")] == 99815.84
        assert db.get_decision("1970-01-01", "sleeve_weights")["payload"]["as_of"] == "2026-09-29"
        assert db.events and db.events[0][0] == "seed"

    def test_seed_rerun_is_idempotent(self, monkeypatch):
        import main as seed
        from conftest import FakeDB
        db = FakeDB()
        db.upsert_equity("control", "2026-09-29", 99994.82)  # pre-existing row
        files = {"state/book.json": {"cash": 1.0, "positions": {}},
                 "control_state.json": {"history": [{"date": "2026-09-29",
                                                     "equity": 99994.82}]}}
        monkeypatch.setattr(seed.gitstate, "read_json", lambda p: files.get(p))
        monkeypatch.setattr(seed, "TradingDB", lambda: db)
        out = seed.main()
        assert out["ok"]
        assert db.equity[("control", "2026-09-29")] == 99994.82  # unchanged

    def test_seed_fails_cleanly_without_git(self, monkeypatch):
        import main as seed
        from conftest import FakeDB
        db = FakeDB()
        monkeypatch.setattr(seed.gitstate, "read_json", lambda p: None)
        monkeypatch.setattr(seed, "TradingDB", lambda: db)
        out = seed.main()
        assert out["ok"] is False
        assert "book.json" in out["error"]
