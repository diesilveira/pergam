-- Initial schema, identical to the former db/schema.sql. On a database that was
-- initialised from it (`pergams` already exists) this migration does nothing at
-- all: no DDL runs, so it needs no ownership and takes no locks.

DO $$
BEGIN
    IF to_regclass('pergams') IS NOT NULL THEN
        RETURN;
    END IF;

    CREATE TABLE pergams (
        id          text NOT NULL,
        version     int  NOT NULL CHECK (version > 0),
        title       text NOT NULL,
        html        text NOT NULL,
        type        text NOT NULL,
        author      text NOT NULL,
        bytes       int  NOT NULL CHECK (bytes > 0),
        created_at  timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (id, version)
    );

    CREATE INDEX pergams_id_version_desc ON pergams (id, version DESC);
    CREATE INDEX pergams_type            ON pergams (type);
    CREATE INDEX pergams_author          ON pergams (author);
    CREATE INDEX pergams_created_at_desc ON pergams (created_at DESC);

    CREATE VIEW pergams_latest AS
    SELECT DISTINCT ON (id) id, version, title, type, author, bytes, created_at
    FROM pergams
    ORDER BY id, version DESC;
END
$$;
