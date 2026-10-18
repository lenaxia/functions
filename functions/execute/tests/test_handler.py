import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))


def _caps(pilot=135000.0):
    return {
        "pilot_notional": pilot,
        "max_notional_per_order": pilot * 0.30,
        "max_book_notional": pilot * 1.04,
        "equity_kill_line": pilot * 0.85,
    }


class TestAlpacaPort:
    def test_pilot_caps_math(self, monkeypatch):
        from trading_common import alpaca
        monkeypatch.setattr(alpaca, "secret",
                            lambda name, default="": "200000"
                            if name == "PILOT_NOTIONAL" else default)
        caps = alpaca.pilot_caps()
        assert caps["max_notional_per_order"] == 60000.0   # 30%
        assert caps["max_book_notional"] == 208000.0       # 104%
        assert caps["equity_kill_line"] == 170000.0        # 85%

    def test_diffs_sells_first(self):
        from trading_common.alpaca import diffs
        d = diffs({"NVDA": 10, "T": 0}, {"AMD": 5, "T": 3})
        assert [x["action"] for x in d] == ["sell", "sell", "buy"]
        assert d[0]["symbol"] == "AMD" and d[1]["symbol"] == "T"
        assert d[2] == {"symbol": "NVDA", "action": "buy", "qty": 10}

    def test_bracket_body_verbatim(self):
        from trading_common.alpaca import bracket_body
        body = {"symbol": "NVDA", "qty": 5, "side": "buy"}
        out = bracket_body(dict(body), "NVDA", 100.0)
        assert out["order_class"] == "bracket"
        assert out["stop_loss"]["stop_price"] == 95.0     # -5%
        assert out["take_profit"]["limit_price"] == 115.0  # +15%
        out2 = bracket_body({"side": "sell"}, "X", 100.0)
        assert "order_class" not in out2                   # sells stay plain
        out3 = bracket_body({"side": "buy"}, "X", None)
        assert "order_class" not in out3                   # no ref price -> no bracket

    def test_evaluate_caps_per_order_reject(self):
        from trading_common.alpaca import evaluate_caps
        caps = _caps(100000.0)
        orders, rej = evaluate_caps(
            [{"symbol": "A", "action": "buy", "qty": 100}], {"A": 500.0}, 0.0, caps)
        # notional 50000 > 30% cap
        assert orders == [] and rej[0]["reason"].startswith("per-order cap")

    def test_evaluate_caps_book_cap_and_ordering(self):
        from trading_common.alpaca import evaluate_caps
        caps = _caps(100000.0)
        diffs = [{"symbol": "A", "action": "buy", "qty": 50},   # 5000 notional
                 {"symbol": "B", "action": "sell", "qty": 10}]
        orders, rej = evaluate_caps(diffs, {"A": 100.0, "B": 50.0}, 0.0, caps)
        assert [o["side"] for o in orders] == ["sell", "buy"]   # sells first
        assert orders[1]["notional"] == 5000.0

    def test_evaluate_caps_projected_book_rejects_late_buy(self):
        from trading_common.alpaca import evaluate_caps
        caps = _caps(100000.0)  # book cap 104000
        diffs = [{"symbol": "A", "action": "buy", "qty": 250},   # 25k notional
                 {"symbol": "B", "action": "buy", "qty": 250}]   # another 25k
        orders, rej = evaluate_caps(diffs, {"A": 100.0, "B": 100.0}, 70000.0, caps)
        assert len(orders) == 1
        assert rej and "total notional cap" in rej[0]["reason"]


class TestExecuteHandler:
    def test_target_book_applies_plan(self, monkeypatch):
        sys.path.insert(0, str(ROOT / "functions" / "execute"))
        import main as execute
        from conftest import FakeDB
        db = FakeDB()
        db.set_positions({"NVDA": {"qty": 10, "entry": 100.0}, "T": {"qty": 5, "entry": 50.0}})
        plan = {"exits": [{"symbol": "T", "quantity": 5}],
                "entries": [{"symbol": "AMD", "quantity": 20}]}
        target = execute.target_book(db, plan)
        assert target == {"NVDA": 10.0, "AMD": 20.0}

    def test_shadow_logs_without_placing(self, monkeypatch):
        import main as execute
        # regression guard: this suite must import functions/execute/main.py,
        # never a sibling function's module (conftest sys.path copy-paste bug)
        assert execute.__file__ == str(ROOT / "functions" / "execute" / "main.py")
        from trading_common import alpaca as ap
        from conftest import FakeDB

        db = FakeDB()
        monkeypatch.setattr(execute, "TradingDB", lambda: db)
        # decide decision present
        db.record_decision("2100-01-01", "decide",
                           {"ok": True, "date": "2100-01-01", "market_open": True,
                            "prices": {"AMD": 100.0},
                            "exits": [],
                            "entries": [{"symbol": "AMD", "quantity": 20}]},
                           status="ok")
        # freeze date
        import datetime as dt
        class Fixed(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime(2100, 1, 1, 15, 0, tzinfo=dt.timezone.utc)
        monkeypatch.setattr(execute, "datetime", Fixed)
        # no halt
        monkeypatch.setattr(execute, "check_halt", lambda d: {"halted": False})
        # alpaca: empty account, one diff (target AMD 20, held 0)
        monkeypatch.setattr(ap, "Alpaca", lambda: None)
        monkeypatch.setattr(ap, "account_state", lambda api: {
            "ok": True, "open": True, "equity": 500000.0, "cash": 100000.0,
            "held": {}, "held_value": 0.0})
        monkeypatch.setattr(execute.ap, "pilot_caps", _caps)
        out = execute.main()
        assert out["ok"] and out["mode"] == "shadow"
        assert out["note"].startswith("shadow: paper-read-only")
        assert len(out["orders"]) == 1 and out["orders"][0]["side"] == "buy"
        assert all(f[1]["shadow"] for f in db.fills)  # shadow fills only
        assert ("2100-01-01", "execute") in db.steps
