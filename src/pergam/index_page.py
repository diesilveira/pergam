"""The HTML index page, rendered from `templates/index.html` and `templates/index.css`."""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import urlencode

from pergam.store import IndexData, IndexFilters, IndexRow

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Template((_TEMPLATES / "index.html").read_text(encoding="utf-8").rstrip("\n"))
_CSS = (_TEMPLATES / "index.css").read_text(encoding="utf-8")

_AVATAR_GRADIENTS = [
    ("#f85149", "#f0d28a"),
    ("#5ee7ff", "#7aa2ff"),
    ("#f0d28a", "#f59f5a"),
    ("#56d364", "#5ee7ff"),
    ("#b692ff", "#7aa2ff"),
    ("#f59f5a", "#f85149"),
]

_EMPTY = (
    '<div class="empty">No pergams match. '
    '<a href="https://github.com/diesilveira/pergam" target="_blank" rel="noopener">'
    'POST one to <code>/pergam</code></a> to get started.</div>'
)

esc = html_lib.escape


def filter_link(
    filters: IndexFilters,
    *,
    type: str | None = None,
    author: str | None = None,
    clear_type: bool = False,
    clear_author: bool = False,
) -> str:
    """Index URL that sets or clears one filter and keeps the other active ones."""
    qs: dict[str, str] = {}
    chosen_type = None if clear_type else (type if type is not None else filters.type)
    chosen_author = None if clear_author else (author if author is not None else filters.author)
    if chosen_type:
        qs["type"] = chosen_type
    if chosen_author:
        qs["author"] = chosen_author
    if filters.q:
        qs["q"] = filters.q
    return "/?" + urlencode(qs) if qs else "/"


def short_author(author: str) -> str:
    return author.split("@")[0] if "@" in author else author


def human_bytes(n: int) -> tuple[str, str]:
    if n < 1024:
        return (f"{n}", "B")
    if n < 1024 * 1024:
        return (f"{n / 1024:.0f}", "KB")
    if n < 1024**3:
        return (f"{n / (1024 * 1024):.1f}", "MB")
    return (f"{n / (1024**3):.2f}", "GB")


def human_age(ts: datetime, now: datetime) -> str:
    secs = int((now - ts).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        hrs = secs // 3600
        return "today" if hrs < 12 else f"{hrs}h ago"
    days = secs // 86400
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def avatar(author: str) -> str:
    """Initials on a gradient picked deterministically from the author string."""
    h = 0
    for ch in author:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    a, b = _AVATAR_GRADIENTS[h % len(_AVATAR_GRADIENTS)]
    initials = "".join(p[0] for p in re.split(r"[.\-_+ ]+", short_author(author)) if p)[:2].upper() or "?"
    return (
        f'<span class="avatar" aria-hidden="true" '
        f'style="background:linear-gradient(135deg,{a},{b})">{esc(initials)}</span>'
    )


def type_pill(filters: IndexFilters, pergam_type: str, count: int) -> str:
    active = pergam_type == filters.type
    cls = ' class="active"' if active else ""
    href = filter_link(filters, clear_type=True) if active else filter_link(filters, type=pergam_type)
    return f'<a href="{esc(href)}"{cls}>{esc(pergam_type)} <span class="cnt">{count}</span></a>'


def author_pill(filters: IndexFilters, author: str, count: int) -> str:
    active = author == filters.author
    cls = ' class="active"' if active else ""
    href = filter_link(filters, clear_author=True) if active else filter_link(filters, author=author)
    return (
        f'<a href="{esc(href)}"{cls} title="{esc(author)}">'
        f'{esc(short_author(author))} <span class="cnt">{count}</span></a>'
    )


def render_row(row: IndexRow, filters: IndexFilters, now: datetime) -> str:
    """One list row; a <div> because <details> and inner <a>s cannot nest inside an <a>."""
    pid, ver = row.id, row.version
    author_href = esc(filter_link(filters, author=row.author))
    type_href = esc(filter_link(filters, type=row.type))
    versions_block = (
        f'<details class="vers"><summary>v{ver}</summary>'
        f'<div class="vers__list">'
        + " · ".join(f'<a href="/{pid}/v{i}/view">v{i}</a>' for i in range(row.total_versions, 0, -1))
        + f' · <a href="/{pid}/versions" class="muted">json</a>'
        + "</div></details>"
    )
    return (
        f'<div class="row" data-href="/{pid}/view" tabindex="0" role="link">'
        f'  <a class="row__title" href="/{pid}/view">{esc(row.title)}</a>'
        f'  <code class="row__id">{esc(pid)}</code>'
        f'  <a class="type t-{esc(row.type)}" '
        f'     href="{type_href}" title="filter by type">{esc(row.type)}</a>'
        f'  <a class="row__author" href="{author_href}" title="filter by author">'
        f'    {avatar(row.author)}<span class="row__author-name">{esc(short_author(row.author))}</span>'
        f'  </a>'
        f'  <span class="row__version">{versions_block}</span>'
        f'  <time class="row__date" datetime="{row.created_at.isoformat()}" '
        f'        title="{row.created_at.strftime("%Y-%m-%d %H:%M UTC")}">{human_age(row.created_at, now)}</time>'
        f'</div>'
    )


def delta(n: int) -> str:
    return f'<span class="stat__delta">▲{n}</span>' if n > 0 else ""


def render_index(data: IndexData, filters: IndexFilters, host_display: str) -> str:
    now = datetime.now(timezone.utc)
    stats = data.stats
    showing = len(data.rows)
    showing_note = (
        f"showing {showing} of {stats.total_pergams}"
        if filters.active and showing != stats.total_pergams
        else f"{stats.total_pergams} pergams · {stats.total_authors} authors · {stats.total_versions} versions"
    )
    bytes_value, bytes_unit = human_bytes(stats.total_bytes)
    return _PAGE.substitute(
        css=_CSS,
        host_display=esc(host_display),
        showing_note=showing_note,
        total_pergams=stats.total_pergams,
        new_pergams_delta=delta(stats.new_pergams_7d),
        total_authors=stats.total_authors,
        total_versions=stats.total_versions,
        new_versions_delta=delta(stats.new_versions_7d),
        bytes_value=bytes_value,
        bytes_unit=bytes_unit,
        type_pills=" ".join(type_pill(filters, t, c) for t, c in data.type_counts),
        author_pills=" ".join(author_pill(filters, a, c) for a, c in data.author_counts),
        q_value=esc(filters.q or ""),
        type_value=esc(filters.type or ""),
        author_value=esc(filters.author or ""),
        clear_link='<a class="clear" href="/">clear all</a>' if filters.active else "",
        rows="\n".join(render_row(row, filters, now) for row in data.rows) or _EMPTY,
    )
