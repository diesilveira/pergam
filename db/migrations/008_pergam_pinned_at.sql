-- When each pergam was pinned, so the index orders its pinned cards most recent
-- first. One nullable column on `pergam_meta`: adding it rewrites no rows; pergams
-- pinned before it read as pinned at an unknown time (they sort after the dated
-- ones) and no backfill runs; `pergams` is untouched.
-- Needs ownership of `pergam_meta` (the role that ran 002 owns it), unless the
-- owner added the column beforehand: then no DDL runs.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'pergam_meta'::regclass AND attname = 'pinned_at' AND NOT attisdropped
    ) THEN
        RETURN;
    END IF;

    ALTER TABLE pergam_meta ADD COLUMN pinned_at timestamptz;
END
$$;
