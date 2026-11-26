-- Per-id metadata that changes without creating a version. Purely additive: ids
-- without a row read as no tags, not archived, not pinned, so no backfill runs.
-- Needs CREATE on the schema, unless the owner created `pergam_meta` beforehand
-- (with SELECT, INSERT, UPDATE granted to the app role): then no DDL runs.

DO $$
BEGIN
    IF to_regclass('pergam_meta') IS NOT NULL THEN
        RETURN;
    END IF;

    CREATE TABLE pergam_meta (
        id        text PRIMARY KEY,
        tags      text[]  NOT NULL DEFAULT '{}',
        archived  boolean NOT NULL DEFAULT false,
        pinned    boolean NOT NULL DEFAULT false
    );

    CREATE INDEX pergam_meta_tags ON pergam_meta USING gin (tags);
END
$$;
