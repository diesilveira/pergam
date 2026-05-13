-- pergam schema. Loaded on first postgres init via docker-entrypoint-initdb.d.
-- Idempotent (IF NOT EXISTS) so re-running on an existing DB is safe.

CREATE TABLE IF NOT EXISTS grids (
    id          text NOT NULL,
    version     int  NOT NULL CHECK (version > 0),
    title       text NOT NULL,
    html        text NOT NULL,
    grid_type   text NOT NULL,
    author      text NOT NULL,
    bytes       int  NOT NULL CHECK (bytes > 0),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);

CREATE INDEX IF NOT EXISTS grids_id_version_desc ON grids (id, version DESC);
CREATE INDEX IF NOT EXISTS grids_grid_type       ON grids (grid_type);
CREATE INDEX IF NOT EXISTS grids_author          ON grids (author);
CREATE INDEX IF NOT EXISTS grids_created_at_desc ON grids (created_at DESC);

CREATE OR REPLACE VIEW grids_latest AS
SELECT DISTINCT ON (id) id, version, title, grid_type, author, bytes, created_at
FROM grids
ORDER BY id, version DESC;
