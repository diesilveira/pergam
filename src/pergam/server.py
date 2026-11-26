#!/usr/bin/env python3
"""pergam server — postgres-backed, versioned, immutable.

Entry point: `python3 server.py` (the Docker image runs `/app/pergam/server.py`).
It applies pending schema migrations, then serves HTTP until interrupted. When
run as a script the package's parent directory replaces the script directory on
`sys.path`, so the sibling modules import as `pergam.*`.

Modules
-------
  routes.py      HTTP routing and request validation
  store.py       data access (all SQL)
  index_page.py  the HTML index, from templates/index.html + index.css
  view_page.py   the view page: version bar and the document in a frame
  diff.py        text diff of two versions, grouped by section
  diff_page.py   the diff page, from templates/diff.html + diff.css
  public_url.py  host and URL used in `view_url` and on the index
  limits.py      largest request body
  identity.py    the writer, from X-Forwarded-User when trusted
  static_files.py files served under /static/ (bridge, vendored Mermaid)
  taxonomy.py    accepted types and type badge colours
  migrate.py     numbered migrations applied at startup
  text.py        visible text of a pergam as Markdown
  search.py      full-text search SQL and snippets
  backfill.py    text of versions without text or with stale text

Env (12-factor)
---------------
  HOST           bind host                     (default 0.0.0.0)
  PORT           bind port                     (default 1111)
  DATABASE_URL   postgres connection string    (required)
  PUBLIC_URL     base of returned URLs         (e.g. https://docs.example.com)
  PUBLIC_HOST    host when PUBLIC_URL is unset (default localhost)
  PUBLIC_PORT    port when PUBLIC_URL is unset (default PORT)
  MIGRATIONS_DIR directory of NNN_*.sql files  (default <repo>/db/migrations)
  PERGAM_TYPES   accepted types, comma separated (default the five built-in ones)
  PERGAM_MAX_BODY_MB largest request body in MB (default 256)
  TRUST_PROXY_USER author from X-Forwarded-User, owners enforced (default off)
"""

from __future__ import annotations

import http.server
import os
import socketserver
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

import psycopg
from psycopg_pool import ConnectionPool

from pergam import identity, limits, public_url, taxonomy
from pergam.view_page import frame_policy
from pergam.backfill import run_backfill
from pergam.migrate import MigrationError, apply_migrations, migrations_dir
from pergam.routes import PergamHandler
from pergam.store import PergamStore

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 1111


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded server with a listen backlog sized for bursts of concurrent clients."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is required", file=sys.stderr)
        return 2

    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    PergamHandler.types = taxonomy.allowed_types(os.environ)
    try:
        PergamHandler.public = public_url.resolve(os.environ, port)
        PergamHandler.max_body_bytes = limits.max_body_bytes(os.environ)
        PergamHandler.trust_proxy_user = identity.trusts_proxy_user(os.environ)
        PergamHandler.frame = frame_policy(PergamHandler.public.origin)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    pool = ConnectionPool(db_url, min_size=1, max_size=8, kwargs={"autocommit": True}, open=True)
    pool.wait(timeout=10.0)
    try:
        with pool.connection() as conn:
            applied = apply_migrations(conn, migrations_dir())
    except (MigrationError, psycopg.Error) as exc:
        print(f"ERROR: migrations failed, not starting: {exc}", file=sys.stderr)
        pool.close()
        return 1
    for migration in applied:
        print(f"applied migration {migration.name}", flush=True)
    PergamHandler.store = PergamStore(pool)
    threading.Thread(target=run_backfill, args=(pool,), name="text-backfill", daemon=True).start()

    with ThreadingHTTPServer((host, port), PergamHandler) as httpd:
        print(f"pergam listening on http://{host}:{port}  (db connected)", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down", flush=True)
        finally:
            pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
