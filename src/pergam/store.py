"""Data access: every SQL statement the HTTP layer needs, over a connection pool."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from pergam.access import Access, require_editor, require_import_authors, require_owner
from pergam.cursor import DEFAULT_SORT, Cursor, IndexCursor, TitleCursor
from pergam.meta import Meta, MetaChange, Origin
from pergam.pins import ARCHIVED_REASON, DEFAULT_MAX_PINNED, PinRefused, over_cap_reason
from pergam.search import HEADLINE_SQL, INSERT_TEXT, MATCHES_SQL, Snippet, search_params
from pergam.summary import SOURCE_CHARS, summarise
from pergam.tags import merge_tags
from pergam.text import EXTRACTOR_VERSION, PageText, extract_text
from pergam.transfer import ExportedVersion, ImportDocument, check_written_authors


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
    cursor: Cursor | None = None
    sort: str = DEFAULT_SORT

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
    summary: str | None = None
    title_key: str = ""
    pinned_at: datetime | None = None

    def cursor(self, sort: str) -> Cursor:
        if sort == "title":
            return TitleCursor(self.title_key, self.id)
        return IndexCursor(self.pinned, self.created_at, self.id)


@dataclass(frozen=True)
class IndexStats:
    total_pergams: int
    total_authors: int
    total_versions: int
    total_bytes: int
    new_pergams_7d: int
    new_versions_7d: int
    total_archived: int = 0
    total_pinned: int = 0
    max_pinned: int = DEFAULT_MAX_PINNED


@dataclass(frozen=True)
class IndexData:
    rows: list[IndexRow]
    type_counts: list[tuple[str, int]]
    author_counts: list[tuple[str, int]]
    tag_counts: list[tuple[str, int]]
    stats: IndexStats
    matching: int
    archived_count: int
    next_cursor: Cursor | None
    pinned: list[IndexRow] = field(default_factory=list)
    pinned_ids: frozenset[str] = frozenset()


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
LISTED = "FROM pergams_latest l LEFT JOIN pergam_meta m ON m.id = l.id"
SEARCHED = LISTED + " LEFT JOIN pergam_text t ON t.id = l.id AND t.version = l.version"
ORDERS = {
    "recent": (
        "COALESCE(m.pinned, false) DESC, l.created_at DESC, l.id DESC",
        "p.pinned DESC, p.created_at DESC, p.id DESC",
    ),
    "title": ("lower(l.title), l.id", "p.title_key, p.id"),
    "pinned": (
        "m.pinned_at DESC NULLS LAST, l.created_at DESC, l.id DESC",
        "p.pinned_at DESC NULLS LAST, p.created_at DESC, p.id DESC",
    ),
}
FEED_SIZE = 50
SUMMARY_CHARS = 280

VERSION_LOCK_NAMESPACE = 0x7067
PIN_LOCK_NAMESPACE = 0x7070


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


def claim_pin(conn: psycopg.Connection, pergam_id: str, limit: int) -> None:
    """Raise `PinRefused` unless one more pergam may be pinned; call before writing the pin.

    Every new pin takes one instance-wide lock until its transaction ends, so
    concurrent pins are counted one after another and never pass `limit`. Only
    pins of pergams that are not archived count.
    """
    conn.execute("SELECT pg_advisory_xact_lock(%s, 0)", (PIN_LOCK_NAMESPACE,))
    pinned = conn.execute(
        "SELECT count(*) FROM pergam_meta WHERE pinned AND NOT archived AND id <> %s", (pergam_id,)
    ).fetchone()[0]
    if pinned >= limit:
        raise PinRefused(over_cap_reason(pinned, limit))


def read_access(conn: psycopg.Connection, pergam_id: str) -> Access | None:
    """Owner and collaborators of an id; None when it has no v1 here."""
    row = conn.execute(
        "SELECT p.author, COALESCE(m.collaborators, '{}') FROM pergams p"
        " LEFT JOIN pergam_meta m ON m.id = p.id WHERE p.id = %s AND p.version = 1",
        (pergam_id,),
    ).fetchone()
    return Access(*row) if row else None


def check_editor(conn: psycopg.Connection, pergam_id: str, editor: str | None) -> None:
    """Raise `Forbidden` when `editor` is a trusted user who may not change an existing id; call under its lock."""
    if editor is None:
        return
    access = read_access(conn, pergam_id)
    if access is not None:
        require_editor(access, editor, pergam_id)


_EXPORTED_VERSIONS = """
    SELECT p.version, p.title, p.html, p.type, p.author, n.note, p.bytes, p.created_at
    FROM pergams p LEFT JOIN pergam_note n ON n.id = p.id AND n.version = p.version
    WHERE p.id = %s ORDER BY p.version
