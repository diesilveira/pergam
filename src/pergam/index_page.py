"""The HTML index page, rendered from `templates/index.html` and `templates/index.css`.

A left column filters by type, tag, author and archived state, with counts. On the
first page, pinned pergams that match show as cards above the list and are left
out of the list on every page, so paging still visits each pergam once. The list
is grouped by the UTC date of each latest version when sorted by recent.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import urlencode

from pergam.cursor import DEFAULT_SORT
from pergam.layout import HEAD_ASSETS, SearchBox, top_bar
from pergam.static_files import INDEX_SCRIPT_PATH, hashed_url
from pergam.store import IndexData, IndexFilters, IndexRow
from pergam.taxonomy import type_rgb

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Template((_TEMPLATES / "index.html").read_text(encoding="utf-8").rstrip("\n"))
_CSS = (_TEMPLATES / "index.css").read_text(encoding="utf-8")

TAGS_SHOWN = 8
DATE_GROUPS = {"today": "Today", "week": "This week", "month": "This month", "earlier": "Earlier"}
SORT_LABELS = (("recent", "Recent", "Most recent first"), ("title", "A–Z", "By title, A to Z"))

esc = html_lib.escape


def index_link(filters: IndexFilters, path: str = "/") -> str:
    """Index URL for `filters`, cursor and sort included; callers reset the cursor with `replace(..., cursor=None)`."""
    qs = {
        key: value
        for key, value in (("type", filters.type), ("author", filters.author), ("tag", filters.tag), ("q", filters.q))
        if value
    }
    if filters.archived:
        qs["archived"] = "1"
    if filters.sort != DEFAULT_SORT:
        qs["sort"] = filters.sort
    if filters.cursor:
        qs["cursor"] = filters.cursor.encode()
    return f"{path}?{urlencode(qs)}" if qs else path


def toggle_link(filters: IndexFilters, **change: str | None) -> str:
    """Link that sets one filter, or clears it when it already has that value, back on page one."""
    ((name, value),) = change.items()
    return index_link(replace(filters, cursor=None, **{name: None if getattr(filters, name) == value else value}))


def short_author(author: str) -> str:
    return author.split("@")[0] if "@" in author else author


def human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024**3:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024**3):.2f} GB"


def human_age(ts: datetime, now: datetime) -> str:
    secs = int((now - ts).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def date_group(moment: datetime, now: datetime) -> str:
    """`today`, `week` (same ISO week), `month` (same calendar month) or `earlier`, all in UTC."""
    day, today = moment.astimezone(timezone.utc).date(), now.astimezone(timezone.utc).date()
    if day >= today:
        return "today"
    if day.isocalendar()[:2] == today.isocalendar()[:2]:
        return "week"
    if (day.year, day.month) == (today.year, today.month):
        return "month"
    return "earlier"


def avatar(author: str) -> str:
    """Initials on a hue (`--h`, 0-359) picked deterministically from the author string."""
    h = 0
    for ch in author:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    initials = "".join(p[0] for p in re.split(r"[.\-_+ ]+", short_author(author)) if p)[:2].upper() or "?"
    return f'<span class="avatar" aria-hidden="true" style="--h:{h % 360}">{esc(initials)}</span>'


def type_style(pergam_type: str) -> str:
    return f'style="--type-rgb:{",".join(map(str, type_rgb(pergam_type)))}"'


def _facet(href: str, label: str, count: int, active: bool, title: str | None = None) -> str:
    current = ' aria-current="true"' if active else ""
    hint = f' title="{esc(title)}"' if title else ""
    return (
        f'<li><a class="facet{" is-on" if active else ""}" href="{esc(href)}"{current}{hint}>'
        f'{label}<span class="facet__count">{count}</span></a></li>'
    )


def _with_active(counts: list[tuple[str, int]], active: str | None) -> list[tuple[str, int]]:
    """`counts`, plus the active value at zero when nothing matches it, so it can still be cleared."""
    if active is None or any(value == active for value, _ in counts):
        return counts
    return [*counts, (active, 0)]


def _facet_group(slug: str, title: str, items: list[str], more: list[str] | None = None) -> str:
    if not items:
        return ""
    extra = (
        f'<details class="facets__more"><summary>Show all {len(items) + len(more)}</summary>'
        f'<ul class="facets__list">{"".join(more)}</ul></details>'
        if more
        else ""
    )
    return (
        f'<section class="facets" aria-labelledby="facets-{slug}"><h2 class="facets__title" id="facets-{slug}">{title}</h2>'
        f'<ul class="facets__list">{"".join(items)}</ul>{extra}</section>'
    )


def type_facets(data: IndexData, filters: IndexFilters) -> str:
    counts = _with_active(data.type_counts, filters.type)
    every = _facet(
        index_link(replace(filters, type=None, cursor=None)),
        '<span class="type-dot" aria-hidden="true"></span>All',
        sum(count for _, count in data.type_counts),
        filters.type is None,
    )
    items = [
        _facet(
            toggle_link(filters, type=name),
            f'<span class="type-dot" aria-hidden="true" {type_style(name)}></span>{esc(name)}',
            count,
            filters.type == name,
        )
        for name, count in counts
    ]
    return _facet_group("type", "Type", [every, *items] if items else [])


def tag_facets(data: IndexData, filters: IndexFilters) -> str:
    """The most used tags first; the rest fold into "Show all", the active one always in view."""
    counts = _with_active(data.tag_counts, filters.tag)
    shown, folded = counts[:TAGS_SHOWN], counts[TAGS_SHOWN:]
    if filters.tag and all(name != filters.tag for name, _ in shown):
        active = next(item for item in folded if item[0] == filters.tag)
        shown, folded = [*shown, active], [item for item in folded if item != active]

    def item(name: str, count: int) -> str:
        label = f'<span class="facet__hash" aria-hidden="true">#</span>{esc(name)}'
        return _facet(toggle_link(filters, tag=name), label, count, filters.tag == name)

    return _facet_group("tag", "Tags", [item(*pair) for pair in shown], [item(*pair) for pair in folded])


def author_facets(data: IndexData, filters: IndexFilters) -> str:
    items = [
        _facet(
            toggle_link(filters, author=name),
            f'{avatar(name)}<span class="facet__name">{esc(short_author(name))}</span>',
            count,
            filters.author == name,
            title=name,
        )
        for name, count in _with_active(data.author_counts, filters.author)
    ]
    return _facet_group("author", "Author", items)


def state_facets(data: IndexData, filters: IndexFilters) -> str:
    if not filters.archived and data.archived_count == 0:
        return ""
    href = index_link(replace(filters, archived=not filters.archived, cursor=None))
    return _facet_group("state", "State", [_facet(href, "Archived", data.archived_count, filters.archived)])


def sidebar(data: IndexData, filters: IndexFilters) -> str:
    """A `<details>` that index.js closes below 860px; without script it stays open."""
    active = sum(bool(value) for value in (filters.type, filters.author, filters.tag, filters.archived))
    badge = f' <span class="filters__active">{active} active</span>' if active else ""
    clear = (
        f'<a class="filters__clear" href="{esc(index_link(IndexFilters(sort=filters.sort)))}">clear all</a>'
        if filters.active or filters.archived
        else ""
    )
    return (
        '<details class="filters" open>'
        f'<summary class="filters__toggle">Filters{badge}</summary>'
        f'<div class="filters__body">{clear}'
        f"{type_facets(data, filters)}{tag_facets(data, filters)}{author_facets(data, filters)}{state_facets(data, filters)}"
        "</div></details>"
    )


def _tag_links(row: IndexRow, filters: IndexFilters) -> str:
    return "".join(
        f'<a class="tag" href="{esc(index_link(replace(filters, tag=tag, cursor=None)))}" title="filter by tag">'
        f"{esc(tag)}</a>"
        for tag in row.tags
    )


def _badge(row: IndexRow, filters: IndexFilters) -> str:
    href = esc(index_link(replace(filters, type=row.type, cursor=None)))
    return f'<a class="badge" {type_style(row.type)} href="{href}" title="filter by type">{esc(row.type)}</a>'


def _line(row: IndexRow) -> str:
    """The highlighted snippet when the body matched a search, else the summary; empty when there is neither."""
    if row.snippet:
        return f'<span class="row__snippet">{row.snippet.html()}</span>'
    return f'<span class="row__summary">{esc(row.summary)}</span>' if row.summary else ""


def _version(row: IndexRow) -> str:
    plural = "s" if row.total_versions != 1 else ""
    return f'<span class="ver" title="{row.total_versions} version{plural}">v{row.version}</span>'


def _time(row: IndexRow, now: datetime) -> str:
    return (
        f'<time datetime="{row.created_at.isoformat()}" title="{row.created_at.strftime("%Y-%m-%d %H:%M UTC")}">'
        f"{human_age(row.created_at, now)}</time>"
    )


def render_row(row: IndexRow, filters: IndexFilters, now: datetime) -> str:
    """One list row. The title link covers the row; tags, type and author stay their own links above it.

    `data-href` names the pergam the row opens, for scripts and tests that look rows up by it.
    """
    flags = ('<span class="flag flag--pinned">pinned</span>' if row.pinned else "") + (
        '<span class="flag flag--archived">archived</span>' if row.archived else ""
    )
    author_href = esc(index_link(replace(filters, author=row.author, cursor=None)))
    return (
        f'<li class="row{" row--archived" if row.archived else ""}" data-href="/{row.id}/view">'
        f'<div class="row__main">'
        f'<div class="row__head">{flags}<a class="row__title" href="/{row.id}/view">{esc(row.title)}</a></div>'
        f'<div class="row__line">{_tag_links(row, filters)}{_line(row)}</div>'
        f"</div>"
        f"{_badge(row, filters)}"
        f'<a class="row__author" href="{author_href}" title="filter by author: {esc(row.author)}">'
        f'{avatar(row.author)}<span class="row__author-name">{esc(short_author(row.author))}</span></a>'
        f'<span class="row__version">{_version(row)}</span>'
        f'<span class="row__date">{_time(row, now)}</span>'
        f"</li>"
    )


def render_card(row: IndexRow, filters: IndexFilters, now: datetime) -> str:
    line = _line(row)
    summary = f'<p class="pin__summary">{line}</p>' if line else ""
    return (
        f'<li class="pin{" pin--archived" if row.archived else ""}" data-href="/{row.id}/view">'
        f'<a class="pin__title" href="/{row.id}/view">{esc(row.title)}</a>{summary}'
        f'<div class="pin__meta">{_badge(row, filters)}{_tag_links(row, filters)}'
        f'<span class="pin__when">{_version(row)} · {_time(row, now)}</span></div>'
        f"</li>"
    )


def pinned_cards(data: IndexData, filters: IndexFilters, now: datetime) -> str:
    if filters.cursor or not data.pinned:
        return ""
    cards = "".join(render_card(row, filters, now) for row in data.pinned)
    return (
        '<section class="pins" aria-labelledby="pins-title">'
        f'<h2 class="group__title" id="pins-title">Pinned</h2><ul class="pins__grid">{cards}</ul></section>'
    )


def _group(key: str, title: str, rows: list[str]) -> str:
    return f'<section class="group" data-group="{key}"><h2 class="group__title">{title}</h2><ul class="list">{"".join(rows)}</ul></section>'


def list_groups(rows: list[IndexRow], filters: IndexFilters, now: datetime) -> str:
    """Rows under date headings when sorted by recent, a new heading wherever the group changes; one list by title."""
    if filters.sort == "title":
        return _group("title", "By title", [render_row(row, filters, now) for row in rows]) if rows else ""
    groups: list[tuple[str, list[str]]] = []
    for row in rows:
        key = date_group(row.created_at, now)
        if not groups or groups[-1][0] != key:
            groups.append((key, []))
        groups[-1][1].append(render_row(row, filters, now))
    return "".join(_group(key, DATE_GROUPS[key], items) for key, items in groups)


def _named_filters(filters: IndexFilters) -> str:
    parts = [f"{name} {esc(value)}" for name, value in (("type", filters.type), ("tag", filters.tag), ("author", filters.author)) if value]
    return ", ".join(parts)


def empty_state(filters: IndexFilters) -> str:
    """What was looked for and a way out: drop the other filters, or the search, or everything."""
    if filters.q:
        narrowed = _named_filters(filters)
        scope = f" with {narrowed}" if narrowed else ""
        archived = "" if filters.archived else " (archived pergams not included)"
        actions = []
        if narrowed or filters.archived:
            wide = index_link(IndexFilters(q=filters.q, archived=True, sort=filters.sort))
            actions.append(f'<a class="btn btn--primary" href="{esc(wide)}">Search every pergam</a>')
        actions.append(f'<a class="btn" href="{esc(index_link(IndexFilters(sort=filters.sort)))}">Clear search</a>')
        return (
            f'<div class="empty"><p class="empty__title">Nothing matches “{esc(filters.q)}”{scope}{archived}.</p>'
            '<p class="empty__hint">Search looks at titles and the visible text of the latest version of each pergam.</p>'
            f'<p class="empty__actions">{"".join(actions)}</p></div>'
        )
    if filters.active:
        clear = esc(index_link(IndexFilters(sort=filters.sort)))
        return (
            f'<div class="empty"><p class="empty__title">No pergams with {_named_filters(filters)}.</p>'
            f'<p class="empty__actions"><a class="btn btn--primary" href="{clear}">Clear filters</a></p></div>'
        )
    return (
        '<div class="empty"><p class="empty__title">No pergams yet.</p>'
        '<p class="empty__hint"><a href="https://github.com/diesilveira/pergam" target="_blank" rel="noopener">'
        "POST one to <code>/pergam</code></a> to get started.</p></div>"
    )


def pager(filters: IndexFilters, data: IndexData) -> str:
    links = []
    if filters.cursor:
        links.append(f'<a class="pager__first" href="{esc(index_link(replace(filters, cursor=None)))}">← first page</a>')
    if data.next_cursor:
        links.append(
            f'<a class="pager__next" href="{esc(index_link(replace(filters, cursor=data.next_cursor)))}" data-load-more>'
            "Load more</a>"
        )
    status = '<p class="sr-only" aria-live="polite" data-load-status></p>'
    return f'<nav class="pager" aria-label="Pages">{"".join(links)}</nav>{status}' if links else ""


def heading(data: IndexData, filters: IndexFilters) -> str:
    if filters.q:
        return f"{data.matching} result{'s' if data.matching != 1 else ''}"
    if filters.active:
        return f"{data.matching} pergam{'s' if data.matching != 1 else ''}"
    return "All pergams"


def showing_note(data: IndexData, filters: IndexFilters) -> str:
    """Counts relative to what the list can show: archived pergams count only when they are listed."""
    if filters.q:
        return f"for “{esc(filters.q)}” · the filters count these results only"
    stats = data.stats
    hidden = 0 if filters.archived else stats.total_archived
    visible = stats.total_pergams - hidden
    if filters.active and data.matching != visible:
        return f"showing {data.matching} of {visible}"
    if hidden:
        return f"showing {visible} of {stats.total_pergams} · {hidden} archived hidden"
    return ""


def stats_line(data: IndexData) -> str:
    stats = data.stats
    parts = [
        f"{stats.total_pergams} pergams",
        f"{stats.total_authors} authors",
        f"{stats.total_versions} versions",
    ]
    if stats.new_pergams_7d:
        parts.append(f"{stats.new_pergams_7d} new this week")
    parts.append(human_bytes(stats.total_bytes))
    return " · ".join(parts)


def controls(filters: IndexFilters) -> str:
    def chip(sort: str, label: str, hint: str) -> str:
        href = esc(index_link(replace(filters, sort=sort, cursor=None)))
        on = filters.sort == sort
        current = ' aria-current="true"' if on else ""
        return f'<a class="chip{" is-on" if on else ""}" href="{href}"{current} title="{hint}">{label}</a>'

    chips = "".join(chip(*labels) for labels in SORT_LABELS)
    return (
        f'<nav class="chips" aria-label="Sort">{chips}</nav>'
        '<button type="button" class="chip" data-density-toggle aria-pressed="false" hidden>Compact</button>'
    )


def search_box(filters: IndexFilters) -> SearchBox:
    """The top bar's field on the index: the current query, and the filters a new search keeps."""
    keep = [(name, value) for name, value in (("type", filters.type), ("author", filters.author), ("tag", filters.tag)) if value]
    if filters.archived:
        keep.append(("archived", "1"))
    if filters.sort != DEFAULT_SORT:
        keep.append(("sort", filters.sort))
    return SearchBox(query=filters.q or "", keep=tuple(keep))


