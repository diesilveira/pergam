"""Numbered SQL migrations, applied when the server starts.

Files named `NNN_description.sql` are applied in numeric order and recorded in
`schema_migrations`. Each migration runs in its own transaction, so a failure
rolls that migration back completely and is raised to the caller, which aborts
startup. A session advisory lock serializes servers that start concurrently
against the same database.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

ADVISORY_LOCK_KEY = 0x70657267616D
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_FILENAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

_CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     int PRIMARY KEY,
    name        text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def migrations_dir() -> Path:
    return Path(os.environ.get("MIGRATIONS_DIR") or DEFAULT_MIGRATIONS_DIR)


def discover(directory: Path) -> list[Migration]:
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            raise MigrationError(f"migration file name must be NNN_name.sql: {path.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"duplicate migration version {version}: {path.name}")
        found[version] = Migration(version, path.stem, path)
    return [found[v] for v in sorted(found)]


def apply_migrations(conn: psycopg.Connection, directory: Path) -> list[Migration]:
    """Apply pending migrations on an autocommit connection; return the ones applied."""
    if not conn.autocommit:
        raise MigrationError("apply_migrations needs an autocommit connection")
    migrations = discover(directory)
    conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    try:
        conn.execute(_CREATE_LEDGER)
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        newly_applied: list[Migration] = []
        for migration in migrations:
            if migration.version in applied:
                continue
            with conn.transaction():
                conn.execute(sql.SQL(migration.path.read_text(encoding="utf-8")))
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                    (migration.version, migration.name),
                )
            newly_applied.append(migration)
        return newly_applied
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
