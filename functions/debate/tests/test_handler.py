import main as debate
from conftest import FakeDB


def _fake_decision(cands):
    return {
        "ok": True,
        "account": {"cash": 50000, "equity": 100000},
        "entries": [{"symbol": s, "reason": "momentum", "headlines": ["x rises"]}
                    for s in cands],
    }


def test_mapping_verbatim():
    assert debate.MAPPING["buy"] == "pass"
    assert debate.MAPPING["overweight"] == "pass"
    assert debate.MAPPING["hold"] == "pass_with_caution"
    assert debate.MAPPING["underweight"] == "veto"
    assert debate.MAPPING["sell"] == "veto"
    assert debate.MAPPING["review"] == "abstain"


def test_debate_symbol_veto(monkeypatch):
    monkeypatch.setattr(debate.llm, "chat", lambda m, **k: "bull...\nbear...\nRATING: sell")
    res = {}
    debate.debate_symbol("NVDA", {"cash": 1, "holdings": {}}, {}, res)
    assert res["verdict"] == "veto"
    assert res["rating"] == "sell"


def test_debate_symbol_abstains_on_llm_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("endpoint down")
    monkeypatch.setattr(debate.llm, "chat", boom)
    res = {}
    debate.debate_symbol("AMD", {}, {}, res)
    assert res["verdict"] == "abstain"  # never blocks


def test_debate_symbol_unknown_rating_abstains(monkeypatch):
    monkeypatch.setattr(debate.llm, "chat", lambda m, **k: "musing... no rating line")
    res = {}
    debate.debate_symbol("TSLA", {}, {}, res)
    assert res["verdict"] == "abstain"


def test_candidates_cap_at_three(monkeypatch):
    db = FakeDB()
    db.set_positions({"NVDA": {"qty": 10, "entry": 100.0}})
    db.record_decision("1970-01-01", "book_state", {"cash": 1000.0})
    monkeypatch.setattr(
        debate.marketdata, "snapshot", lambda: {"stocks": []})
    monkeypatch.setattr(
        debate.trade_rules, "build_decision",
        lambda book, snap: _fake_decision(["A", "B", "C", "D", "E"]))
    cands, dec = debate.candidates(db)
    assert cands == ["A", "B", "C"]  # MAX_CANDIDATES = 3 (verbatim)


def test_main_idempotent(monkeypatch):
    from trading_common.db import utc_today
    db = FakeDB()
    db.steps.add((utc_today(), "debate"))
    monkeypatch.setattr(debate, "TradingDB", lambda: db)
    out = debate.main()
    assert out["already_done"] and out["ok"]
