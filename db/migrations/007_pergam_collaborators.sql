-- Who besides its owner (the author of v1) may add versions to a pergam and edit
-- its metadata when the server trusts its proxy for identity: a list the owner
-- edits with `PUT /{id}/collaborators`. One nullable column on `pergam_meta`:
-- adding it rewrites no rows, existing rows read as no collaborators and no
-- backfill runs; `pergams` is untouched.
-- Needs ownership of `pergam_meta` (the role that ran 002 owns it), unless the
-- owner added the column beforehand: then no DDL runs.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'pergam_meta'::regclass AND attname = 'collaborators' AND NOT attisdropped
    ) THEN
        RETURN;
    END IF;

    ALTER TABLE pergam_meta ADD COLUMN collaborators text[];
END
$$;
