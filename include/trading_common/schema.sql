-- Trading system schema (Fission port of lenaxia/ai-trading state files).
--
-- MULTI-BOOK: every table that belongs to a book is namespaced by `book`
-- (pilot = momentum book, v228 = mean-rev-vol book, ...). Strategies share
-- one database and never see each other's rows; db.py scopes every query
-- by the BOOK secret of the pod it runs in. equity_series is research
-- data (control / fwd:* replay curves) and is strategy-keyed, not booked.
--
-- Idempotent: applied on every function connect (CREATE IF NOT EXISTS), in
-- the same spirit as repo-assessor's postgres backend. Kept in sync with
-- include/trading_common/db.py. The live-DB migration that matches this
-- shape lives in deploy/trading/migrations/001-multi-book.sql — run it
-- BEFORE deploying code that expects the book columns (fresh clusters
-- don't need it; CREATE IF NOT EXISTS below already has the columns).
--
-- Tables:
--   positions         current books (qty/entry per book+symbol)
--   fills             executed (live) or assumed (shadow) fills, per book
--   decisions         per-run decision payloads, per book
--   equity_series     strategy equity curves (control, fwd:<variant>, fwd:SPY)
--   events            append-only audit log (chain runs, halts, reconciles..)
--   step_completions  idempotency + watchdog: (book, run_date, step) markers

CREATE TABLE IF NOT EXISTS positions (
    book         TEXT NOT NULL DEFAULT 'pilot',
    symbol       TEXT NOT NULL,
    qty          NUMERIC NOT NULL DEFAULT 0,
    entry        NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (book, symbol)
);

CREATE TABLE IF NOT EXISTS fills (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book          TEXT NOT NULL DEFAULT 'pilot',
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
    book       TEXT NOT NULL DEFAULT 'pilot',
    run_date   DATE NOT NULL,
    step       TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    status     TEXT NOT NULL DEFAULT 'ok',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (book, run_date, step)
);

CREATE TABLE IF NOT EXISTS equity_series (
    strategy TEXT NOT NULL,
    as_of    DATE NOT NULL,
    equity   NUMERIC NOT NULL,
    PRIMARY KEY (strategy, as_of)
);

CREATE TABLE IF NOT EXISTS events (
    id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book   TEXT NOT NULL DEFAULT 'pilot',
    ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    type   TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_book_type_ts ON events (book, type, ts);

CREATE TABLE IF NOT EXISTS step_completions (
    book     TEXT NOT NULL DEFAULT 'pilot',
    run_date DATE NOT NULL,
    step     TEXT NOT NULL,
    ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (book, run_date, step)
);
