"""Data access: every SQL statement the HTTP layer needs, over a connection pool."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class NewPergam:
    title: str
    html: str
    type: str
    author: str


@dataclass(frozen=True)
class VersionInfo:
    version: int
    title: str
    type: str
    author: str
    bytes: int
    created_at: datetime


@dataclass(frozen=True)
class IndexFilters:
    type: str | None = None
    author: str | None = None
    q: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.type or self.author or self.q)


@dataclass(frozen=True)
class IndexRow:
    id: str
    version: int
    title: str
    type: str
    author: str
    created_at: datetime
    bytes: int
    total_versions: int


@dataclass(frozen=True)
class IndexStats:
    total_pergams: int
    total_authors: int
    total_versions: int
    total_bytes: int
    new_pergams_7d: int
    new_versions_7d: int


@dataclass(frozen=True)
class IndexData:
    rows: list[IndexRow]
    type_counts: list[tuple[str, int]]
    author_counts: list[tuple[str, int]]
    stats: IndexStats


VERSION_LOCK_NAMESPACE = 0x7067


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class PergamStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def publish(self, pergam: NewPergam, existing_id: str | None) -> tuple[str, int]:
        """Insert a new version and return `(id, version)`.

        Posts to an explicit id take a transaction-scoped advisory lock on that id
        before reading `MAX(version)`, so concurrent posts get consecutive versions
        instead of colliding on the primary key. New random ids retry on collision.
        """
        values = (pergam.title, pergam.html, pergam.type, pergam.author, len(pergam.html.encode("utf-8")))
        with self.pool.connection() as conn, conn.transaction():
            if existing_id:
                conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (VERSION_LOCK_NAMESPACE, existing_id))
                row = conn.execute(
                    "INSERT INTO pergams (id, version, title, html, type, author, bytes)"
                    " SELECT %s, COALESCE(MAX(version), 0) + 1, %s, %s, %s, %s, %s FROM pergams WHERE id = %s"
                    " RETURNING version",
                    (existing_id, *values, existing_id),
                ).fetchone()
                return existing_id, row[0]
            while True:
                pergam_id = new_id()
                inserted = conn.execute(
                    "INSERT INTO pergams (id, version, title, html, type, author, bytes)"
                    " VALUES (%s, 1, %s, %s, %s, %s, %s) ON CONFLICT (id, version) DO NOTHING RETURNING id",
                    (pergam_id, *values),
                ).fetchone()
                if inserted:
                    return pergam_id, 1

    def html(self, pergam_id: str, version: int | None) -> str | None:
        """HTML of one version, or of the latest one when `version` is None."""
        with self.pool.connection() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT html FROM pergams WHERE id = %s ORDER BY version DESC LIMIT 1",
                    (pergam_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT html FROM pergams WHERE id = %s AND version = %s",
                    (pergam_id, version),
                ).fetchone()
        return row[0] if row else None

    def versions(self, pergam_id: str) -> list[VersionInfo]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT version, title, type, author, bytes, created_at
                FROM pergams WHERE id = %s ORDER BY version DESC
                """,
                (pergam_id,),
            ).fetchall()
        return [VersionInfo(*row) for row in rows]

    def index(self, filters: IndexFilters) -> IndexData:
        sql = """
            SELECT l.id, l.version, l.title, l.type, l.author, l.created_at, l.bytes,
                   (SELECT count(*) FROM pergams p WHERE p.id = l.id) AS total_versions
            FROM pergams_latest l
            WHERE 1=1
        """
        params: list[Any] = []
        if filters.type:
            sql += " AND l.type = %s"
            params.append(filters.type)
        if filters.author:
            sql += " AND l.author = %s"
            params.append(filters.author)
        if filters.q:
            sql += " AND l.title ILIKE %s"
            params.append(f"%{filters.q}%")
        sql += " ORDER BY l.created_at DESC LIMIT 500"

        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            type_counts = conn.execute(
                "SELECT type, count(*) FROM pergams_latest GROUP BY type ORDER BY 2 DESC, 1"
            ).fetchall()
            author_counts = conn.execute(
                "SELECT author, count(*) FROM pergams_latest GROUP BY author ORDER BY 2 DESC, 1"
            ).fetchall()
            stats = conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM pergams_latest),
                  (SELECT count(DISTINCT author) FROM pergams_latest),
                  (SELECT count(*) FROM pergams),
                  (SELECT COALESCE(SUM(bytes), 0) FROM pergams),
                  (SELECT count(*) FROM pergams_latest WHERE created_at >= now() - interval '7 days'),
                  (SELECT count(*) FROM pergams         WHERE created_at >= now() - interval '7 days')
                """
            ).fetchone()

        return IndexData(
            rows=[IndexRow(*row) for row in rows],
            type_counts=[(t, c) for t, c in type_counts],
            author_counts=[(a, c) for a, c in author_counts],
            stats=IndexStats(*stats),
        )
