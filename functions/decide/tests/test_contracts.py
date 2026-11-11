import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "include"))

from trading_common import contracts as C  # noqa: E402
from trading_common.contracts import (  # noqa: E402
    StrategyError,
    ensure_decision,
    ensure_market_ctx,
    market_ctx,
    validate_decision_plan_overlap,
    validate_strategy_module,
)


def _mod(**overrides):
    """A contract-valid momentum-like module to mutate in tests."""
    class Mod:
        NAME = "tester"
        UNIVERSE = ("AAPL", "MSFT")
        STEPS = ({"step": "decide", "path": "/decide", "timeout": 480},
                 {"step": "execute", "path": "/execute", "timeout": 300})
        EXPECTED_STEPS = ("decide", "execute")
        BRACKET = {"tp_pct": 15.0, "stop_pct": 5.0}

        def fetch(self):
            return C.market_ctx({"AAPL": 100.0})

        def build_decision(self, book, ctx, now=None):
            return {"ok": False, "today": "2026-01-01", "now_utc": "x"}

    for k, v in overrides.items():
        setattr(Mod, k, v)
    return Mod


class TestRegistrationValidation:
    def test_valid_module_passes(self):
        assert validate_strategy_module(_mod()) == []

    def test_duplicate_step_names_rejected(self):
        problems = validate_strategy_module(_mod(STEPS=(
            {"step": "decide", "path": "/decide", "timeout": 480},
            {"step": "decide", "path": "/decide2", "timeout": 480})))
        assert any("duplicate step names" in p for p in problems)

    def test_duplicate_step_paths_rejected(self):
        problems = validate_strategy_module(_mod(STEPS=(
            {"step": "decide", "path": "/x", "timeout": 480},
            {"step": "execute", "path": "/x", "timeout": 300})))
        assert any("duplicate step paths" in p for p in problems)

    def test_expected_steps_missing_chain_step_rejected(self):
        problems = validate_strategy_module(_mod(EXPECTED_STEPS=("decide",)))
        assert any("must cover every chain step" in p for p in problems)

    def test_expected_steps_ghost_name_rejected(self):
        problems = validate_strategy_module(_mod(EXPECTED_STEPS=("decide", "execute", "scroe")))
        assert any("unknown to STEPS" in p for p in problems)

    def test_expected_steps_research_step_allowed(self):
        assert validate_strategy_module(
            _mod(EXPECTED_STEPS=("decide", "execute", "score"))) == []

    def test_bracket_bounds_rejected(self):
        for bad in ({"tp_pct": 0.0, "stop_pct": 5.0},
                    {"tp_pct": -5.0, "stop_pct": 5.0},
                    {"tp_pct": 150.0, "stop_pct": 5.0},
                    {"tp_pct": float("nan"), "stop_pct": 5.0},
                    {"tp_pct": "15", "stop_pct": 5.0}):
            problems = validate_strategy_module(_mod(BRACKET=bad))
            assert any("BRACKET.tp_pct" in p for p in problems), bad

    def test_universe_malformed_and_duplicates_rejected(self):
        problems = validate_strategy_module(_mod(UNIVERSE=("aapl", "MSFT", "MSFT")))
        assert any("malformed symbols" in p for p in problems)
        assert any("duplicates" in p for p in problems)

    def test_empty_universe_allowed_dynamic_discovery(self):
        assert validate_strategy_module(_mod(UNIVERSE=())) == []

    def test_step_timeout_bounds_rejected(self):
        for bad in (0, -5, 3001):
            problems = validate_strategy_module(_mod(STEPS=(
                {"step": "decide", "path": "/decide", "timeout": bad},)))
            assert any("timeout" in p for p in problems), bad

    def test_step_path_must_start_with_slash(self):
        problems = validate_strategy_module(_mod(STEPS=(
            {"step": "decide", "path": "decide", "timeout": 480},)))
        assert any("must start with /" in p for p in problems)

    def test_name_charset_enforced(self):
        problems = validate_strategy_module(_mod(NAME="Bad Name"))
        assert any("NAME" in p for p in problems)

    def test_missing_callables_rejected(self):
        m = _mod()
        del m.fetch
        problems = validate_strategy_module(m)
        assert any("fetch() must be callable" in p for p in problems)

    def test_report_lists_all_problems_at_once(self):
        problems = validate_strategy_module(_mod(NAME="Bad", BRACKET={"tp_pct": 0.0, "stop_pct": 5.0}))
        assert len(problems) >= 2  # not fail-first


