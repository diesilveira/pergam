"""Fill `pergam_text` for versions published before it existed.

The server runs this in a background thread after migrating, so it answers
requests meanwhile; until a version is filled, `?q=` still finds it by title.
Each batch is its own transaction and inserts with `ON CONFLICT DO NOTHING`, so
stopping the server at any point loses at most one batch, a restart resumes
with what is still missing, and concurrent servers or publishes never clash.
`pergams` is only read.
"""

from __future__ import annotations

import sys

import psycopg
from psycopg_pool import ConnectionPool

from pergam.store import save_text

BATCH_SIZE = 50

_MISSING = """
    SELECT p.id, p.version, p.html FROM pergams p
    WHERE (p.id, p.version) > (%s, %s)
      AND NOT EXISTS (SELECT 1 FROM pergam_text t WHERE t.id = p.id AND t.version = p.version)
    ORDER BY p.id, p.version
    LIMIT %s
"""


def backfill_text(pool: ConnectionPool, batch_size: int = BATCH_SIZE) -> int:
    """Extract and store the text of every version that has none; return how many were filled."""
    filled = 0
    after: tuple[str, int] = ("", 0)
    while True:
        with pool.connection() as conn, conn.transaction():
            rows = conn.execute(_MISSING, (*after, batch_size)).fetchall()
            if not rows:
                return filled
            save_text(conn, rows)
        filled += len(rows)
        after = (rows[-1][0], rows[-1][1])


def run_backfill(pool: ConnectionPool) -> None:
    """Thread target: log the outcome instead of raising, the server keeps serving either way."""
    try:
        filled = backfill_text(pool)
    except psycopg.Error as exc:
        print(f"ERROR: text backfill stopped, it resumes on the next start: {exc}", file=sys.stderr, flush=True)
        return
    if filled:
        print(f"text backfill: filled {filled} versions", flush=True)