"""


def exported_versions(conn: psycopg.Connection, pergam_id: str) -> list[ExportedVersion]:
    return [ExportedVersion(*row) for row in conn.execute(_EXPORTED_VERSIONS, (pergam_id,)).fetchall()]


def import_conflict(doc: ImportDocument, present: dict[int, ExportedVersion]) -> str | None:
    """Why `doc` cannot be imported over `present`, or None when it can.

    The merged versions must number 1..n. Gaps are found by walking the sorted
    numbers, never by enumerating up to the largest one.
    """
    differing = [v.version for v in doc.versions if v.version in present and not present[v.version].same_content(v)]
    if differing:
        listed = ", ".join(f"v{n}" for n in differing)
        return f"{listed} of {doc.id} already exist here with different content"
    numbers = sorted(set(present) | {v.version for v in doc.versions})
    gap = next((expected for expected, number in enumerate(numbers, 1) if number != expected), None)
    if gap is not None:
        return f"importing would leave v{gap} of {doc.id} missing"
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


def page_query(source: str, condition: str, sort: str, headline: str, limit: str) -> str:
    """The rows of one index page: chosen and ordered first, then joined to their text.

    The version count, text prefix and headline are computed for the page's rows
    only, never for every pergam the sort has to look at.
    """
    order, page_order = ORDERS[sort]
    return f"""
        SELECT p.id, p.version, p.title, p.type, p.author, p.created_at, p.bytes,
               (SELECT count(*) FROM pergams v WHERE v.id = p.id),
               p.tags, p.archived, p.pinned, {headline}, left(t.body, %(source_chars)s), p.title_key, p.pinned_at
        FROM (
            SELECT l.id, l.version, l.title, l.type, l.author, l.created_at, l.bytes,
                   COALESCE(m.tags, '{{}}') AS tags, COALESCE(m.archived, false) AS archived,
                   COALESCE(m.pinned, false) AS pinned, lower(l.title) AS title_key, m.pinned_at
            {source} {condition} ORDER BY {order} LIMIT {limit}
        ) p
        LEFT JOIN pergam_text t ON t.id = p.id AND t.version = p.version
        ORDER BY {page_order}
    """


def index_row(row: tuple[Any, ...]) -> IndexRow:
    """An `IndexRow` from the index query's columns: the row, its headline, text prefix and title key."""
    *fields, headline, body, title_key, pinned_at = row
    return IndexRow(
        *fields, snippet=Snippet.of(headline), summary=summarise(body, fields[2]), title_key=title_key, pinned_at=pinned_at
    )