class TestRegistry:
    def test_real_strategies_registered_and_valid(self):
        import strategies
        assert set(strategies.REGISTRY) == {"momentum", "v228"}
        for name, mod in strategies.REGISTRY.items():
            assert validate_strategy_module(mod) == []
            assert mod.NAME == name

    def test_double_register_rejected(self):
        import strategies
        with pytest.raises(StrategyError, match="registered twice"):
            strategies.register(strategies.get("momentum"))

    def test_invalid_register_rejected_with_all_problems(self):
        import strategies
        with pytest.raises(StrategyError) as e:
            strategies.register(_mod(NAME="Bad", BRACKET={"tp_pct": 0.0, "stop_pct": 5.0}))
        assert "violates its contract" in str(e.value)
        assert "BRACKET.tp_pct" in str(e.value)

    def test_unknown_strategy_lookup(self):
        import strategies
        with pytest.raises(StrategyError, match="unknown strategy"):
            strategies.get("nope")


class TestMarketCtxBoundary:
    def test_valid_passes(self):
        ctx = ensure_market_ctx("s", market_ctx({"AAPL": 100.0}, {"MSFT": "no data"}))
        assert ctx["prices"] == {"AAPL": 100.0}

    def test_non_dict_rejected(self):
        with pytest.raises(StrategyError, match="must return a MarketCtx"):
            ensure_market_ctx("s", ["prices"])

    def test_missing_prices_rejected(self):
        with pytest.raises(StrategyError, match="prices must be a dict"):
            ensure_market_ctx("s", {"errors": {}})

    def test_nonpositive_or_nonfinite_price_rejected(self):
        with pytest.raises(StrategyError, match="non-positive"):
            ensure_market_ctx("s", {"prices": {"AAPL": 0.0}, "errors": {}})
        with pytest.raises(StrategyError, match="non-positive"):
            ensure_market_ctx("s", {"prices": {"AAPL": float("nan")}, "errors": {}})

    def test_price_error_overlap_rejected_by_builder(self):
        with pytest.raises(StrategyError, match="both prices and errors"):
            market_ctx({"AAPL": 1.0}, {"AAPL": "down"})


def _decision(**over):
    d = {"ok": True, "today": "2026-01-01", "now_utc": "x", "strategy": "t",
         "market_open": True,
         "account": {"cash": 1.0, "equity": 2.0, "positions_held": 0},
         "exits": [], "entries": []}
    d.update(over)
    return d


