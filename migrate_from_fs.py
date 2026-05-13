#!/usr/bin/env python3
"""One-shot migration of filesystem-backed grids into Postgres.

Reads ./data/{id}.html and ./data/{id}.meta.json, inserts each as version=1
with the given author and grid_type. Idempotent: skips ids that already
exist in the DB.

Usage:
    GRID_DB_URL=postgresql://user:pass@host:5432/pergam \
    AUTHOR=you@example.com \
    GRID_TYPE=otro \
    python3 migrate_from_fs.py
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
import psycopg

DATA_DIR = Path(__file__).parent / "data"


def main() -> int:
    db_url = os.environ.get("GRID_DB_URL")
    if not db_url:
        print("ERROR: GRID_DB_URL required", file=sys.stderr)
        return 2
    author = os.environ.get("AUTHOR", "you@example.com")
    grid_type = os.environ.get("GRID_TYPE", "otro")

    inserted = skipped = errors = 0
    with psycopg.connect(db_url, autocommit=True) as conn:
        for html_file in sorted(DATA_DIR.glob("*.html")):
            grid_id = html_file.stem
            meta_file = DATA_DIR / f"{grid_id}.meta.json"

            exists = conn.execute(
                "SELECT 1 FROM grids WHERE id=%s LIMIT 1", (grid_id,)
            ).fetchone()
            if exists:
                print(f"  skip {grid_id} (exists)")
                skipped += 1
                continue

            try:
                html = html_file.read_text(encoding="utf-8")
                meta = {}
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        pass
                title = meta.get("title", grid_id)
                bytes_count = meta.get("bytes", len(html.encode("utf-8")))

                conn.execute(
                    """
                    INSERT INTO grids (id, version, title, html, grid_type, author, bytes, created_at)
                    VALUES (%s, 1, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now()))
                    """,
                    (grid_id, title, html, grid_type, author, bytes_count, meta.get("created_at")),
                )
                print(f"  insert {grid_id}  '{title[:60]}'  ({bytes_count} bytes)")
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR {grid_id}: {exc}", file=sys.stderr)
                errors += 1

    print(f"\nDone. inserted={inserted} skipped={skipped} errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
