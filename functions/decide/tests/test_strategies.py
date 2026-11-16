import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))
sys.path.insert(0, str(ROOT / "functions" / "decide"))


def _book(cash=90000.0, positions=None):
    return {"cash": cash, "positions": positions or {}}


def _ctx(rets, sigmas, prices):
    stocks = {}
    for sym in rets:
        stocks[sym] = {"price": prices[sym], "ret_1d": rets[sym],
                       "sigma20": sigmas[sym]}
    return {"v228": True, "stocks": stocks}


class TestRegistry:
    def test_get_known_and_default(self):
        import strategies
        assert strategies.get("momentum").NAME == "momentum"
        assert strategies.get("v228").NAME == "v228"
        assert strategies.get(None).NAME == "momentum"
        assert strategies.get("  ") .NAME == "momentum"

    def test_unknown_raises(self):
        import strategies
        try:
            strategies.get("nope")
            assert False, "should raise"
        except ValueError:
            pass

    def test_v228_has_no_debate_step(self):
        import strategies
        steps = [s["step"] for s in strategies.get("v228").STEPS]
        assert steps == ["decide", "execute"]
        assert "debate" not in strategies.get("v228").EXPECTED_STEPS
        assert strategies.get("v228").BRACKET == {"tp_pct": 10.0, "stop_pct": 5.0}
        assert strategies.get("momentum").BRACKET == {"tp_pct": 15.0, "stop_pct": 5.0}


class TestV228:
    def test_entry_dip_below_2sigma_worst_first(self):
        from strategies import v228
        # NVDA dips hardest, AMD second, META third, AAPL shallow (no entry)
        ctx = _ctx(rets={"NVDA": -0.04, "AMD": -0.03, "META": -0.025,
                         "AAPL": -0.005, "LLY": 0.01},
                   sigmas={"NVDA": 0.015, "AMD": 0.012, "META": 0.011,
                           "AAPL": 0.01, "LLY": 0.01},
                   prices={"NVDA": 100.0, "AMD": 100.0, "META": 100.0,
                           "AAPL": 100.0, "LLY": 100.0})
        d = v228.build_decision(_book(cash=99000.0), ctx)
        syms = [e["symbol"] for e in d["entries"]]
        assert syms == ["NVDA", "AMD", "META"]          # worst-first, top 3
        for e in d["entries"]:
            assert e["quantity"] == 330                 # 99k/3 // 100
        assert d["exits"] == []

    def test_threshold_kvol_times_sigma(self):
        from strategies import v228
        # AMD: -3.0% vs threshold -2*1.4% = -2.8% -> enters
        # TSLA: -2.7% vs threshold -2*1.4% = -2.8% -> no entry
        ctx = _ctx(rets={"AMD": -0.03, "TSLA": -0.027},
                   sigmas={"AMD": 0.014, "TSLA": 0.014},
                   prices={"AMD": 100.0, "TSLA": 100.0})
        d = v228.build_decision(_book(cash=99000.0), ctx)
        assert [e["symbol"] for e in d["entries"]] == ["AMD"]

    def test_exits_tp_and_stop(self):
        from strategies import v228
        ctx = _ctx(rets={"AAPL": 0.0}, sigmas={"AAPL": 0.01},
                   prices={"AAPL": 100.0})
        book = _book(cash=1000.0, positions={
            "GILD": {"qty": 10, "entry": 100.0},   # cur 100 -> wait, use ctx price
        })
        # craft: GILD entry 100 cur 111 (+11% -> TP), MSFT entry 100 cur 94 (-6% -> stop)
        ctx["stocks"]["GILD"] = {"price": 111.0, "ret_1d": 0.0, "sigma20": 0.01}
        ctx["stocks"]["MSFT"] = {"price": 94.0, "ret_1d": 0.0, "sigma20": 0.01}
        book = _book(cash=1000.0, positions={
            "GILD": {"qty": 10, "entry": 100.0},
            "MSFT": {"qty": 5, "entry": 100.0}})
        d = v228.build_decision(book, ctx)
        exits = {e["symbol"]: e["reason"] for e in d["exits"]}
        assert "v228_tp_exit" in exits["GILD"]
        assert "v228_stop_exit" in exits["MSFT"]

    def test_held_names_not_reentered_and_slots_respected(self):
        from strategies import v228
        ctx = _ctx(rets={"NVDA": -0.05, "AMD": -0.04, "META": -0.03,
                         "JPM": -0.02},
                   sigmas={s: 0.01 for s in ("NVDA", "AMD", "META", "JPM")},
                   prices={s: 100.0 for s in ("NVDA", "AMD", "META", "JPM")})
        book = _book(cash=1000.0, positions={"NVDA": {"qty": 30, "entry": 98.0}})
        d = v228.build_decision(book, ctx)
        syms = [e["symbol"] for e in d["entries"]]
        assert "NVDA" not in syms                      # held, not re-entered
        assert len(syms) == 2                          # 3 slots - 1 held
        assert syms == ["AMD", "META"]                 # worst-first among rest

    def test_error_names_skipped(self):
        from strategies import v228
        ctx = {"v228": True, "stocks": {
            "NVDA": {"error": "only 3 closes"},
            "AMD": {"price": 100.0, "ret_1d": -0.05, "sigma20": 0.01}}}
        d = v228.build_decision(_book(cash=99000.0), ctx)
        assert [e["symbol"] for e in d["entries"]] == ["AMD"]


class TestV228ClosesCompletedBar:
    """v228._closes must ignore the in-progress session bar: Yahoo appends
    today's partial bar while the market is open, which would turn ret_1d
    into a minutes-old intraday move measured against a -2*sigma20 DAILY
    threshold (entry-blind live strategy; see lab4 + 2026-11-16 fix)."""

    def _fake_chart(self, today):
        import datetime as dt
        ts = [int((today - dt.timedelta(days=3)).timestamp()),
              int((today - dt.timedelta(days=2)).timestamp()),
              int(today.replace(hour=14, minute=30).timestamp())]
        return {"chart": {"result": [{"timestamp": ts,
                                      "indicators": {"quote": [{"close": [100.0, 110.0, 120.0]}]}}]}}

    def _patch(self, monkeypatch, today, fake):
        import strategies.v228 as m
        monkeypatch.setattr(m.marketdata, "get", lambda url, timeout=12: fake)
        return m

    def test_drops_partial_bar_when_open(self, monkeypatch):
        import datetime as dt
        from trading_common.trade_rules import market_open
        today = None
        for h in range(9, 21):
            cand = dt.datetime.now(dt.timezone.utc).replace(hour=h, minute=0)
            if market_open(cand):
                today = cand
                break
        if today is None:  # defensive: weekend-adjacent CI runs
            import pytest
            pytest.skip("no market-open hour findable from CI clock")
        m = self._patch(monkeypatch, today, self._fake_chart(today))
        assert m._closes("NVDA", need=2, now=today) == [100.0, 110.0]

    def test_keeps_completed_bar_when_closed(self, monkeypatch):
        import datetime as dt
        from trading_common.trade_rules import market_open
        after = None
        for h in range(23, 2, -1):
            cand = dt.datetime.now(dt.timezone.utc).replace(hour=h, minute=0)
            if not market_open(cand):
                after = cand
                break
        if after is None:
            import pytest
            pytest.skip("no market-closed hour findable from CI clock")
        m = self._patch(monkeypatch, after, self._fake_chart(after))
        assert m._closes("NVDA", need=2, now=after) == [100.0, 110.0, 120.0]
