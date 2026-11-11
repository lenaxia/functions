"""Strategy registry — the seam between books and trading rules.

A strategy module declares:
  NAME            registry key (matches the STRATEGY secret)
  UNIVERSE        candidate symbols
  STEPS           ordered chain steps for the morning chain, each with
                  {step, path, timeout, optional} (paths may point at
                  per-book function CRs, e.g. /decide-v228)
  EXPECTED_STEPS  daily steps reconcile should demand (subset of STEPS)
  BRACKET         {"tp_pct": .., "stop_pct": ..} attached to live buys
  fetch()         market context for build_decision (each strategy owns
                  its own data needs — momentum wants a snapshot, v228
                  wants 40d closes for sigma20)
  build_decision(book, ctx, now)  -> decide-shaped payload

decide/chains/execute resolve their strategy from the STRATEGY secret;
adding strategy #3 is a new module + registry entry — no core changes.
"""
from __future__ import annotations

from . import momentum, v228

REGISTRY = {
    momentum.NAME: momentum,
    v228.NAME: v228,
}

DEFAULT = momentum.NAME


def get(name: str | None):
    key = (name or DEFAULT).strip() or DEFAULT
    if key not in REGISTRY:
        raise ValueError("unknown strategy %r (have: %s)" % (key, sorted(REGISTRY)))
    return REGISTRY[key]


def for_secret() -> "object":
    """Resolve from the pod's STRATEGY secret."""
    from trading_common.secrets import secret
    return get(secret("STRATEGY", DEFAULT))
