import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))


class TestGates:
    def test_veto_only_from_today(self):
        from trading_common.gates import dd_vetoes
        debate = {"date": "2026-10-19", "verdicts": [
            {"symbol": "NVDA", "verdict": "veto"},
            {"symbol": "AMD", "verdict": "pass"},
            {"symbol": "T", "verdict": "abstain"},
        ]}
        assert set(dd_vetoes(debate, "2026-10-19")) == {"NVDA"}
        assert dd_vetoes(debate, "2026-10-20") == {}   # stale = no vetoes
        assert dd_vetoes(None, "2026-10-19") == {}      # missing = no vetoes

    def test_headline_risk_pattern(self):
        from trading_common.gates import headline_risk
        assert headline_risk([]) == "headline screen unavailable"
        assert headline_risk(["_news_fetch_error: boom"]) == "headline screen unavailable"
        assert headline_risk(["NVDA rises on earnings beat"]) is None
        assert "risk headline" in headline_risk(["SEC opens probe into NVDA"])

    def test_apply_gates_exits_before_entries(self):
        from trading_common.gates import apply_gates
        decision = {
            "today": "2026-10-19", "market_open": True,
            "exits": [{"action": "sell", "symbol": "T", "quantity": 5, "reason": "stop loss"}],
            "entries": [{"action": "buy", "symbol": "NVDA", "quantity": 3, "reason": "momentum",
                         "headlines": ["ok"]}],
        }
        exits, entries, log = apply_gates(decision, None, 0)
        assert [e["symbol"] for e in exits] == ["T"]
        assert [e["symbol"] for e in entries] == ["NVDA"]
        tags = [g["tag"] for g in log]
        assert tags == ["TRADED", "TRADED"]

    def test_apply_gates_veto_blocks_buy(self):
        from trading_common.gates import apply_gates
        decision = {
            "today": "2026-10-19", "market_open": True, "exits": [],
            "entries": [{"action": "buy", "symbol": "NVDA", "quantity": 3,
                         "reason": "r", "headlines": ["fine"]}],
        }
        debate = {"date": "2026-10-19",
                  "verdicts": [{"symbol": "NVDA", "verdict": "veto",
                                "thesis_excerpt": "overextended"}]}
        exits, entries, log = apply_gates(decision, debate, 0)
        assert entries == []
        assert any(g["tag"] == "DD_VETO" for g in log)

    def test_apply_gates_market_closed_skips_buys(self):
        from trading_common.gates import apply_gates
        decision = {
            "today": "2026-10-19", "market_open": False, "exits": [],
            "entries": [{"action": "buy", "symbol": "NVDA", "quantity": 3,
                         "reason": "r", "headlines": ["fine"]}],
        }
        _, entries, log = apply_gates(decision, None, 0)
        assert entries == []
        assert any("market closed" in g["line"] for g in log)

    def test_apply_gates_max_trades_per_day(self):
        from trading_common.gates import apply_gates
        decision = {
            "today": "2026-10-19", "market_open": True,
            "exits": [{"action": "sell", "symbol": "A", "quantity": 1, "reason": "r"},
                      {"action": "sell", "symbol": "B", "quantity": 1, "reason": "r"}],
            "entries": [{"action": "buy", "symbol": "C", "quantity": 1, "reason": "r",
                         "headlines": ["fine"]}],
        }
        exits, entries, log = apply_gates(decision, None, 2)  # cap already reached
        assert exits == [] and entries == []
        assert all(g["tag"] != "TRADED" for g in log)


class TestTradeRules:
    def test_entry_rule_and_sizing(self, monkeypatch):
        from trading_common import trade_rules
        monkeypatch.setattr(trade_rules, "headlines", lambda q, limit=5: ["h1"])
        snap = {"stocks": [
            {"sym": "SPY", "price": 500.0, "chg_1d_pct": 0.0,
             "week_52_high": 510.0, "week_52_low": 400.0},
            {"sym": "NVDA", "price": 100.0, "chg_1d_pct": 5.0,
             "week_52_high": 103.0, "week_52_low": 40.0},   # RS +5, within 3% of high
            {"sym": "DOG", "price": 50.0, "chg_1d_pct": -2.0,
             "week_52_high": 51.0, "week_52_low": 10.0},    # negative 1d change
        ]}
        book = {"cash": 100000.0, "positions": {}}
        import datetime as dt
        d = trade_rules.build_decision(
            book, snap, now=dt.datetime(2026, 10, 19, 15, 0, tzinfo=dt.timezone.utc))
        assert [e["symbol"] for e in d["entries"]] == ["NVDA"]
        assert d["entries"][0]["quantity"] == 100  # 10% of 100k equity at $100

    def test_exit_rules(self, monkeypatch):
        from trading_common import trade_rules
        monkeypatch.setattr(trade_rules, "headlines", lambda q, limit=5: [])
        import datetime as dt
        snap = {"stocks": [
            {"sym": "SPY", "price": 500.0, "chg_1d_pct": 0.0,
             "week_52_high": 510.0, "week_52_low": 400.0},
            {"sym": "AAA", "price": 90.0, "chg_1d_pct": 0.0,
             "week_52_high": 200.0, "week_52_low": 50.0},
            {"sym": "BBB", "price": 120.0, "chg_1d_pct": 0.0,
             "week_52_high": 200.0, "week_52_low": 50.0},
        ]}
        book = {"cash": 0.0, "positions": {
            "AAA": {"qty": 10, "entry": 100.0},   # -10% -> stop
            "BBB": {"qty": 10, "entry": 100.0},   # +20% -> target
        }}
        d = trade_rules.build_decision(
            book, snap, now=dt.datetime(2026, 10, 19, 15, 0, tzinfo=dt.timezone.utc))
        syms = {e["symbol"]: e["reason"] for e in d["exits"]}
        assert "AAA" in syms and "stop loss" in syms["AAA"]
        assert "BBB" in syms and "take profit" in syms["BBB"]

    def test_market_open_window(self):
        from trading_common import trade_rules
        import datetime as dt
        utc = dt.timezone.utc
        assert trade_rules.market_open(dt.datetime(2026, 10, 19, 14, 0, tzinfo=utc))
        assert not trade_rules.market_open(dt.datetime(2026, 10, 19, 12, 0, tzinfo=utc))
        assert not trade_rules.market_open(dt.datetime(2026, 10, 17, 14, 0, tzinfo=utc))  # Sat


