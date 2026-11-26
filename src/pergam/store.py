"""Data access: every SQL statement the HTTP layer needs, over a connection pool."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from pergam.cursor import IndexCursor
from pergam.meta import Meta, MetaChange, Origin
from pergam.search import HEADLINE_SQL, INSERT_TEXT, MATCHES_SQL, Snippet, search_params
from pergam.tags import merge_tags
from pergam.text import EXTRACTOR_VERSION, PageText, extract_text
from pergam.transfer import ExportedVersion, ImportDocument


@dataclass(frozen=True)
class NewPergam:
    title: str
    html: str
    type: str
    author: str
    tags: list[str] | None = None
    note: str | None = None


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
    note: str | None = None


@dataclass(frozen=True)
class ImportResult:
    imported: list[int]
    skipped: list[int]
    origin: Origin | None


class ImportConflict(Exception):
    """The import disagrees with the versions this server already has; nothing was written."""


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


@dataclass(frozen=True)
class FeedEntry:
    id: str
    version: int
    title: str
    type: str
    author: str
    created_at: datetime
    tags: list[str]
    summary: str | None


PAGE_SIZE = 100
FEED_SIZE = 50
SUMMARY_CHARS = 280

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


_EXPORTED_VERSIONS = """
    SELECT p.version, p.title, p.html, p.type, p.author, n.note, p.bytes, p.created_at
    FROM pergams p LEFT JOIN pergam_note n ON n.id = p.id AND n.version = p.version
    WHERE p.id = %s ORDER BY p.version
