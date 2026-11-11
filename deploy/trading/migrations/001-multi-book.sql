-- 001-multi-book.sql — namespace the single-book tables by `book`.
--
-- Run ONCE against the live trading DB. Safe to run BEFORE or AFTER the
-- 0.2.0 code deploy: the schema below works for BOTH generations.
--   - new code (0.2.0+): filters/supplies book everywhere, conflicts on
--     the composite PKs.
--   - old code (<=0.1.4): inserts default book='pilot' and conflicts on
--     the TRANSITIONAL single-column UNIQUE constraints kept below.
-- All existing rows default to book='pilot' (the momentum book).
--
-- Follow-up 002-cleanup.sql drops the transitional constraints once every
-- function runs 0.2.0+ (and MUST run before a second book is created —
-- UNIQUE(symbol) would forbid a v228 row for a symbol the pilot holds).
--
-- Idempotent via DO guards / IF NOT EXISTS.

BEGIN;

ALTER TABLE positions        ADD COLUMN IF NOT EXISTS book TEXT NOT NULL DEFAULT 'pilot';
ALTER TABLE fills            ADD COLUMN IF NOT EXISTS book TEXT NOT NULL DEFAULT 'pilot';
ALTER TABLE decisions        ADD COLUMN IF NOT EXISTS book TEXT NOT NULL DEFAULT 'pilot';
ALTER TABLE events           ADD COLUMN IF NOT EXISTS book TEXT NOT NULL DEFAULT 'pilot';
ALTER TABLE step_completions ADD COLUMN IF NOT EXISTS book TEXT NOT NULL DEFAULT 'pilot';

-- positions: old PK(symbol) -> UNIQUE(symbol) + new PK(book, symbol)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'positions_pkey')
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'positions_book_pkey') THEN
    ALTER TABLE positions DROP CONSTRAINT positions_pkey;
    ALTER TABLE positions ADD CONSTRAINT positions_book_pkey PRIMARY KEY (book, symbol);
  END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_symbol_legacy ON positions (symbol);

-- decisions: old PK(run_date, step) -> UNIQUE(run_date, step) + new PK
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_pkey')
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decisions_book_pkey') THEN
    ALTER TABLE decisions DROP CONSTRAINT decisions_pkey;
    ALTER TABLE decisions ADD CONSTRAINT decisions_book_pkey PRIMARY KEY (book, run_date, step);
  END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_run_step_legacy ON decisions (run_date, step);

-- step_completions: same pattern
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'step_completions_pkey')
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'step_completions_book_pkey') THEN
    ALTER TABLE step_completions DROP CONSTRAINT step_completions_pkey;
    ALTER TABLE step_completions ADD CONSTRAINT step_completions_book_pkey PRIMARY KEY (book, run_date, step);
  END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_step_completions_run_step_legacy ON step_completions (run_date, step);

DROP INDEX IF EXISTS idx_events_type_ts;
CREATE INDEX IF NOT EXISTS idx_events_book_type_ts ON events (book, type, ts);

COMMIT;
