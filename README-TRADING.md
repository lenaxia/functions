# Trading system on Fission

Fission port of the `lenaxia/ai-trading` engine (source of truth:
`/workspace/ai4trade`, logic ported verbatim where possible), chained via
the Fission router with PostgreSQL state — deployed in **shadow mode** for a
comparison week alongside the existing GitHub Actions workflow.

## Architecture

```
                       ┌─────────────────────────────────────────────────┐
                       │  Fission (ns fission)                           │
                       │                                                 │
  TimeTrigger ────────▶│  morning-chain  (13:45 UTC, Mon-Fri)            │
  45 13 * * 1-5        │    │ POST /debate    (timeout 2400s, optional)  │
                       │    │ POST /decide    (timeout 480s)  ──fail──▶ ABORT
                       │    │ POST /execute   (timeout 300s)             │
                       │                                                 │
  TimeTrigger ────────▶│  afternoon-chain (22:30 UTC, Mon-Fri)           │
  30 22 * * 1-5        │    │ POST /score     (timeout 900s)             │
                       │    │ POST /digest    (timeout 120s, runs even   │
                       │    │                 if score failed = watchdog)│
                       └───────────────┬─────────────────────────────────┘
                                       │ router (in-cluster:
                                       │ router.fission.svc.cluster.local)
        ┌──────────────┬───────────────┼────────────────┬─────────────┐
        ▼              ▼               ▼                ▼             ▼
     debate         decide          execute           score         digest
   dd_screen.py   trade.py rules  alpaca_bridge    forward.py +    watchdog +
   LLM debate     + daily_run    reconcile+caps   control.py     plain-language
   (veto-only)    gates         +halt/resume      replay         summary
        │            │               │                │             │
        └────────────┴───────┬───────┴────────────────┴─────────────┘
                             ▼
              PostgreSQL (CNPG `tradingpg`, ns databases, db `trading`)
              positions / fills / decisions / equity_series /
              events / step_completions      + git journal
              (ai-trading repo, shadow/<date>/<step>.json)
```

- **debate** — DD gate (`dd_screen.py` port): 3 candidates max, one LLM
  debate per symbol (single round, temperature 0) over a plain HTTP
  OpenAI-compatible endpoint (`LLM_API_BASE`/`LLM_API_KEY`/`LLM_MODEL`).
  Verdict mapping verbatim: buy/overweight→pass, hold→pass_with_caution,
  underweight/sell→veto, else abstain. Veto-only: the LLM can only REMOVE
  candidates. 20 min/symbol, 40 min function budget.
  *Deviation (documented): the TradingAgents multi-agent graph is not
  vendored into Fission; a single structured chat completion produces the
  same rating space. Mapping, caps, timeouts and veto-only semantics
  unchanged.*
- **decide** — marketdata sweep (Yahoo, ~120 names) + `trade.py` rules
  verbatim (RS entry, ±5%/+15% exits, 10% sizing, market-hours guard) +
  `daily_run.py` gates verbatim (DD vetoes, headline risk screen,
  max 2 trades/day, exits before entries). Debate missing/failed ⇒ runs
  with `debate_fallback=true` (no vetoes — never blocks). In shadow, the
  book re-syncs from `state/book.json` each morning (git stays the source
  of truth).
- **execute** — `alpaca_bridge.py` port: reconcile broker to target
  (sells first), brackets on buys (−5%/+15%), caps from `PILOT_NOTIONAL`
  (per-order 30%, book 104%, kill-line 85% → halt), market-dictated
  resume only when the panic gates re-open (VIX ≤ 70th pct of 252d AND
  HYG/LQD credit stress cleared; fail-closed).
- **score** — `forward/forward.py` port (6 OOS variants + SPY benchmark,
  3y refetch, no-lookahead replay, deterministic, idempotent) +
  `control.py` port (buy-and-hold clone anchored 2026-09-29 at 100k).
  Equity curves land in `equity_series` (`fwd:<variant>`, `fwd:SPY`,
  `control`).
- **digest** — watchdog over `step_completions` + plain-language daily
  summary; posts to the `events` table AND the git journal.
- **chains** — call steps via router URLs with per-step timeouts; steps
  idempotent per `run_date` (skip already-completed). Morning chain aborts
  if decide fails; debate failure continues with the fallback flag.
  Afternoon chain always runs digest (watchdog).

## Shared code

`include/trading_common/` is rsynced into every function package by the
build (repo convention): `marketdata.py`, `trade_rules.py`, `gates.py`,
`alpaca.py`, `forward.py`, `control.py`, `broker.py` (ports),
`db.py` (psycopg3 + idempotent `schema.sql`), `gitstate.py` (ai-trading
read + shadow journal via contents API), `llm.py`, `secrets.py`.

## Database