def top_actions(filters: IndexFilters) -> str:
    json_href = esc(index_link(filters, path="/api/pergams"))
    return (
        '<a class="icon-btn" href="/feed.atom" aria-label="Atom feed" title="Atom feed"><span aria-hidden="true">◉</span></a>'
        f'<a class="icon-btn" href="{json_href}" aria-label="This list as JSON" title="This list as JSON">'
        '<span aria-hidden="true">{ }</span></a>'
    )


def render_index(data: IndexData, filters: IndexFilters, host_display: str) -> str:
    now = datetime.now(timezone.utc)
    listed = [row for row in data.rows if row.id not in data.pinned_ids]
    cards = pinned_cards(data, filters, now)
    groups = list_groups(listed, filters, now)
    return _PAGE.substitute(
        head_assets=HEAD_ASSETS,
        index_script=hashed_url(INDEX_SCRIPT_PATH),
        css=_CSS,
        page_title=esc(f"“{filters.q}” · pergam") if filters.q else "pergam · index",
        top_bar=top_bar(host=host_display, actions=top_actions(filters), search=search_box(filters)),
        sidebar=sidebar(data, filters),
        heading=heading(data, filters),
        showing_note=showing_note(data, filters),
        stats_line=stats_line(data),
        controls=controls(filters),
        pinned=cards,
        groups=groups if groups or cards or filters.cursor else empty_state(filters),
        pager=pager(filters, data),
    )
