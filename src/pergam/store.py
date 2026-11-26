"""Data access: every SQL statement the HTTP layer needs, over a connection pool."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from pergam.cursor import IndexCursor
from pergam.meta import Meta, MetaChange
from pergam.search import HEADLINE_SQL, INSERT_TEXT, MATCHES_SQL, Snippet, search_params
from pergam.tags import merge_tags
from pergam.text import extract_markdown


@dataclass(frozen=True)
class NewPergam:
    title: str
    html: str
    type: str
    author: str
    tags: list[str] | None = None


@dataclass(frozen=True)
class Published:
    id: str
    version: int
    tags: list[str] | None


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
    tag: str | None = None
    q: str | None = None
    archived: bool = False
    cursor: IndexCursor | None = None

    @property
    def active(self) -> bool:
        return bool(self.type or self.author or self.tag or self.q)


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
    tags: list[str] = field(default_factory=list)
    archived: bool = False
    pinned: bool = False
    snippet: Snippet | None = None

    @property
    def cursor(self) -> IndexCursor:
        return IndexCursor(self.pinned, self.created_at, self.id)


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
    tag_counts: list[tuple[str, int]]
    stats: IndexStats
    matching: int
    archived_count: int
    next_cursor: IndexCursor | None


PAGE_SIZE = 100

VERSION_LOCK_NAMESPACE = 0x7067


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def lock_id(conn: psycopg.Connection, pergam_id: str) -> None:
    """Serialize writers of one id (new versions and metadata) until the transaction ends."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (VERSION_LOCK_NAMESPACE, pergam_id))


def insert_with_new_id(conn: psycopg.Connection, values: tuple[Any, ...]) -> str:
    while True:
        pergam_id = new_id()
        inserted = conn.execute(
            "INSERT INTO pergams (id, version, title, html, type, author, bytes)"
            " VALUES (%s, 1, %s, %s, %s, %s, %s) ON CONFLICT (id, version) DO NOTHING RETURNING id",
            (pergam_id, *values),
        ).fetchone()
        if inserted:
            return pergam_id


def add_tags(conn: psycopg.Connection, pergam_id: str, tags: list[str]) -> list[str]:
    row = conn.execute("SELECT tags FROM pergam_meta WHERE id = %s", (pergam_id,)).fetchone()
    merged = merge_tags(row[0] if row else [], tags)
    conn.execute(
        "INSERT INTO pergam_meta (id, tags) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET tags = EXCLUDED.tags",
        (pergam_id, merged),
    )
    return merged


def save_text(conn: psycopg.Connection, versions: list[tuple[str, int, str]]) -> None:
    """Store the extracted text and search document of each `(id, version, html)`; existing rows win."""
    with conn.cursor() as cur:
        cur.executemany(
            INSERT_TEXT,
            [{"id": pergam_id, "version": version, "body": extract_markdown(html)} for pergam_id, version, html in versions],
        )


class PergamStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def publish(self, pergam: NewPergam, existing_id: str | None) -> Published:
        """Insert a new version and, when `pergam.tags` is given, merge them into the id's tags.

        Posts to an explicit id take a transaction-scoped advisory lock on that id
        before reading `MAX(version)`, so concurrent posts get consecutive versions
        instead of colliding on the primary key. New random ids retry on collision.
        A tag merge that fails (`TagError`) rolls the version back with it.
        """
        values = (pergam.title, pergam.html, pergam.type, pergam.author, len(pergam.html.encode("utf-8")))
        with self.pool.connection() as conn, conn.transaction():
            if existing_id:
                lock_id(conn, existing_id)
                row = conn.execute(
                    "INSERT INTO pergams (id, version, title, html, type, author, bytes)"
                    " SELECT %s, COALESCE(MAX(version), 0) + 1, %s, %s, %s, %s, %s FROM pergams WHERE id = %s"
                    " RETURNING version",
                    (existing_id, *values, existing_id),
                ).fetchone()
                pergam_id, version = existing_id, row[0]
            else:
                pergam_id, version = insert_with_new_id(conn, values), 1
            save_text(conn, [(pergam_id, version, pergam.html)])
            tags = None if pergam.tags is None else add_tags(conn, pergam_id, pergam.tags)
        return Published(pergam_id, version, tags)

    def meta(self, pergam_id: str) -> Meta | None:
        """Metadata of an id, defaults when it has no row; None when the id has no versions."""
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(m.tags, '{}'), COALESCE(m.archived, false), COALESCE(m.pinned, false)
                FROM (SELECT DISTINCT id FROM pergams WHERE id = %(id)s) p
                LEFT JOIN pergam_meta m ON m.id = p.id
                """,
                {"id": pergam_id},
            ).fetchone()
        return Meta(*row) if row else None

    def update_meta(self, pergam_id: str, change: MetaChange) -> Meta | None:
        """Apply `change` without touching any version; None when the id has no versions."""
        params = {"id": pergam_id, "tags": change.tags, "archived": change.archived, "pinned": change.pinned}
        with self.pool.connection() as conn, conn.transaction():
            lock_id(conn, pergam_id)
            if conn.execute("SELECT 1 FROM pergams WHERE id = %(id)s LIMIT 1", params).fetchone() is None:
                return None
            row = conn.execute(
                """
                INSERT INTO pergam_meta (id, tags, archived, pinned)
                VALUES (%(id)s, COALESCE(%(tags)s::text[], '{}'),
                        COALESCE(%(archived)s::boolean, false), COALESCE(%(pinned)s::boolean, false))
                ON CONFLICT (id) DO UPDATE SET
                    tags     = COALESCE(%(tags)s::text[], pergam_meta.tags),
                    archived = COALESCE(%(archived)s::boolean, pergam_meta.archived),
                    pinned   = COALESCE(%(pinned)s::boolean, pergam_meta.pinned)
                RETURNING tags, archived, pinned
                """,
                params,
            ).fetchone()
        return Meta(*row)

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

    def index(self, filters: IndexFilters, page_size: int = PAGE_SIZE) -> IndexData:
        """One page of the index plus the counts around it.

        Rows sort pinned first, then newest first, then by id, so the order is total
        and a page resumes strictly after `filters.cursor`. Pill counts cover every
        visible pergam (archived ones only with `filters.archived`), not just the page.
        """
        scope = "" if filters.archived else " AND NOT COALESCE(m.archived, false)"
        where = "WHERE true" + scope
        params: dict[str, Any] = {}
        if filters.type:
            where += " AND l.type = %(type)s"
            params["type"] = filters.type
        if filters.author:
            where += " AND l.author = %(author)s"
            params["author"] = filters.author
        if filters.tag:
            where += " AND m.tags @> ARRAY[%(tag)s]::text[]"
            params["tag"] = filters.tag
        if filters.q:
            where += f" AND {MATCHES_SQL}"
            params.update(search_params(filters.q))
        page_where = where
        if filters.cursor:
            page_where += " AND (COALESCE(m.pinned, false), l.created_at, l.id) < (%(c_pinned)s, %(c_at)s, %(c_id)s)"
            params.update(c_pinned=filters.cursor.pinned, c_at=filters.cursor.created_at, c_id=filters.cursor.id)
        params["limit"] = page_size + 1
        joined = "FROM pergams_latest l LEFT JOIN pergam_meta m ON m.id = l.id"
        searched = joined + " LEFT JOIN pergam_text t ON t.id = l.id AND t.version = l.version" if filters.q else joined
        headline = HEADLINE_SQL if filters.q else "NULL"

        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT l.id, l.version, l.title, l.type, l.author, l.created_at, l.bytes,
                       (SELECT count(*) FROM pergams p WHERE p.id = l.id),
                       COALESCE(m.tags, '{{}}'), COALESCE(m.archived, false), COALESCE(m.pinned, false),
                       {headline}
                {searched} {page_where}
                ORDER BY COALESCE(m.pinned, false) DESC, l.created_at DESC, l.id DESC
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()
            matching = conn.execute(f"SELECT count(*) {searched} {where}", params).fetchone()[0]
            type_counts = conn.execute(
                f"SELECT l.type, count(*) {joined} WHERE true{scope} GROUP BY 1 ORDER BY 2 DESC, 1"
            ).fetchall()
            author_counts = conn.execute(
                f"SELECT l.author, count(*) {joined} WHERE true{scope} GROUP BY 1 ORDER BY 2 DESC, 1"
            ).fetchall()
            tag_counts = conn.execute(
                f"SELECT t, count(*) {joined} CROSS JOIN unnest(m.tags) t WHERE true{scope} GROUP BY 1 ORDER BY 2 DESC, 1"
            ).fetchall()
            archived_count = conn.execute("SELECT count(*) " + joined + " WHERE m.archived").fetchone()[0]
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

        page = [IndexRow(*row[:-1], snippet=Snippet.of(row[-1])) for row in rows[:page_size]]
        return IndexData(
            rows=page,
            type_counts=[(t, c) for t, c in type_counts],
            author_counts=[(a, c) for a, c in author_counts],
            tag_counts=[(t, c) for t, c in tag_counts],
            stats=IndexStats(*stats),
            matching=matching,
            archived_count=archived_count,
            next_cursor=page[-1].cursor if len(rows) > page_size else None,
        )
