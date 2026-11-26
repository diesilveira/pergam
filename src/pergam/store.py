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


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class PergamStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def publish(self, pergam: NewPergam, existing_id: str | None) -> tuple[str, int]:
        """Insert a new version and return `(id, version)`."""
        bytes_count = len(pergam.html.encode("utf-8"))
        with self.pool.connection() as conn:
            if existing_id:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM pergams WHERE id = %s",
                    (existing_id,),
                ).fetchone()
                next_version = (row[0] or 0) + 1
                pergam_id = existing_id
            else:
                next_version = 1
                pergam_id = new_id()
                while conn.execute(
                    "SELECT 1 FROM pergams WHERE id = %s LIMIT 1", (pergam_id,)
                ).fetchone():
                    pergam_id = new_id()

            conn.execute(
                """
                INSERT INTO pergams (id, version, title, html, type, author, bytes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (pergam_id, next_version, pergam.title, pergam.html, pergam.type, pergam.author, bytes_count),
            )
        return pergam_id, next_version

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
