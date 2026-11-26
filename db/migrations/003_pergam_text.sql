-- Extracted text and search document of every version, behind `?q=` on the index.
-- A side table keyed like `pergams`, so `pergams` keeps its schema and its rows
-- are never rewritten. The server fills it on publish and, after migrating,
-- backfills the versions that predate it (idempotent, in batches, resumable).
-- `document` is `simple` (language-neutral), the title weighted above the body.
-- Needs CREATE on the schema, unless the owner created `pergam_text` beforehand
-- (with SELECT, INSERT granted to the app role): then no DDL runs.

DO $$
BEGIN
    IF to_regclass('pergam_text') IS NOT NULL THEN
        RETURN;
    END IF;

    CREATE TABLE pergam_text (
        id        text     NOT NULL,
        version   int      NOT NULL,
        body      text     NOT NULL,
        document  tsvector NOT NULL,
        PRIMARY KEY (id, version)
    );

    CREATE INDEX pergam_text_document ON pergam_text USING gin (document);
END
$$;
