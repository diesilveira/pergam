-- pergam schema. Loaded on first postgres init via docker-entrypoint-initdb.d.
-- Idempotent (IF NOT EXISTS) so re-running on an existing DB is safe.

CREATE TABLE IF NOT EXISTS pergams (
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

CREATE INDEX IF NOT EXISTS pergams_id_version_desc ON pergams (id, version DESC);
CREATE INDEX IF NOT EXISTS pergams_type            ON pergams (type);
CREATE INDEX IF NOT EXISTS pergams_author          ON pergams (author);
CREATE INDEX IF NOT EXISTS pergams_created_at_desc ON pergams (created_at DESC);

CREATE OR REPLACE VIEW pergams_latest AS
SELECT DISTINCT ON (id) id, version, title, type, author, bytes, created_at
FROM pergams
ORDER BY id, version DESC;
