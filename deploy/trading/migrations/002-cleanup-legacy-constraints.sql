-- 002-cleanup-legacy-constraints.sql — drop the transitional single-column
-- UNIQUE constraints from 001. Run AFTER every trading function runs
-- 0.2.0+ AND BEFORE creating the second book (UNIQUE(symbol) would reject
-- a v228 row for a symbol the pilot already holds).
--
-- Precondition check: no rows outside book='pilot' yet.

BEGIN;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM positions WHERE book <> 'pilot')
     OR EXISTS (SELECT 1 FROM decisions WHERE book <> 'pilot')
     OR EXISTS (SELECT 1 FROM step_completions WHERE book <> 'pilot') THEN
    RAISE EXCEPTION 'multi-book rows exist; legacy cleanup must happen before second book bootstrap';
  END IF;
END $$;

DROP INDEX IF EXISTS idx_positions_symbol_legacy;
DROP INDEX IF EXISTS idx_decisions_run_step_legacy;
DROP INDEX IF EXISTS idx_step_completions_run_step_legacy;

COMMIT;
