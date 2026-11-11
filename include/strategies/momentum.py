"""momentum — the pilot book's conservative-momentum strategy (verbatim
port of trade_rules.build_decision via trade_rules; this module is the
registry adapter, the rules themselves stay in trading_common)."""
from __future__ import annotations

from trading_common import marketdata, trade_rules
from trading_common.trade_rules import POLICY

NAME = "momentum"

UNIVERSE: list[str] = []  # candidate discovery is dynamic (snapshot sweep)

STEPS = [
    {"step": "debate", "path": "/debate", "timeout": 2400, "optional": True},
    {"step": "decide", "path": "/decide", "timeout": 480, "optional": False},
    {"step": "execute", "path": "/execute", "timeout": 300, "optional": False},
]

EXPECTED_STEPS = ("debate", "decide", "execute")

BRACKET = {"tp_pct": float(POLICY["take_profit_pct"]),
           "stop_pct": -float(POLICY["stop_loss_pct"])}


def fetch() -> dict:
    return marketdata.snapshot()


def build_decision(book: dict, ctx: dict, now=None) -> dict:
    return trade_rules.build_decision(book, ctx, now=now)
