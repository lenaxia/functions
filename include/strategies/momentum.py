"""momentum — the pilot book's conservative-momentum strategy (verbatim
rules in trade_rules; this module is the registry adapter)."""
from __future__ import annotations

from trading_common import marketdata, trade_rules
from trading_common.contracts import market_ctx
from trading_common.trade_rules import POLICY

NAME = "momentum"

UNIVERSE: tuple[str, ...] = ()  # candidate discovery is dynamic (snapshot sweep)

STEPS: tuple[dict, ...] = (
    {"step": "debate", "path": "/debate", "timeout": 2400, "optional": True},
    {"step": "decide", "path": "/decide", "timeout": 480, "optional": False},
    {"step": "execute", "path": "/execute", "timeout": 300, "optional": False},
)

EXPECTED_STEPS = ("debate", "decide", "execute", "score")

BRACKET = {"tp_pct": float(POLICY["take_profit_pct"]),
           "stop_pct": -float(POLICY["stop_loss_pct"])}


def fetch() -> dict:
    """MarketCtx surface (prices/errors/as_of) + the raw snapshot under
    `snapshot` for build_decision (52w bands, chg_1d — momentum's
    private extras beyond the contract surface)."""
    snap = marketdata.snapshot()
    prices: dict[str, float] = {}
    errors: dict[str, str] = {}
    for s in snap.get("stocks", []):
        if "error" in s:
            errors[s["sym"]] = str(s["error"])[:120]
        elif s.get("price"):
            prices[s["sym"]] = float(s["price"])
    ctx = market_ctx(prices, errors, snap.get("fetched_at_utc"))
    ctx["snapshot"] = snap
    return ctx


def build_decision(book: dict, ctx: dict, now=None) -> dict:
    return trade_rules.build_decision(book, ctx["snapshot"], now=now)
