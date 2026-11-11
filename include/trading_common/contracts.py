"""Typed contracts for trading strategies — the single source of truth.

Every strategy implements the `Strategy` protocol (see
strategies/__init__.py) and communicates in the shapes below. The helpers
here enforce the contracts at the boundaries:

  - registry registration: validate_strategy_module() checks a strategy
    module's static shape and FAILS the import (pods refuse to specialize
    against a broken strategy) — bad strategies never ship quietly.
  - decide: ensure_market_ctx()/ensure_decision() validate what fetch()
    and build_decision() returned, with the strategy named in every error.

Edge cases the validators reject explicitly (each was a real or plausible
silent-failure mode):
  duplicate step names / paths, unknown-or-missing chain steps in
  EXPECTED_STEPS, non-positive step timeouts, oversized timeouts (would
  exceed any deployed functionTimeout), bad bracket bounds (0% = instant
  exit; negative; >100%), duplicate or malformed universe symbols,
  duplicate symbols across a decision's entries, an entry and an exit for
  the same symbol in one decision, zero/negative quantities, non-finite
  numbers (NaN/Inf break jsonb writes downstream), and ok=False decisions
  (handled by decide as a failed step, never as an empty plan).
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable


class StrategyError(ValueError):
    """A strategy module violated its contract. The message names the
    strategy and every problem found (not just the first)."""


# ---------------------------------------------------------------------------
# data shapes
# ---------------------------------------------------------------------------

class Position(TypedDict):
    qty: int
    entry: float


class Book(TypedDict):
    cash: float
    positions: dict[str, Position]


class Quote(TypedDict):
    price: float
    ret_1d: NotRequired[float]
    sigma20: NotRequired[float]


class MarketCtx(TypedDict):
    """Normalized market context every strategy's fetch() must return.

    `prices` carries VALID quotes only — unavailable symbols are absent
    here and named in `errors`. Strategies may attach extra private keys
    (e.g. v228's per-symbol sigma20 view) beyond this surface; consumers
    (decide/chains/execute) must rely on the contract keys only.
    """
    prices: dict[str, float]
    errors: dict[str, str]
    as_of: str


class SellIntent(TypedDict):
    action: Literal["sell"]
    symbol: str
    quantity: int
    reason: str
    pnl_pct: NotRequired[float]


class EntryIntent(TypedDict):
    action: Literal["buy"]
    symbol: str
    quantity: int
    est_cost: float
    reason: str
    headlines: NotRequired[list[str]]


class AccountView(TypedDict):
    cash: float
    equity: float
    positions_held: int


class Decision(TypedDict):
    ok: bool
    now_utc: str
    today: str
    market_open: bool
    strategy: str
    account: AccountView
    exits: list[SellIntent]
    entries: list[EntryIntent]


class StepSpec(TypedDict):
    step: str
    path: str
    timeout: int
    optional: NotRequired[bool]


class BracketParams(TypedDict):
    tp_pct: float
    stop_pct: float


@runtime_checkable
class Strategy(Protocol):
    NAME: str
    UNIVERSE: tuple[str, ...]
    STEPS: tuple[StepSpec, ...]
    EXPECTED_STEPS: tuple[str, ...]
    BRACKET: BracketParams

    def fetch(self) -> MarketCtx: ...
    def build_decision(self, book: Book, ctx: MarketCtx,
                       now: datetime | None = None) -> Decision: ...


# ---------------------------------------------------------------------------
# bounds & patterns (single source of truth — validation and docs agree)
# ---------------------------------------------------------------------------

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
NAME_RE = re.compile(r"^[a-z0-9_]+$")
MAX_STEP_TIMEOUT_S = 3000        # deployed functionTimeouts cap at 3480
MAX_STEPS = 6                    # a chain is a handful of steps, not a pipeline


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def market_ctx(prices: dict[str, float], errors: dict[str, str] | None = None,
               as_of: str | None = None) -> MarketCtx:
    """Build a validated MarketCtx (strategies call this from fetch())."""
    bad = [s for s, p in prices.items() if not (_finite(p) and p > 0)]
    if bad:
        raise StrategyError("market_ctx: non-positive/non-finite prices for %s" % bad)
    overlap = sorted(set(prices) & set(errors or {}))
    if overlap:
        raise StrategyError("market_ctx: %s appear in both prices and errors" % overlap)
    return {"prices": dict(prices), "errors": dict(errors or {}),
            "as_of": as_of or utc_now_iso()}


# ---------------------------------------------------------------------------
# boundary validation
# ---------------------------------------------------------------------------

def ensure_market_ctx(strategy: str, ctx: Any) -> MarketCtx:
    """Validate what a strategy's fetch() returned. decide wraps fetch()
    with this so a malformed context fails loudly, named and early."""
    problems: list[str] = []
    if not isinstance(ctx, dict):
        raise StrategyError("%s.fetch() must return a MarketCtx dict, got %s"
                            % (strategy, type(ctx).__name__))
    prices = ctx.get("prices")
    errors = ctx.get("errors")
    if not isinstance(prices, dict):
        problems.append("prices must be a dict, got %s" % type(prices).__name__)
    elif not all(_finite(p) and p > 0 for p in prices.values()):
        problems.append("prices contains non-positive or non-finite values: %s"
                        % [s for s, p in prices.items() if not (_finite(p) and p > 0)])
    if not isinstance(errors, dict):
        problems.append("errors must be a dict, got %s" % type(errors).__name__)
    if problems:
        raise StrategyError("%s.fetch() contract violations: %s" % (strategy, "; ".join(problems)))
    return ctx  # type: ignore[return-value]


def ensure_decision(strategy: str, decision: Any) -> Decision:
    """Validate what a strategy's build_decision() returned."""
    base = "%s.build_decision()" % strategy
    if not isinstance(decision, dict):
        raise StrategyError("%s must return a Decision dict, got %s"
                            % (base, type(decision).__name__))
    if not decision.get("ok"):
        # a strategy may signal failure (no data, upstream down) — decide
        # treats this as a failed step; still require the marker keys
        for k in ("today", "now_utc"):
            if not isinstance(decision.get(k), str):
                raise StrategyError("%s ok=False decision lacks %s" % (base, k))
        return decision  # type: ignore[return-value]

    problems: list[str] = []
    for k in ("today", "now_utc", "strategy", "market_open",
              "account", "exits", "entries"):
        if k not in decision:
            problems.append("missing key %r" % k)
    if problems:
        raise StrategyError("%s contract violations: %s" % (base, "; ".join(problems)))

    entries = decision["entries"]
    exits = decision["exits"]
    for label, rows, action in (("entries", entries, "buy"), ("exits", exits, "sell")):
        if not isinstance(rows, list):
            problems.append("%s must be a list" % label)
            continue
        for r in rows:
            if not isinstance(r, dict) or r.get("action") != action:
                problems.append("%s rows must carry action=%r" % (label, action))
                continue
            sym = r.get("symbol")
            qty = r.get("quantity")
            if not isinstance(sym, str) or not SYMBOL_RE.match(sym):
                problems.append("%s bad symbol %r" % (label, sym))
            if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                problems.append("%s %s: quantity must be a positive int, got %r"
                                % (label, sym, qty))
            if label == "entries":
                cost = r.get("est_cost")
                if not _finite(cost) or cost < 0:
                    problems.append("entry %s: est_cost must be finite >= 0, got %r"
                                    % (sym, cost))
            if not isinstance(r.get("reason"), str) or not r["reason"]:
                problems.append("%s %s: missing reason" % (label, sym))

    e_syms = [r.get("symbol") for r in entries if isinstance(r, dict)]
    x_syms = [r.get("symbol") for r in exits if isinstance(r, dict)]
    dup_e = sorted({s for s in e_syms if e_syms.count(s) > 1})
    dup_x = sorted({s for s in x_syms if x_syms.count(s) > 1})
    if dup_e:
        problems.append("duplicate entry symbols %s" % dup_e)
    if dup_x:
        problems.append("duplicate exit symbols %s" % dup_x)
    both = sorted(set(e_syms) & set(x_syms))
    if both:
        problems.append("symbol(s) %s in entries AND exits of the same decision "
                        "(contradictory intents — exit first, enter next run)" % both)

    acct = decision.get("account")
    if isinstance(acct, dict):
        for k in ("cash", "equity"):
            if not _finite(acct.get(k)):
                problems.append("account.%s must be a finite number" % k)
        if not isinstance(acct.get("positions_held"), int):
            problems.append("account.positions_held must be an int")
    else:
        problems.append("account must be a dict")
    if problems:
        raise StrategyError("%s contract violations: %s" % (base, "; ".join(problems)))
    return decision  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# registration-time validation (import-time fail-fast)
# ---------------------------------------------------------------------------

def validate_strategy_module(mod: Any) -> list[str]:
    """Static contract check for a strategy module. Returns every problem
    found (callers raise StrategyError with the full list)."""
    name = getattr(mod, "NAME", "<unnamed>")
    problems: list[str] = []

    if not isinstance(name, str) or not NAME_RE.match(name):
        problems.append("NAME %r must match %s" % (name, NAME_RE.pattern))
    if not isinstance(mod, Strategy):  # runtime_checkable: attrs/methods exist
        problems.append("does not satisfy the Strategy protocol shape")

    universe = getattr(mod, "UNIVERSE", None)
    if not isinstance(universe, tuple):
        problems.append("UNIVERSE must be a tuple (empty = dynamic discovery)")
    else:
        bad = [s for s in universe
               if not isinstance(s, str) or not SYMBOL_RE.match(s)]
        if bad:
            problems.append("UNIVERSE has malformed symbols: %s" % bad)
        dups = sorted({s for s in universe if universe.count(s) > 1})
        if dups:
            problems.append("UNIVERSE has duplicates: %s" % dups)

    steps = getattr(mod, "STEPS", None)
    if not isinstance(steps, tuple) or not steps:
        problems.append("STEPS must be a non-empty tuple of StepSpec")
    else:
        if len(steps) > MAX_STEPS:
            problems.append("STEPS has %d entries (max %d)" % (len(steps), MAX_STEPS))
        names_seen: list[str] = []
        paths_seen: list[str] = []
        for s in steps:
            if not isinstance(s, dict):
                problems.append("STEPS entries must be StepSpec dicts")
                continue
            step_name = s.get("step")
            if not isinstance(step_name, str) or not NAME_RE.match(step_name):
                problems.append("step name %r must match %s" % (step_name, NAME_RE.pattern))
            else:
                names_seen.append(step_name)
            path = s.get("path")
            if not isinstance(path, str) or not path.startswith("/"):
                problems.append("step %r path %r must start with /" % (step_name, path))
            else:
                paths_seen.append(path)
            t = s.get("timeout")
            if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
                problems.append("step %r timeout must be a positive int, got %r"
                                % (step_name, t))
            elif t > MAX_STEP_TIMEOUT_S:
                problems.append("step %r timeout %ds exceeds the %ds deployment cap"
                                % (step_name, t, MAX_STEP_TIMEOUT_S))
        dup_names = sorted({n for n in names_seen if names_seen.count(n) > 1})
        dup_paths = sorted({p for p in paths_seen if paths_seen.count(p) > 1})
        if dup_names:
            problems.append("duplicate step names %s (step_done keys collide)" % dup_names)
        if dup_paths:
            problems.append("duplicate step paths %s" % dup_paths)

    expected = getattr(mod, "EXPECTED_STEPS", None)
    chain_names = {s.get("step") for s in steps} if isinstance(steps, tuple) else set()
    if not isinstance(expected, tuple) or not expected:
        problems.append("EXPECTED_STEPS must be a non-empty tuple")
    else:
        missing_chain = sorted(chain_names - set(expected))
        if missing_chain:
            problems.append("EXPECTED_STEPS must cover every chain step; missing %s "
                            "(reconcile would never demand them)" % missing_chain)
        ghost = sorted(set(expected) - chain_names - {"score", "digest"})
        if ghost:
            problems.append("EXPECTED_STEPS names unknown to STEPS: %s (extra names "
                            "are allowed only for research steps score/digest)" % ghost)

    bracket = getattr(mod, "BRACKET", None)
    if not isinstance(bracket, dict):
        problems.append("BRACKET must be a BracketParams dict")
    else:
        for k in ("tp_pct", "stop_pct"):
            v = bracket.get(k)
            if not _finite(v) or not (0 < v <= 100):
                problems.append("BRACKET.%s must be finite in (0, 100], got %r" % (k, v))

    for m in ("fetch", "build_decision"):
        if not callable(getattr(mod, m, None)):
            problems.append("%s() must be callable" % m)

    return problems


def validate_decision_plan_overlap(decision: Decision, book: Book) -> None:
    """Runtime cross-check against the live book: an entry for a symbol the
    book already holds (and which this decision doesn't exit) would double
    a position downstream. Called by decide after gates, before journaling."""
    held = {s for s, p in (book.get("positions") or {}).items()
            if int(p.get("qty") or 0) > 0}
    exiting = {e["symbol"] for e in decision["exits"]}
    reentered = sorted({e["symbol"] for e in decision["entries"]} & (held - exiting))
    if reentered:
        raise StrategyError("decision re-enters held symbol(s) %s without exiting "
                            "them first" % reentered)
