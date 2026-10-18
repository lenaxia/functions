-- Trading system schema (Fission port of lenaxia/ai-trading state files).
--
-- Idempotent: applied on every function connect (CREATE IF NOT EXISTS), in the
-- same spirit as repo-assessor's postgres backend. Kept in sync with
-- include/trading_common/db.py.
--
-- Tables:
--   positions         current book (from state/book.json; qty/entry per symbol)
--   fills             executed (live) or assumed (shadow) fills
--   decisions         per-run decision payloads (debate/decide/execute/score..)
--   equity_series     strategy equity curves (control, fwd:<variant>, fwd:SPY)
--   events            append-only audit log (digest, shadow orders, halts..)
--   step_completions  idempotency + watchdog: (run_date, step) done markers

CREATE TABLE IF NOT EXISTS positions (
    symbol       TEXT PRIMARY KEY,
    qty          NUMERIC NOT NULL DEFAULT 0,
    entry        NUMERIC NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fills (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty           NUMERIC NOT NULL CHECK (qty > 0),
    price         NUMERIC,
    assumed_price NUMERIC,
    slippage_bps  NUMERIC,
    note          TEXT,
    shadow        BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills (ts);

CREATE TABLE IF NOT EXISTS decisions (
    run_date   DATE NOT NULL,
    step       TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    status     TEXT NOT NULL DEFAULT 'ok',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_date, step)
);

CREATE TABLE IF NOT EXISTS equity_series (
    strategy TEXT NOT NULL,
    as_of    DATE NOT NULL,
    equity   NUMERIC NOT NULL,
    PRIMARY KEY (strategy, as_of)
);

CREATE TABLE IF NOT EXISTS events (
    id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    type   TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events (type, ts);

CREATE TABLE IF NOT EXISTS step_completions (
    run_date DATE NOT NULL,
    step     TEXT NOT NULL,
    ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_date, step)
);