class TestDecideHandler:
    def test_exec_returns_rowcount_not_cursor(self):
        # regression: exec() returned the cursor AFTER its with-block closed
        # it — the first caller to use the return value would fail
        from trading_common.db import TradingDB

        class StubCursor:
            closed = False
            rowcount = 3

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.closed = True
                return False

            def execute(self, sql, params=()):
                pass

        class StubConn:
            def cursor(self):
                return StubCursor()

        db = TradingDB.__new__(TradingDB)
        db._conn = StubConn()
        db._closed = False
        assert db.exec("INSERT INTO events VALUES (1, 't', '{}')") == 3

    def test_database_url_quotes_special_chars(self, monkeypatch):
        # regression: raw URL interpolation broke conninfo parsing for any
        # password with @ : / + = or whitespace (openssl rand -base64 alphabet)
        from trading_common import db as dbmod

        password = "aB3+/=xY z@w:9"
        monkeypatch.setenv("PGUSER", "trading")
        monkeypatch.setenv("PGPASSWORD", password)
        monkeypatch.setattr(dbmod, "secret",
                            lambda name, default="": {"PGUSER": "trading",
                                                      "PGPASSWORD": password}.get(name, default))
        url = dbmod.database_url()
        assert password not in url  # never the raw bytes in the URL
        from psycopg.conninfo import conninfo_to_dict
        ci = conninfo_to_dict(url)
        assert ci["user"] == "trading"
        assert ci["password"] == password  # round-trips through psycopg

    def test_main_records_prices_for_book_and_plan(self, monkeypatch):
        # regression: payload["prices"] held only plan symbols, so execute's
        # cap math valued non-plan book symbols at notional 0 (cap bypass)
        sys.path.insert(0, str(ROOT / "functions" / "decide"))
        import main as decide
        from conftest import FakeDB

        db = FakeDB()
        monkeypatch.setattr(decide, "TradingDB", lambda: db)
        monkeypatch.setattr(decide.gitstate, "read_json",
                            lambda p: {"cash": 25000.0,
                                       "positions": {"NVDA": {"qty": 5, "entry": 200.0},
                                                     "HELD": {"qty": 2, "entry": 50.0}}}
                            if p == "state/book.json" else None)
        monkeypatch.setattr(decide.marketdata, "snapshot", lambda: {
            "stocks": [{"sym": "NVDA", "price": 210.0},
                       {"sym": "HELD", "price": 51.0},
                       {"sym": "AMD", "price": 100.0},
                       {"sym": "SPY", "price": 500.0}]})
        monkeypatch.setattr(
            decide.trade_rules, "build_decision",
            lambda book, snap, now=None: {"market_open": True, "account": {"cash": book["cash"]},
                                          "exits": [], "entries": []})
        monkeypatch.setattr(
            decide, "trading_gates",
            lambda dec, debate, trades: (
                [{"symbol": "NVDA", "quantity": 5}],
                [{"symbol": "AMD", "quantity": 10}], []))
        journaled = []
        monkeypatch.setattr(decide.gitstate, "journal",
                            lambda date, step, payload: journaled.append((step, payload)))

        out = decide.main()
        assert out["ok"] and out["debate_fallback"] is True
        # book symbols (incl. non-plan HELD) AND plan symbols are priced
        assert out["prices"] == {"AMD": 100.0, "HELD": 51.0, "NVDA": 210.0}
        # idempotent: re-run skips, decision payload persisted as recorded
        assert decide.main()["already_done"] is True
        row = db.get_decision(out["date"], "decide")
        assert row["status"] == "ok" and row["payload"]["prices"] == out["prices"]
        assert journaled and journaled[0][0] == "decide"

    def test_shadow_sync_and_fallback(self, monkeypatch):
        sys.path.insert(0, str(ROOT / "functions" / "decide"))
        import main as decide
        from conftest import FakeDB
        import trading_common.db as dbmod

        db = FakeDB()
        monkeypatch.setattr(decide, "TradingDB", lambda: db)
        monkeypatch.setattr(decide.gitstate, "read_json",
                            lambda p: {"cash": 25000.0,
                                       "positions": {"NVDA": {"qty": 5, "entry": 200.0}}}
                            if p == "state/book.json" else None)
        book = decide.sync_book_from_git(db)
        assert book["cash"] == 25000.0
        assert db.positions()["NVDA"]["qty"] == 5
        row = db.get_decision("1970-01-01", "book_state")
        assert row["payload"]["cash"] == 25000.0
