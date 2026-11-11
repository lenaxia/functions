"""Strategy registry — the seam between books and trading rules.

A strategy module implements the `Strategy` protocol (trading_common/
contracts.py) and declares:
  NAME            registry key (matches the STRATEGY secret)
  UNIVERSE        candidate symbols, uppercase tuple
  STEPS           ordered chain steps as a tuple of StepSpec (paths may
                  point at per-book function CRs, e.g. /decide-v228)
  EXPECTED_STEPS  daily steps reconcile should demand (must cover every
                  chain step; research steps score/digest may be added)
  BRACKET         {"tp_pct": .., "stop_pct": ..} attached to live buys
  fetch()         -> MarketCtx (normalized: prices/errors/as_of; private
                  extras allowed beyond the contract surface)
  build_decision(book, ctx, now) -> Decision

Registration validates the full contract at IMPORT time — a broken
strategy fails every pod's specialization loudly instead of trading on
defaults. Adding strategy #3 is a module + one register() line.
"""
from __future__ import annotations

from trading_common.contracts import (
    StrategyError,
    validate_strategy_module,
)

REGISTRY: dict[str, object] = {}


def register(mod: object) -> None:
    """Validate a strategy module and register it. Raises StrategyError
    naming every problem at once (import-time fail-fast)."""
    name = getattr(mod, "NAME", None)
    if not isinstance(name, str) or not name:
        raise StrategyError("strategy module %r has no usable NAME" % mod)
    if name in REGISTRY:
        raise StrategyError("strategy %r registered twice" % name)
    problems = validate_strategy_module(mod)
    if problems:
        raise StrategyError("strategy %r violates its contract:\n  - %s"
                            % (name, "\n  - ".join(problems)))
    REGISTRY[name] = mod


from . import momentum, v228  # noqa: E402  (import after register() exists)

register(momentum)
register(v228)

DEFAULT = momentum.NAME


def get(name: str | None):
    key = (name or DEFAULT).strip() or DEFAULT
    if key not in REGISTRY:
        raise StrategyError("unknown strategy %r (have: %s)" % (key, sorted(REGISTRY)))
    return REGISTRY[key]


def for_secret():
    """Resolve from the pod's STRATEGY secret."""
    from trading_common.secrets import secret
    return get(secret("STRATEGY", DEFAULT))