class PergamStore:
    def __init__(self, pool: ConnectionPool, max_pinned: int = DEFAULT_MAX_PINNED) -> None:
        self.pool = pool
        self.max_pinned = max_pinned

    def publish(self, pergam: NewPergam, existing_id: str | None, editor: str | None = None) -> Published:
        """Insert a new version and, when `pergam.tags` is given, merge them into the id's tags.

        Posts to an explicit id take a transaction-scoped advisory lock on that id
        before reading `MAX(version)`, so concurrent posts get consecutive versions
        instead of colliding on the primary key. Their `created_at` is the clock
        after the lock, never earlier than the id's latest version, so dates follow
        version order (the transaction's start, the column default, does not). New random ids retry on collision.
        A tag merge that fails (`TagError`) rolls the version back with it. The note,
        when given, is written in the same transaction as its version. With a trusted
        `editor`, a new version of an existing id needs its owner or a collaborator
        (`Forbidden` otherwise), checked under the same lock.
        """
        values = (pergam.title, pergam.html, pergam.type, pergam.author, len(pergam.html.encode("utf-8")))
        with self.pool.connection() as conn, conn.transaction():
            if existing_id:
                lock_id(conn, existing_id)
                check_editor(conn, existing_id, editor)
                row = conn.execute(
                    "INSERT INTO pergams (id, version, title, html, type, author, bytes, created_at)"
                    " SELECT %s, COALESCE(MAX(version), 0) + 1, %s, %s, %s, %s, %s,"
                    " GREATEST(clock_timestamp(), MAX(created_at)) FROM pergams WHERE id = %s"
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
                       m.origin_url, m.origin_id, m.pinned_at
                FROM (SELECT DISTINCT id FROM pergams WHERE id = %(id)s) p
                LEFT JOIN pergam_meta m ON m.id = p.id
                """,
                {"id": pergam_id},
            ).fetchone()
        if row is None:
            return None
        tags, archived, pinned, origin_url, origin_id, pinned_at = row
        origin = Origin(origin_url, origin_id) if origin_url and origin_id else None
        return Meta(tags, archived, pinned, origin, pinned_at)

    def update_meta(self, pergam_id: str, change: MetaChange, editor: str | None = None) -> Meta | None:
        """Apply `change` without touching any version; None when the id has no versions.

        Pinning a pergam that is not pinned yet stamps `pinned_at` and must fit under
        the instance's cap (`PinRefused` otherwise); pinning an archived one is
        refused too. Archiving unpins, unpinning clears `pinned_at`, and patching a
        pinned pergam again leaves its `pinned_at` alone. A row left both archived
        and pinned by an older build reads as not pinned and is normalised here.
        A trusted `editor` must be the owner or a collaborator (`Forbidden` otherwise).
        Nothing is written when anything is refused.
        """
        with self.pool.connection() as conn, conn.transaction():
            lock_id(conn, pergam_id)
            if conn.execute("SELECT 1 FROM pergams WHERE id = %s LIMIT 1", (pergam_id,)).fetchone() is None:
                return None
            check_editor(conn, pergam_id, editor)
            current = conn.execute(
                "SELECT tags, archived, pinned, pinned_at FROM pergam_meta WHERE id = %s", (pergam_id,)
            ).fetchone()
            tags, archived, pinned, pinned_at = current or ([], False, False, None)
            pinned = pinned and not archived
            tags = tags if change.tags is None else change.tags
            archived = archived if change.archived is None else change.archived
            stamp = False
            if change.pinned and not pinned:
                if archived:
                    raise PinRefused(ARCHIVED_REASON)
                claim_pin(conn, pergam_id, self.max_pinned)
                pinned, stamp = True, True
            elif change.pinned is False or archived:
                pinned = False
            row = conn.execute(
                """
                INSERT INTO pergam_meta (id, tags, archived, pinned, pinned_at)
                VALUES (%(id)s, %(tags)s, %(archived)s, %(pinned)s,
                        CASE WHEN %(stamp)s THEN clock_timestamp() WHEN %(pinned)s THEN %(pinned_at)s::timestamptz END)
                ON CONFLICT (id) DO UPDATE SET
                    tags = EXCLUDED.tags, archived = EXCLUDED.archived,
                    pinned = EXCLUDED.pinned, pinned_at = EXCLUDED.pinned_at
                RETURNING tags, archived, pinned, pinned_at
                """,
                {"id": pergam_id, "tags": tags, "archived": archived, "pinned": pinned, "stamp": stamp, "pinned_at": pinned_at},
            ).fetchone()
        tags, archived, pinned, pinned_at = row
        return Meta(tags, archived, pinned, pinned_at=pinned_at)

    def access(self, pergam_id: str) -> Access | None:
        with self.pool.connection() as conn:
            return read_access(conn, pergam_id)

    def set_collaborators(self, pergam_id: str, collaborators: list[str], editor: str) -> Access | None:
        """Replace the collaborators of an id; None when it has no versions. `editor` must be the owner (`Forbidden` otherwise)."""
        with self.pool.connection() as conn, conn.transaction():
            lock_id(conn, pergam_id)
            access = read_access(conn, pergam_id)
            if access is None:
                return None
            require_owner(access, editor, pergam_id)
            conn.execute(
                "INSERT INTO pergam_meta (id, collaborators) VALUES (%s, %s)"
                " ON CONFLICT (id) DO UPDATE SET collaborators = EXCLUDED.collaborators",
                (pergam_id, collaborators),
            )
        return Access(access.owner, collaborators)

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

    def import_pergam(self, doc: ImportDocument, editor: str | None = None) -> ImportResult:
        """Write the versions of `doc` this server lacks, keeping their numbers and `created_at`.

        One transaction under the id's advisory lock: versions already present with
        the same content are skipped, and when any present version differs, or the
        result would leave a version number missing, `ImportConflict` is raised
        before anything is written. Tags are merged as on publish; `archived`,
        `pinned` and `origin` are set only when the id is new to this server.

        A trusted `editor` follows the rules of a publish: an id already here needs
        its owner or a collaborator, and every version written must be by `editor`
        or, for an id already here, by its owner or a collaborator (`Forbidden`
        otherwise, nothing written).
        """
        with self.pool.connection() as conn, conn.transaction():
            lock_id(conn, doc.id)
            present = {v.version: v for v in exported_versions(conn, doc.id)}
            if editor is not None:
                access = read_access(conn, doc.id) if present else None
                if access is not None:
                    require_editor(access, editor, doc.id)
                written = [(v.version, v.author) for v in doc.versions if v.version not in present]
                require_import_authors(doc.id, written, editor, access)
            conflict = import_conflict(doc, present)
            if conflict:
                raise ImportConflict(conflict)
            missing = [v for v in doc.versions if v.version not in present]
            check_written_authors(missing)
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
                pinned = doc.pinned and not doc.archived and self._has_pin_room(conn, doc.id)
                conn.execute(
                    "INSERT INTO pergam_meta (id, tags, archived, pinned, pinned_at, origin_url, origin_id)"
                    " VALUES (%s, %s, %s, %s, CASE WHEN %s THEN COALESCE(%s::timestamptz, clock_timestamp()) END, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET tags = EXCLUDED.tags,"
                    " archived = EXCLUDED.archived, pinned = EXCLUDED.pinned, pinned_at = EXCLUDED.pinned_at,"
                    " origin_url = EXCLUDED.origin_url, origin_id = EXCLUDED.origin_id",
                    (doc.id, doc.tags, doc.archived, pinned, pinned, doc.pinned_at, origin and origin.url, origin and origin.id),
                )
        return ImportResult([v.version for v in missing], sorted(set(present) & {v.version for v in doc.versions}), origin)

    def _has_pin_room(self, conn: psycopg.Connection, pergam_id: str) -> bool:
        """Whether an imported pin fits under the cap; an import over it arrives unpinned rather than failing."""
        try:
            claim_pin(conn, pergam_id, self.max_pinned)
        except PinRefused:
            return False
        return True

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

    def index(self, filters: IndexFilters, page_size: int = PAGE_SIZE, cards: bool = False) -> IndexData:
        """One page of the index plus the counts around it.

        `recent` sorts pinned first, then newest first, then by id; `title` sorts by
        `lower(title)`, then id. Both orders are total, so a page resumes strictly
        after `filters.cursor`. The page is chosen first and only its rows get their
        version count, text prefix and headline.

        Counts are facets over every visible pergam, not just the page: each one
        (type, author, tag, and `archived_count` for the archived state) applies `q`
        and every other active filter but not its own, so the other values of a facet
        stay listed with what choosing them would give.

        `cards` asks for the pinned pergams the HTML lifts into cards (the first
        `page_size` that match, in the page's order): their rows on the first page,
        only their ids on later pages, where the list still has to leave them out.
        """
        scope = "" if filters.archived else " AND NOT COALESCE(m.archived, false)"
        conditions: dict[str, str] = {}
        params: dict[str, Any] = {"source_chars": SOURCE_CHARS, "limit": page_size + 1, "pinned_limit": page_size}
        if filters.type:
            conditions["type"] = "l.type = %(type)s"
            params["type"] = filters.type
        if filters.author:
            conditions["author"] = "l.author = %(author)s"
            params["author"] = filters.author
        if filters.tag:
            conditions["tag"] = "m.tags @> ARRAY[%(tag)s]::text[]"
            params["tag"] = filters.tag
        if filters.q:
            conditions["q"] = MATCHES_SQL
            params.update(search_params(filters.q))

        def narrowed(*without: str) -> str:
            return "".join(f" AND {sql}" for name, sql in conditions.items() if name not in without)

        where = f"WHERE true{scope}{narrowed()}"
        page_where = where
        if isinstance(filters.cursor, TitleCursor):
            page_where += " AND (lower(l.title), l.id) > (%(c_title)s, %(c_id)s)"
            params.update(c_title=filters.cursor.title_key, c_id=filters.cursor.id)
        elif isinstance(filters.cursor, IndexCursor):
            page_where += " AND (COALESCE(m.pinned, false), l.created_at, l.id) < (%(c_pinned)s, %(c_at)s, %(c_id)s)"
            params.update(c_pinned=filters.cursor.pinned, c_at=filters.cursor.created_at, c_id=filters.cursor.id)
        order = ORDERS[filters.sort][0]
        source = SEARCHED if filters.q else LISTED
        headline = HEADLINE_SQL if filters.q else "NULL"

        def page_of(condition: str, limit: str, sort: str = filters.sort) -> str:
            return page_query(source, condition, sort, headline, limit)

        pinned_where = f"{where} AND m.pinned AND NOT m.archived"
        with self.pool.connection() as conn:
            rows = conn.execute(page_of(page_where, "%(limit)s"), params).fetchall()
            pinned: list[IndexRow] = []
            pinned_ids: frozenset[str] = frozenset()
            if cards and filters.cursor:
                pinned_ids = frozenset(
                    pid
                    for (pid,) in conn.execute(
                        f"SELECT l.id {source} {pinned_where} ORDER BY {ORDERS['pinned'][0]} LIMIT %(pinned_limit)s",
                        params,
                    ).fetchall()
                )
            elif cards:
                pinned = [index_row(row) for row in conn.execute(page_of(pinned_where, "%(pinned_limit)s", "pinned"), params)]
                pinned_ids = frozenset(row.id for row in pinned)
            matching = conn.execute(f"SELECT count(*) {source} {where}", params).fetchone()[0]
            type_counts = conn.execute(
                f"SELECT l.type, count(*) {source} WHERE true{scope}{narrowed('type')} GROUP BY 1 ORDER BY 2 DESC, 1",
                params,
            ).fetchall()
            author_counts = conn.execute(
                f"SELECT l.author, count(*) {source} WHERE true{scope}{narrowed('author')} GROUP BY 1 ORDER BY 2 DESC, 1",
                params,
            ).fetchall()
            tag_counts = conn.execute(
                f"SELECT tag, count(*) {source} CROSS JOIN unnest(m.tags) tag WHERE true{scope}{narrowed('tag')}"
                " GROUP BY 1 ORDER BY 2 DESC, 1",
                params,
            ).fetchall()
            archived_count = conn.execute(f"SELECT count(*) {source} WHERE m.archived{narrowed()}", params).fetchone()[0]
            stats = conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM pergams_latest),
                  (SELECT count(DISTINCT author) FROM pergams_latest),
                  (SELECT count(*) FROM pergams),
                  (SELECT COALESCE(SUM(bytes), 0) FROM pergams),
                  (SELECT count(*) FROM pergams_latest WHERE created_at >= now() - interval '7 days'),
                  (SELECT count(*) FROM pergams         WHERE created_at >= now() - interval '7 days'),
                  (SELECT count(*) FROM pergams_latest l JOIN pergam_meta m ON m.id = l.id WHERE m.archived),
                  (SELECT count(*) FROM pergam_meta WHERE pinned AND NOT archived)
                """
            ).fetchone()

        page = [index_row(row) for row in rows[:page_size]]
        return IndexData(
            rows=page,
            type_counts=[(t, c) for t, c in type_counts],
            author_counts=[(a, c) for a, c in author_counts],
            tag_counts=[(t, c) for t, c in tag_counts],
            stats=IndexStats(*stats, max_pinned=self.max_pinned),
            matching=matching,
            archived_count=archived_count,
            next_cursor=page[-1].cursor(filters.sort) if len(rows) > page_size else None,
            pinned=pinned,
            pinned_ids=pinned_ids,
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