"""


def exported_versions(conn: psycopg.Connection, pergam_id: str) -> list[ExportedVersion]:
    return [ExportedVersion(*row) for row in conn.execute(_EXPORTED_VERSIONS, (pergam_id,)).fetchall()]


def import_conflict(doc: ImportDocument, present: dict[int, ExportedVersion]) -> str | None:
    """Why `doc` cannot be imported over `present`, or None when it can.

    The merged versions must number 1..n and, wherever an imported version
    borders another, keep `created_at` non-decreasing. Gaps are found by
    walking the sorted numbers, never by enumerating up to the largest one.
    """
    differing = [v.version for v in doc.versions if v.version in present and not present[v.version].same_content(v)]
    if differing:
        listed = ", ".join(f"v{n}" for n in differing)
        return f"{listed} of {doc.id} already exist here with different content"
    merged = {v.version: v for v in doc.versions} | present
    numbers = sorted(merged)
    gap = next((expected for expected, number in enumerate(numbers, 1) if number != expected), None)
    if gap is not None:
        return f"importing would leave v{gap} of {doc.id} missing"
    for older, newer in zip(numbers, numbers[1:]):
        if (older not in present or newer not in present) and merged[newer].created_at < merged[older].created_at:
            return f"v{newer} of {doc.id} would be dated before v{older}"
    return None


def extract_or_empty(pergam_id: str, version: int, html: str) -> PageText:
    """The extracted text, or empty text after logging when the extractor fails on this HTML.

    One bad document must neither fail its publish nor stop the backfill; its row
    is stored empty (still found by title) and retried when the extractor version changes.
    """
    try:
        return extract_text(html)
    except Exception as exc:
        print(f"ERROR: text extraction failed for {pergam_id} v{version}: {exc!r}", file=sys.stderr, flush=True)
        return PageText(markdown="", plain="")


def save_text(conn: psycopg.Connection, versions: list[tuple[str, int, str]]) -> None:
    """Store the plain extracted text and search document of each `(id, version, html)`.

    An existing row is replaced only when it was written by an older extractor.
    """
    with conn.cursor() as cur:
        cur.executemany(
            INSERT_TEXT,
            [
                {
                    "id": pergam_id,
                    "version": version,
                    "body": extract_or_empty(pergam_id, version, html).plain,
                    "extractor": EXTRACTOR_VERSION,
                }
                for pergam_id, version, html in versions
            ],
        )


class PergamStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def publish(self, pergam: NewPergam, existing_id: str | None) -> Published:
        """Insert a new version and, when `pergam.tags` is given, merge them into the id's tags.

        Posts to an explicit id take a transaction-scoped advisory lock on that id
        before reading `MAX(version)`, so concurrent posts get consecutive versions
        instead of colliding on the primary key. New random ids retry on collision.
        A tag merge that fails (`TagError`) rolls the version back with it. The note,
        when given, is written in the same transaction as its version.
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
            if pergam.note:
                conn.execute(
                    "INSERT INTO pergam_note (id, version, note) VALUES (%s, %s, %s)", (pergam_id, version, pergam.note)
                )
            tags = None if pergam.tags is None else add_tags(conn, pergam_id, pergam.tags)
        return Published(pergam_id, version, tags)

    def meta(self, pergam_id: str) -> Meta | None:
        """Metadata of an id, defaults when it has no row; None when the id has no versions."""
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(m.tags, '{}'), COALESCE(m.archived, false), COALESCE(m.pinned, false),
                       m.origin_url, m.origin_id
                FROM (SELECT DISTINCT id FROM pergams WHERE id = %(id)s) p
                LEFT JOIN pergam_meta m ON m.id = p.id
                """,
                {"id": pergam_id},
            ).fetchone()
        if row is None:
            return None
        tags, archived, pinned, origin_url, origin_id = row
        origin = Origin(origin_url, origin_id) if origin_url and origin_id else None
        return Meta(tags, archived, pinned, origin)

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

    def export(self, pergam_id: str) -> list[ExportedVersion]:
        """Every version of an id with its HTML and note, oldest first; empty for an unknown id."""
        with self.pool.connection() as conn:
            return exported_versions(conn, pergam_id)

    def import_pergam(self, doc: ImportDocument) -> ImportResult:
        """Write the versions of `doc` this server lacks, keeping their numbers and `created_at`.

        One transaction under the id's advisory lock: versions already present with
        the same content are skipped, and when any present version differs, or the
        result would leave a version number missing, `ImportConflict` is raised
        before anything is written. Tags are merged as on publish; `archived`,
        `pinned` and `origin` are set only when the id is new to this server.
        """
        with self.pool.connection() as conn, conn.transaction():
            lock_id(conn, doc.id)
            present = {v.version: v for v in exported_versions(conn, doc.id)}
            conflict = import_conflict(doc, present)
            if conflict:
                raise ImportConflict(conflict)
            missing = [v for v in doc.versions if v.version not in present]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO pergams (id, version, title, html, type, author, bytes, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [(doc.id, v.version, v.title, v.html, v.type, v.author, v.bytes, v.created_at) for v in missing],
                )
                cur.executemany(
                    "INSERT INTO pergam_note (id, version, note) VALUES (%s, %s, %s)",
                    [(doc.id, v.version, v.note) for v in missing if v.note],
                )
            save_text(conn, [(doc.id, v.version, v.html) for v in missing])
            if present:
                if doc.tags:
                    add_tags(conn, doc.id, doc.tags)
                origin = self._origin(conn, doc.id)
            else:
                origin = doc.origin
                conn.execute(
                    "INSERT INTO pergam_meta (id, tags, archived, pinned, origin_url, origin_id)"
                    " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET tags = EXCLUDED.tags,"
                    " archived = EXCLUDED.archived, pinned = EXCLUDED.pinned,"
                    " origin_url = EXCLUDED.origin_url, origin_id = EXCLUDED.origin_id",
                    (doc.id, doc.tags, doc.archived, doc.pinned, origin and origin.url, origin and origin.id),
                )
        return ImportResult([v.version for v in missing], sorted(set(present) & {v.version for v in doc.versions}), origin)

    @staticmethod
    def _origin(conn: psycopg.Connection, pergam_id: str) -> Origin | None:
        row = conn.execute("SELECT origin_url, origin_id FROM pergam_meta WHERE id = %s", (pergam_id,)).fetchone()
        return Origin(*row) if row and row[0] and row[1] else None

    def versions(self, pergam_id: str) -> list[VersionInfo]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT p.version, p.title, p.type, p.author, p.bytes, p.created_at, n.note
                FROM pergams p LEFT JOIN pergam_note n ON n.id = p.id AND n.version = p.version
                WHERE p.id = %s ORDER BY p.version DESC
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

    def feed(self, tag: str | None, author: str | None, limit: int = FEED_SIZE) -> list[FeedEntry]:
        """The newest versions of pergams that are not archived, each publication its own entry."""
        where = "WHERE NOT COALESCE(m.archived, false)"
        params: dict[str, Any] = {"limit": limit, "summary_chars": SUMMARY_CHARS}
        if tag:
            where += " AND m.tags @> ARRAY[%(tag)s]::text[]"
            params["tag"] = tag
        if author:
            where += " AND p.author = %(author)s"
            params["author"] = author
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT p.id, p.version, p.title, p.type, p.author, p.created_at, COALESCE(m.tags, '{{}}'),
                       left(t.body, %(summary_chars)s)
                FROM pergams p
                LEFT JOIN pergam_meta m ON m.id = p.id
                LEFT JOIN pergam_text t ON t.id = p.id AND t.version = p.version
                {where}
                ORDER BY p.created_at DESC, p.id DESC, p.version DESC
                LIMIT %(limit)s
                """,
                params,
            ).fetchall()
        return [FeedEntry(*row) for row in rows]
