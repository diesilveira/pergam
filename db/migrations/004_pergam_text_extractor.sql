-- Which extractor version wrote each `pergam_text` row. Rows written before this
-- column read as version 1. After migrating, the server's backfill re-extracts
-- every row older than its own extractor, so an extractor fix reaches text that
-- is already stored; `pergams` is still only read. Adding a column with a
-- constant default rewrites no rows.
-- Needs ownership of `pergam_text` (the role that ran 003 owns it), unless the
-- owner added the column beforehand: then no DDL runs.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'pergam_text'::regclass AND attname = 'extractor' AND NOT attisdropped
    ) THEN
        RETURN;
    END IF;

    ALTER TABLE pergam_text ADD COLUMN extractor int NOT NULL DEFAULT 1;
END
$$;