class TestDecisionBoundary:
    def test_valid_passes(self):
        assert ensure_decision("s", _decision())["ok"] is True

    def test_non_dict_rejected(self):
        with pytest.raises(StrategyError, match="must return a Decision"):
            ensure_decision("s", [])

    def test_missing_keys_rejected(self):
        d = _decision()
        del d["entries"]
        with pytest.raises(StrategyError, match="missing key 'entries'"):
            ensure_decision("s", d)

    def test_zero_or_negative_quantity_rejected(self):
        with pytest.raises(StrategyError, match="positive int"):
            ensure_decision("s", _decision(entries=[
                {"action": "buy", "symbol": "AAPL", "quantity": 0,
                 "est_cost": 1.0, "reason": "r"}]))

    def test_boolean_quantity_rejected(self):
        with pytest.raises(StrategyError, match="positive int"):
            ensure_decision("s", _decision(entries=[
                {"action": "buy", "symbol": "AAPL", "quantity": True,
                 "est_cost": 1.0, "reason": "r"}]))

    def test_negative_est_cost_and_nan_rejected(self):
        with pytest.raises(StrategyError, match="est_cost"):
            ensure_decision("s", _decision(entries=[
                {"action": "buy", "symbol": "AAPL", "quantity": 1,
                 "est_cost": -1.0, "reason": "r"}]))
        with pytest.raises(StrategyError, match="est_cost"):
            ensure_decision("s", _decision(entries=[
                {"action": "buy", "symbol": "AAPL", "quantity": 1,
                 "est_cost": float("inf"), "reason": "r"}]))

    def test_duplicate_entry_symbols_rejected(self):
        e = {"action": "buy", "symbol": "AAPL", "quantity": 1, "est_cost": 1.0, "reason": "r"}
        with pytest.raises(StrategyError, match="duplicate entry symbols"):
            ensure_decision("s", _decision(entries=[e, dict(e)]))

    def test_entry_and_exit_same_symbol_rejected(self):
        e = {"action": "buy", "symbol": "AAPL", "quantity": 1, "est_cost": 1.0, "reason": "r"}
        x = {"action": "sell", "symbol": "AAPL", "quantity": 1, "reason": "r"}
        with pytest.raises(StrategyError, match="contradictory intents"):
            ensure_decision("s", _decision(entries=[e], exits=[x]))

    def test_bad_action_rejected(self):
        with pytest.raises(StrategyError, match="action='buy'"):
            ensure_decision("s", _decision(entries=[
                {"action": "sell", "symbol": "AAPL", "quantity": 1,
                 "est_cost": 1.0, "reason": "r"}]))

    def test_nonfinite_account_rejected(self):
        with pytest.raises(StrategyError, match="account.cash"):
            ensure_decision("s", _decision(account={"cash": float("nan"),
                                                    "equity": 1.0, "positions_held": 0}))

    def test_ok_false_minimal_shape_allowed(self):
        d = ensure_decision("s", {"ok": False, "today": "2026-01-01", "now_utc": "x"})
        assert d["ok"] is False

    def test_ok_false_missing_today_rejected(self):
        with pytest.raises(StrategyError, match="lacks today"):
            ensure_decision("s", {"ok": False, "now_utc": "x"})


class TestPlanOverlap:
    def _book(self, held):
        return {"cash": 0.0, "positions": {s: {"qty": 1, "entry": 1.0} for s in held}}

    def test_reentry_without_exit_rejected(self):
        d = _decision(entries=[{"action": "buy", "symbol": "AAPL", "quantity": 1,
                                "est_cost": 1.0, "reason": "r"}])
        with pytest.raises(StrategyError, match="re-enters held symbol"):
            validate_decision_plan_overlap(d, self._book({"AAPL"}))

    def test_exit_then_reentry_allowed(self):
        d = _decision(entries=[{"action": "buy", "symbol": "AAPL", "quantity": 1,
                                "est_cost": 1.0, "reason": "r"}],
                      exits=[{"action": "sell", "symbol": "AAPL", "quantity": 1,
                              "reason": "r"}])
        validate_decision_plan_overlap(d, self._book({"AAPL"}))  # no raise

    def test_new_symbol_fine(self):
        d = _decision(entries=[{"action": "buy", "symbol": "NEW", "quantity": 1,
                                "est_cost": 1.0, "reason": "r"}])
        validate_decision_plan_overlap(d, self._book({"AAPL"}))  # no raise


class TestRealStrategies:
    def test_momentum_fetch_shape(self):
        import strategies
        strat = strategies.get("momentum")
        # don't hit the network: validate the normalization logic offline
        ctx = C.market_ctx({"SPY": 500.0}, {}, "2026-01-01T00:00:00+00:00")
        ctx["snapshot"] = {"stocks": [], "fetched_at_utc": ctx["as_of"]}
        ensure_market_ctx(strat.NAME, ctx)
        assert ctx["prices"] == {"SPY": 500.0}

    def test_v228_build_decision_contract_clean(self):
        import strategies
        strat = strategies.get("v228")
        from datetime import datetime, timezone
        ctx = {"prices": {"NVDA": 100.0, "AMD": 100.0},
               "errors": {},
               "as_of": "2026-01-01T00:00:00+00:00",
               "stocks": {"NVDA": {"price": 100.0, "ret_1d": -0.05, "sigma20": 0.01},
                          "AMD": {"price": 100.0, "ret_1d": -0.01, "sigma20": 0.01}}}
        decision = ensure_decision(
            "v228", strat.build_decision(
                {"cash": 99000.0, "positions": {}}, ctx,
                now=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        assert decision["strategy"] == "v228"
        assert [e["symbol"] for e in decision["entries"]] == ["NVDA"]
