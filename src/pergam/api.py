"""JSON bodies of `GET /api/pergams` and `GET /api/pergams/{id}`.

The index body comes from the same `PergamStore.index` call as the HTML index,
so both list the same pergams, counts and cursor for the same filters.
"""

from __future__ import annotations

from typing import Any

from pergam.cursor import DEFAULT_SORT
from pergam.meta import Meta
from pergam.public_url import PublicAddress
from pergam.store import IndexData, IndexRow, VersionInfo


def _row(row: IndexRow, public: PublicAddress) -> dict[str, Any]:
    """`summary` is left out, not null, while the version's text has not been extracted."""
    body = {
        "id": row.id,
        "version": row.version,
        "title": row.title,
        "type": row.type,
        "author": row.author,
        "created_at": row.created_at.isoformat(),
        "bytes": row.bytes,
        "versions": row.total_versions,
        "tags": row.tags,
        "archived": row.archived,
        "pinned": row.pinned,
        "view_url": public.view_url(row.id),
        "text_url": public.url(f"{row.id}/text"),
        "snippet": row.snippet.text() if row.snippet else None,
    }
    if row.summary is not None:
        body["summary"] = row.summary
    return body


def index_json(data: IndexData, public: PublicAddress, sort: str = DEFAULT_SORT) -> dict[str, Any]:
    stats = data.stats
    return {
        "pergams": [_row(row, public) for row in data.rows],
        "sort": sort,
        "matching": data.matching,
        "next_cursor": data.next_cursor.encode() if data.next_cursor else None,
        "archived_count": data.archived_count,
        "counts": {
            "types": [{"type": name, "count": count} for name, count in data.type_counts],
            "authors": [{"author": name, "count": count} for name, count in data.author_counts],
            "tags": [{"tag": name, "count": count} for name, count in data.tag_counts],
        },
        "stats": {
            "pergams": stats.total_pergams,
            "authors": stats.total_authors,
            "versions": stats.total_versions,
            "bytes": stats.total_bytes,
            "new_pergams_7d": stats.new_pergams_7d,
            "new_versions_7d": stats.new_versions_7d,
        },
    }


def _version(pergam_id: str, version: VersionInfo, public: PublicAddress) -> dict[str, Any]:
    return {
        "version": version.version,
        "title": version.title,
        "type": version.type,
        "author": version.author,
        "bytes": version.bytes,
        "created_at": version.created_at.isoformat(),
        "note": version.note,
        "view_url": public.url(f"{pergam_id}/v{version.version}/view"),
        "text_url": public.url(f"{pergam_id}/v{version.version}/text"),
    }


def pergam_json(pergam_id: str, versions: list[VersionInfo], meta: Meta, public: PublicAddress) -> dict[str, Any]:
    """`versions` newest first, as `PergamStore.versions` returns them; the top level describes the latest."""
    latest = versions[0]
    return {
        "id": pergam_id,
        "version": latest.version,
        "title": latest.title,
        "type": latest.type,
        "author": latest.author,
        "created_at": latest.created_at.isoformat(),
        "bytes": latest.bytes,
        "note": latest.note,
        "tags": meta.tags,
        "archived": meta.archived,
        "pinned": meta.pinned,
        "origin": meta.origin.to_json() if meta.origin else None,
        "view_url": public.view_url(pergam_id),
        "text_url": public.url(f"{pergam_id}/text"),
        "versions": [_version(pergam_id, version, public) for version in versions],
    }
