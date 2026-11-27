"""The side panel of the view page: every version with its note, author and date, then the pergam's details.

It is plain server-rendered HTML, so it works without script; `static/pages.js`
only collapses it and remembers that choice.
"""

from __future__ import annotations

import html as html_lib
from datetime import datetime
from urllib.parse import urlencode, urlparse

from pergam.access import Access
from pergam.meta import Meta, Origin
from pergam.store import VersionInfo

esc = html_lib.escape

_UNITS = (("year", 365 * 86400), ("month", 30 * 86400), ("day", 86400), ("hour", 3600), ("minute", 60))


def ago(ts: datetime, now: datetime) -> str:
    """`ts` relative to `now` in words: "just now", "1 minute ago", "6 days ago", "2 years ago"."""
    seconds = int((now - ts).total_seconds())
    for unit, size in _UNITS:
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"


def published_at(ts: datetime, now: datetime) -> str:
    """A `<time>` with the relative age as text and the exact UTC time as its tooltip."""
    exact = ts.strftime("%Y-%m-%d %H:%M UTC")
    return f'<time datetime="{ts.isoformat()}" title="{exact}">{ago(ts, now)}</time>'


def diff_path(pergam_id: str, old: int, new: int) -> str:
    return f"/{pergam_id}/diff/v{old}..v{new}"


def _version(pergam_id: str, info: VersionInfo, shown: int, latest: int, now: datetime) -> str:
    current = ' aria-current="page"' if info.version == shown else ""
    note = f'<span class="history__note">{esc(info.note)}</span>' if info.note else ""
    latest_badge = '<span class="history__latest">current</span>' if info.version == latest else ""
    compare = (
        f'<a class="history__compare" href="{diff_path(pergam_id, info.version, latest)}">'
        f"Compare v{info.version} with current<span aria-hidden=\"true\"> →</span></a>"
        if info.version != latest
        else ""
    )
    return (
        f'<li class="history__item{" history__item--shown" if info.version == shown else ""}">'
        f'<a class="history__link" href="/{pergam_id}/v{info.version}/view"{current}>'
        f'<span class="history__n">v{info.version}</span>{latest_badge}'
        f'<span class="history__when">{published_at(info.created_at, now)} · '
        f'<span class="history__author">{esc(info.author)}</span></span>{note}</a>{compare}</li>'
    )


def origin_link(origin: Origin | None) -> str:
    if origin is None:
        return "published here"
    shown = f"{urlparse(origin.url).netloc}/{origin.id}"
    return (
        f'<span class="bar__origin">imported from '
        f'<a href="{esc(origin.view_url)}" rel="noreferrer" title="{esc(origin.view_url)}">{esc(shown)}</a></span>'
    )


def _tag_links(tags: list[str]) -> str:
    links = "".join(f'<a class="tag" href="/?{esc(urlencode({"tag": tag}))}">{esc(tag)}</a>' for tag in tags)
    return f'<span class="facts__tags" data-tags>{links}</span>'


def _details(pergam_id: str, kind: str, meta: Meta, owner: str, collaborators: list[str]) -> str:
    people = ", ".join(esc(user) for user in collaborators) or '<span class="muted">none</span>'
    facts = (
        ("Type", esc(kind)),
        ("Tags", _tag_links(meta.tags)),
        ("Author", esc(owner)),
        ("Collaborators", people),
        ("Origin", origin_link(meta.origin)),
        ("Id", f"<code>{esc(pergam_id)}</code>"),
    )
    rows = "".join(f"<div><dt>{name}</dt><dd>{value}</dd></div>" for name, value in facts)
    return f'<h2 class="panel__title">Details</h2><dl class="facts">{rows}</dl>'


def render_panel(
    pergam_id: str, versions: list[VersionInfo], shown: int, meta: Meta, access: Access | None, now: datetime
) -> str:
    """`versions` newest first; without `access` the author of the first version is the author."""
    latest = versions[0].version
    kind = next(info.type for info in versions if info.version == shown)
    owner = access.owner if access else versions[-1].author
    collaborators = access.collaborators if access else []
    items = "".join(_version(pergam_id, info, shown, latest, now) for info in versions)
    return (
        '<aside class="panel" id="panel" aria-labelledby="panel-title">'
        '<div class="panel__head"><h2 class="panel__title" id="panel-title">History</h2>'
        '<button type="button" class="icon-btn" data-panel-close aria-label="Close history" hidden>'
        '<span aria-hidden="true">×</span></button></div>'
        f'<ol class="history" reversed>{items}</ol>'
        f"{_details(pergam_id, kind, meta, owner, collaborators)}</aside>"
    )
