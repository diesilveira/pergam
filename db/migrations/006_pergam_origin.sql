-- Where an imported pergam came from: the exporting server's base URL and the
-- id it had there, set by `POST /import` when it creates the id and never
-- changed. Two nullable columns on `pergam_meta`: adding them rewrites no rows,
-- existing rows read as no origin and no backfill runs; `pergams` is untouched.
-- Needs ownership of `pergam_meta` (the role that ran 002 owns it), unless the
-- owner added the columns beforehand: then no DDL runs.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'pergam_meta'::regclass AND attname = 'origin_url' AND NOT attisdropped
    ) THEN
        RETURN;
    END IF;

    ALTER TABLE pergam_meta ADD COLUMN origin_url text, ADD COLUMN origin_id text;
END
$$;
