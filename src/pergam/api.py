"""JSON bodies of `GET /api/pergams` and `GET /api/pergams/{id}`.

The index body comes from the same `PergamStore.index` call as the HTML index,
so both list the same pergams, counts and cursor for the same filters.
"""

from __future__ import annotations

from typing import Any

from pergam.cursor import DEFAULT_SORT
from pergam.meta import Meta
from pergam.states import ACTIVE
from pergam.public_url import PublicAddress
from pergam.store import Facets, IndexData, IndexFilters, IndexRow, VersionInfo


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
        "pinned_at": row.pinned_at.isoformat() if row.pinned_at else None,
        "view_url": public.view_url(row.id),
        "text_url": public.url(f"{row.id}/text"),
        "snippet": row.snippet.text() if row.snippet else None,
    }
    if row.summary is not None:
        body["summary"] = row.summary
    return body


def index_json(data: IndexData, public: PublicAddress, sort: str = DEFAULT_SORT, state: str = ACTIVE) -> dict[str, Any]:
    stats = data.stats
    return {
        "pergams": [_row(row, public) for row in data.rows],
        "sort": sort,
        "state": state,
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
            "pinned": stats.total_pinned,
            "max_pinned": stats.max_pinned,
        },
    }


def facet_values(
    counts: list[tuple[str, int]], selected: tuple[str, ...], first: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """`[{value, count}]` for one facet: every known value, the selected ones at zero when no pergam has them.

    `first` (the configured types) come first in their own order; the rest follow by count.
    """
    known = dict(counts)
    extra = {value: 0 for value in (*first, *selected) if value not in known}
    ordered = [value for value in first if value in known or value in extra]
    ordered += [value for value, _ in counts if value not in ordered]
    ordered += [value for value in extra if value not in ordered]
    return [{"value": value, "count": known.get(value, 0)} for value in ordered]


def facets_json(facets: Facets, filters: IndexFilters, types: tuple[str, ...]) -> dict[str, Any]:
    return {
        "total": facets.total,
        "facets": {
            "type": facet_values(facets.types, filters.types, types),
            "author": facet_values(facets.authors, filters.authors),
            "tag": facet_values(facets.tags, filters.tags),
            "state": facets.states,
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
        "pinned_at": meta.pinned_at.isoformat() if meta.pinned_at else None,
        "origin": meta.origin.to_json() if meta.origin else None,
        "view_url": public.view_url(pergam_id),
        "text_url": public.url(f"{pergam_id}/text"),
        "versions": [_version(pergam_id, version, public) for version in versions],
    }
