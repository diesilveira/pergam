-- Optional note of a version ("adds the costs section"), set when it is posted
-- and never changed. A side table keyed like `pergams`, holding a row only for
-- versions posted with a note, so `pergams` keeps its schema, its rows are never
-- rewritten and no backfill runs: a version without a row has no note.
-- Needs CREATE on the schema, unless the owner created `pergam_note` beforehand
-- (with SELECT, INSERT granted to the app role): then no DDL runs.

DO $$
BEGIN
    IF to_regclass('pergam_note') IS NOT NULL THEN
        RETURN;
    END IF;

    CREATE TABLE pergam_note (
        id       text NOT NULL,
        version  int  NOT NULL,
        note     text NOT NULL,
        PRIMARY KEY (id, version)
    );
END
$$;