CNPG cluster `tradingpg` (deploy templates in `deploy/trading/`). Schema
DDL: `include/trading_common/schema.sql` — `positions`, `fills`
(+`assumed_price`/`slippage_bps`), `decisions(run_date,step,payload_jsonb,
status)`, `equity_series(strategy,as_of,equity)`, `events`,
`step_completions`. Applied idempotently on every connect. Seed
(`trading-seed` function): book.json → positions + cash sentinel;
control/forward histories → `equity_series`; sleeve → sentinel row.
Upserts only — re-running never double-appends.

## Deploy steps

1. **This repo**: merge the PR → releases `<fn>-<version>.zip` appear,
   version per each function's `config.yaml` (CI builds + tests each
   function; the Package URLs pin those versions).
2. **talos-ops-prod**: copy `deploy/trading/*` per its README (CNPG
   cluster with initdb bootstrap, two sops secrets, Fission
   Package/Function/trigger CRs) + the environment CR from
   `envs/python3-trading/`.
3. `POST /trading-seed` once via the router (role+db are created by the
   CNPG initdb bootstrap from the `tradingpg-app` secret — no db-init
   Job).
4. Watch the next 13:45 / 22:30 UTC runs:
   `fission function logs --name morning-chain --follow`, and
   `SELECT * FROM step_completions ORDER BY ts DESC LIMIT 10;` on trading.

## Shadow mode (comparison week)

`SHADOW=1` in the shared `trading` secret (deployment default):

- All functions write the DB (decisions, fills-as-shadow, equity, events,
  step_completions) and the git journal (`ai-trading` repo,
  `shadow/<date>/<step>.json` via `GITHUB_TOKEN`).
- **execute is paper-read-only**: it reads the Alpaca paper account and
  logs exactly what it WOULD order (events + decisions status=shadow +
  shadow fills + journal) but places nothing and never mutates positions.
- The existing GitHub Actions workflow in `ai-trading` keeps running
  unchanged (daily 14:00 UTC run + 21:30 scoreboard) — it remains the
  live paper book.
- Compare, per UTC day: GHA `state/log.txt` + book commits vs the
  fission `decisions`/`events` rows + shadow journals.

### Shadow-mode caveats

1. Fission decides at 13:45 UTC, GHA at 14:00 UTC — snapshots differ
   slightly (prices/headlines), so identical decisions are expected but
   not bit-equal inputs.
2. The debate step replaces the (workspace-side, currently disabled in
   GHA) TradingAgents graph with a single structured LLM call — same
   rating space/veto-only semantics, different argmax behind it.
3. Shadow fills carry `assumed_price` from the decide snapshot; no
   `price`/`slippage_bps` until live.
4. The forward tracker refetches 3y from Yahoo per cold start (pod /tmp);
   GHA uses a warmed data dir. Same data source, occasionally different
   trailing-bar revisions.
5. Git journal writes consume the contents-API rate budget (1 file per
   step per day — negligible).
6. Halt state lives in the trading DB (events), not in
   `alpaca/halt_state.json`; the two halt states are independent during
   shadow.

## Cutover checklist (flip to live)

1. **Comparison week passes**: digest events clean (no watchdog misses),
   fission decisions track GHA decisions, no unexplained diff in
   equity_series.
2. **Backup**: add a `ScheduledBackup` + `barmanObjectStore` block to
   `tradingpg` (fills/events become authoritative at cutover) — see
   `llmsafespacespg.yaml` for the pattern.
3. **Seed parity check**: `POST /trading-seed` once more so DB positions
   equal the final GHA book; verify with
   `SELECT symbol, qty, entry FROM positions ORDER BY symbol;` vs
   `state/book.json`.
4. **Flip execute**: set `SHADOW=0` in the shared `trading` secret
   (one key, sops-edit + commit). Verify the next run places real paper
   orders and records `price`/`slippage_bps` on fills.
5. **Disable the GHA schedules** in `lenaxia/ai-trading`: comment out the
   `schedule:` blocks in `.github/workflows/daily.yml` and
   `scoreboard.yml` (leave `workflow_dispatch` for manual fallback).
6. **Watch the first live morning chain end-to-end**; keep the GHA
   workflows dispatchable for one more week as rollback (re-enable
   schedules + set `SHADOW=1`).
7. Optional cleanup: retire the git journal writes (`GITHUB_TOKEN` key)
   from secrets once the DB is the sole source of truth.

## Testing

Every function ships offline pytest suites (`tests/`, run per-function by
CI — `task local-build-<function>` locally). 86 tests cover the verbatim
gate/rule/cap math, chain ordering/abort/skip semantics, shadow-mode
no-order behavior, seed idempotency, the digest watchdog, the secret
mount-path contract (shared `trading` mount discovery, env fallback,
SHADOW=0 cutover flip honored), the manifest<->template secret-name +
package-URL/config version
cross-validation, and the regressions for the review-validated defects
(per-function conftest imports, DB-credential URL escaping, Alpaca
positions/clock read failures, unpriced-diff cap rejections, decide's
book-wide price map, the exec rowcount contract, and id-based V228
param selection).
