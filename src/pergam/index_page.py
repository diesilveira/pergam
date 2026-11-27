"""The HTML index page, rendered from `templates/index.html` and `templates/index.css`.

One centred column. On the first page, pinned pergams that match show as a row of
cards above the list and are left out of the list on every page, so paging still
visits each pergam once. The list is grouped by the UTC date of each latest
version when sorted by recent. A row is one line (title, type, author, date);
"Show summaries" in the sort menu adds the summary, tags and version, a choice
index.js remembers. The feed and JSON links sit in the footer.
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
from pergam.states import ACTIVE, ALL, ARCHIVED
from pergam.static_files import INDEX_SCRIPT_PATH, hashed_url
from pergam.store import IndexData, IndexFilters, IndexRow
from pergam.taxonomy import type_rgb

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Template((_TEMPLATES / "index.html").read_text(encoding="utf-8").rstrip("\n"))
_CSS = (_TEMPLATES / "index.css").read_text(encoding="utf-8")

DATE_GROUPS = {"today": "Today", "week": "This week", "month": "This month", "earlier": "Earlier"}
SORT_LABELS = {"recent": "Recent", "title": "Title A–Z"}

esc = html_lib.escape


FACETS = (("types", "type"), ("authors", "author"), ("tags", "tag"))


def query_pairs(filters: IndexFilters) -> list[tuple[str, str]]:
    """The query of `filters` in canonical order, as the filter form submits it: types, authors, tags, state, q, sort, cursor."""
    pairs = [(key, value) for field_name, key in FACETS for value in getattr(filters, field_name)]
    if filters.state != ACTIVE:
        pairs.append(("state", filters.state))
    if filters.q:
        pairs.append(("q", filters.q))
    if filters.sort != DEFAULT_SORT:
        pairs.append(("sort", filters.sort))
    if filters.cursor:
        pairs.append(("cursor", filters.cursor.encode()))
    return pairs


def index_link(filters: IndexFilters, path: str = "/") -> str:
    """Index URL for `filters`, cursor and sort included; callers reset the cursor with `replace(..., cursor=None)`."""
    pairs = query_pairs(filters)
    return f"{path}?{urlencode(pairs)}" if pairs else path


def with_value(filters: IndexFilters, field_name: str, value: str) -> IndexFilters:
    """`filters` with `value` added to one facet (kept once), back on page one."""
    values = getattr(filters, field_name)
    return replace(filters, cursor=None, **{field_name: values if value in values else (*values, value)})


def without_value(filters: IndexFilters, field_name: str, value: str) -> IndexFilters:
    return replace(filters, cursor=None, **{field_name: tuple(v for v in getattr(filters, field_name) if v != value)})


def short_author(author: str) -> str:
    return author.split("@")[0] if "@" in author else author


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


def _tag_links(row: IndexRow, filters: IndexFilters) -> str:
    return "".join(
        f'<a class="tag" href="{esc(index_link(with_value(filters, "tags", tag)))}" title="filter by tag">'
        f"{esc(tag)}</a>"
        for tag in row.tags
    )


def _badge(row: IndexRow, filters: IndexFilters) -> str:
    href = esc(index_link(with_value(filters, "types", row.type)))
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
    """One list row: title, type, author and date on one line; summaries add a second line, the version and the name.

    The title link covers the row; type, author and tags stay their own filter links
    above it. A search snippet always shows, since it says why the row matched.
    `data-href` names the pergam the row opens, for scripts and tests that look rows up by it.
    """
    flags = ('<span class="flag flag--pinned">pinned</span>' if row.pinned else "") + (
        '<span class="flag flag--archived">Archived</span>' if row.archived and filters.state == ALL else ""
    )
    author_href = esc(index_link(with_value(filters, "authors", row.author)))
    classes = "row" + (" row--archived" if row.archived else "") + (" row--snippet" if row.snippet else "")
    return (
        f'<li class="{classes}" data-href="/{row.id}/view">'
        f'<div class="row__main">'
        f'<div class="row__head"><a class="row__title" href="/{row.id}/view">{esc(row.title)}</a>{flags}</div>'
        f'<div class="row__line">{_tag_links(row, filters)}{_line(row)}</div>'
        f"</div>"
        f'<span class="row__version">{_version(row)}</span>'
        f"{_badge(row, filters)}"
        f'<a class="row__author" href="{author_href}" aria-label="Filter by author: {esc(row.author)}" title="{esc(row.author)}">'
        f'{avatar(row.author)}<span class="row__author-name" aria-hidden="true">{esc(short_author(row.author))}</span></a>'
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
    """One row of cards, most recently pinned first, that scrolls sideways.

    It is a plain labelled list: native scrolling with snap points works by touch,
    trackpad and Tab; index.js adds previous/next buttons (hidden until then).
    """
    if filters.cursor or not data.pinned:
        return ""
    cards = "".join(render_card(row, filters, now) for row in data.pinned)
    stats = data.stats
    return (
        '<section class="pins">'
        '<div class="pins__head">'
        f'<h2 class="group__title" id="pins-title">Pinned</h2>'
        f'<span class="pins__count">{stats.total_pinned} of {stats.max_pinned} pinned</span>'
        '<button type="button" class="icon-btn pins__nav" data-pins-prev aria-label="Previous pinned pergams" hidden>'
        '<span aria-hidden="true">‹</span></button>'
        '<button type="button" class="icon-btn pins__nav" data-pins-next aria-label="Next pinned pergams" hidden>'
        '<span aria-hidden="true">›</span></button>'
        "</div>"
        f'<ul class="pins__row" aria-labelledby="pins-title" data-pins>{cards}</ul></section>'
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
    parts = [
        f"{key} {' or '.join(esc(value) for value in getattr(filters, field_name))}"
        for field_name, key in FACETS
        if getattr(filters, field_name)
    ]
    return ", ".join(parts)


def empty_state(filters: IndexFilters) -> str:
    """What was looked for and a way out: drop the other filters, or the search, or everything."""
    if filters.q:
        narrowed = _named_filters(filters)
        scope = f" with {narrowed}" if narrowed else ""
        archived = " (archived pergams not included)" if filters.state == ACTIVE else ""
        actions = []
        if narrowed or filters.state != ALL:
            wide = index_link(IndexFilters(q=filters.q, state=ALL, sort=filters.sort))
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
    if filters.state == ARCHIVED and not (filters.q or filters.active):
        return "Archived pergams"
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
    if filters.state != ACTIVE:
        return ""
    hidden = stats.total_archived
    visible = stats.total_pergams - hidden
    if filters.active and data.matching != visible:
        return f"showing {data.matching} of {visible}"
    if hidden:
        return f"showing {visible} of {stats.total_pergams} · {hidden} archived hidden"
    return ""


def sort_menu(filters: IndexFilters) -> str:
    """The sort menu: a `<details>` of links, so it works without script, plus the "Show summaries" switch index.js reveals."""
    def option(sort: str, label: str) -> str:
        href = esc(index_link(replace(filters, sort=sort, cursor=None)))
        current = ' aria-current="true"' if filters.sort == sort else ""
        return f'<li><a href="{href}"{current}>{label}</a></li>'

    label = SORT_LABELS[filters.sort]
    return (
        '<details class="dropdown sortmenu" data-sortmenu>'
        f'<summary class="btn sortmenu__toggle" aria-label="Sort: {label}">{label}<span aria-hidden="true"> ▾</span></summary>'
        '<ul class="dropdown__menu dropdown__menu--end sortmenu__menu">'
        f'{"".join(option(sort, text) for sort, text in SORT_LABELS.items())}'
        '<li class="sortmenu__switch" data-summaries-item hidden>'
        '<button type="button" role="switch" aria-checked="false" data-summaries-toggle>'
        '<span class="switch" aria-hidden="true"></span>Show summaries</button></li>'
        "</ul></details>"
    )


def search_box(filters: IndexFilters) -> SearchBox:
    """The top bar's field on the index: the current query, and the filters a new search keeps."""
    keep = [(key, value) for key, value in query_pairs(replace(filters, q=None, cursor=None))]
    return SearchBox(query=filters.q or "", keep=tuple(keep), compact=False)


def clear_link(filters: IndexFilters) -> str:
    """"Clear all" drops every filter, the status included, and keeps the search and the sort."""
    if not (filters.active or filters.state != ACTIVE):
        return ""
    href = esc(index_link(IndexFilters(q=filters.q, sort=filters.sort)))
    return f'<a class="toolbar__clear" href="{href}">Clear all</a>'


def footer(filters: IndexFilters) -> str:
    json_href = esc(index_link(filters, path="/api/pergams"))
    return (
        '<footer class="index-foot"><nav aria-label="Feeds and API">'
        '<a href="/feed.atom">Atom feed</a>'
        f'<a href="{json_href}">This list as JSON</a>'
        '<a href="https://github.com/diesilveira/pergam" rel="noopener">About pergam</a>'
        "</nav></footer>"
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
        top_bar=top_bar(host=host_display, search=search_box(filters)),
        heading=heading(data, filters),
        showing_note=showing_note(data, filters),
        sort_menu=sort_menu(filters),
        clear_link=clear_link(filters),
        footer=footer(filters),
        pinned=cards,
        groups=groups if groups or cards or filters.cursor else empty_state(filters),
        pager=pager(filters, data),
    )
