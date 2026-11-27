"""The page behind `/{id}/view` and `/{id}/v{n}/view`, from `templates/view.html` and `view.css`.

The page is pergam's own: a bar with a breadcrumb, the version's type and tags,
a version selector and the actions (compare, text, print, a menu with raw, export
and the document alone), a side panel with the history and details
(`view_panel`), and the published document in an iframe loaded from the
version-pinned `/{id}/v{n}/content`. The document itself
is stored unchanged: `/raw` returns it byte for byte, and `/content` returns it
followed by one `<script src>` tag, the frame bridge (`static/frame-bridge.js`).
The bridge and this page talk through `postMessage` only, never through the
frame's DOM or location, so the frame can be sandboxed without
`allow-same-origin`. How the framed document is isolated (iframe attributes and
the headers of its content response) lives in `FRAME_POLICY` and nowhere else.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from pergam import icons
from pergam.access import Access
from pergam.layout import HEAD_ASSETS, SearchBox, top_bar
from pergam.meta import Meta
from pergam.static_files import BRIDGE_PATH, PAGES_SCRIPT_PATH, STORAGE_SHIM, hashed_url
from pergam.store import VersionInfo
from pergam.view_panel import diff_path, published_at, render_panel

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Template((_TEMPLATES / "view.html").read_text(encoding="utf-8").rstrip("\n"))
_CSS = (_TEMPLATES / "view.css").read_text(encoding="utf-8")

esc = html_lib.escape


BRIDGE_TAG = f'<script src="{BRIDGE_PATH}" defer></script>'
PREAMBLE = f"<script>{STORAGE_SHIM}</script>{BRIDGE_TAG}"
_LEADING_DOCTYPE = re.compile(r"\A\ufeff?(?:\s|<!--.*?-->)*<!doctype\b[^>]*>", re.IGNORECASE | re.DOTALL)


def framed(html: str) -> str:
    """The body of `/content`: the posted HTML with `PREAMBLE` right after a leading doctype, else first.

    At the start, a document that ends inside `<title>`, `<textarea>` or `<plaintext>`
    cannot swallow the tags as text, and the doctype still comes first so the
    document keeps its rendering mode. The storage stand-in is inline and runs
    before any script of the document; `defer` runs the bridge once it is parsed.
    """
    match = _LEADING_DOCTYPE.match(html)
    cut = match.end() if match else 0
    return html[:cut] + PREAMBLE + html[cut:]


SANDBOX = (
    "allow-scripts allow-popups allow-popups-to-escape-sandbox allow-modals allow-top-navigation-by-user-activation"
)
MERMAID_CDN = "https://cdn.jsdelivr.net"


@dataclass(frozen=True)
class FramePolicy:
    """Isolation of the framed document: extra iframe attributes, and headers sent with `/content`."""

    iframe_attributes: tuple[tuple[str, str], ...] = ()
    content_headers: tuple[tuple[str, str], ...] = ()


def frame_policy(own_origin: str | None) -> FramePolicy:
    """The sandbox and CSP of every published document, framed or opened alone at `/content`.

    Without `allow-same-origin` the document runs in an opaque origin: it cannot
    read cookies or storage of this server, and its requests to the API are
    cross-origin. The CSP, the hosted viewer's, also forbids any connection, form
    or frame to this server, and its `sandbox` directive applies the same
    isolation when `/content` is opened directly. Scripts load only inline, from
    this server (the bridge, the vendored Mermaid in `/static/`) and from
    jsDelivr, which documents published before `/static/` served Mermaid still use. A browser
    that does not match `'self'` from an opaque origin gets the server's public
    origin by name. The sandbox tokens keep scripts, links that leave the document
    (top navigation on a click), printing (`allow-modals`) and links that open a
    tab, which open as ordinary pages outside the sandbox.
    """
    own = " ".join(source for source in ("'self'", own_origin) if source)
    csp = "; ".join(
        (
            f"sandbox {SANDBOX}",
            "default-src 'none'",
            f"script-src 'unsafe-inline' {own} {MERMAID_CDN}",
            f"style-src 'unsafe-inline' {MERMAID_CDN}",
            "img-src data:",
            f"font-src data: {MERMAID_CDN}",
            f"connect-src {MERMAID_CDN}",
            "form-action 'none'",
            f"frame-ancestors {own}",
            "base-uri 'none'",
        )
    )
    return FramePolicy(
        iframe_attributes=(("sandbox", SANDBOX),),
        content_headers=(
            ("Content-Security-Policy", csp),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
        ),
    )


FRAME_POLICY = frame_policy(None)


def content_path(pergam_id: str, version: int) -> str:
    return f"/{pergam_id}/v{version}/content"


def _attributes(pairs: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f'{name}="{esc(value)}"' for name, value in pairs)


def _version_item(pergam_id: str, info: VersionInfo, current: int, latest: int) -> str:
    current_attr = ' aria-current="page"' if info.version == current else ""
    badge = ' <span class="versions__latest">current</span>' if info.version == latest else ""
    note = f'<span class="versions__note">{esc(info.note)}</span>' if info.note else ""
    return (
        f'<li><a href="/{pergam_id}/v{info.version}/view"{current_attr}>'
        f'<span class="versions__n">v{info.version}</span>'
        f'<time datetime="{info.created_at.isoformat()}">{info.created_at.strftime("%Y-%m-%d %H:%M")}</time>'
        f"{badge}{note}</a></li>"
    )


def _tag_form(tags: list[str]) -> str:
    """The tag editor, opened from the ⋯ menu over the bar; the tags themselves show in the panel's details."""
    return (
        '<form class="tag-form" data-tags-form hidden>'
        '<label class="sr-only" for="tag-input">Tags, separated by commas</label>'
        '<input class="input" id="tag-input" name="tags" autocomplete="off" spellcheck="false" '
        f'value="{esc(", ".join(tags))}" placeholder="q3, budget">'
        '<button type="submit" class="btn btn--primary">Save</button>'
        '<button type="button" class="btn" data-tags-cancel>Cancel</button></form>'
    )


def _bar_middle(pergam_id: str, shown: VersionInfo, meta: Meta, versions: list[VersionInfo], editable: bool) -> str:
    latest = versions[0].version
    items = "".join(_version_item(pergam_id, info, shown.version, latest) for info in versions)
    return (
        '<div class="crumbs"><nav aria-label="Breadcrumb"><a href="/">Index</a></nav>'
        '<span class="crumbs__sep" aria-hidden="true">/</span>'
        f'<h1 class="bar__title">{esc(shown.title)}</h1></div>'
        f"{_tag_form(meta.tags) if editable else ''}"
        '<details class="dropdown versions"><summary class="ver dropdown__toggle">'
        f'v{shown.version}<span class="ver__of"> of {len(versions)}</span>{icons.CHEVRON_DOWN}</summary>'
        f'<ol class="dropdown__menu versions__list" reversed>{items}</ol></details>'
    )


def _pin_state(meta: Meta) -> str:
    """An archived pergam cannot be pinned, so Pin is then unavailable and says why (`pin-note`)."""
    return ' aria-disabled="true" aria-describedby="pin-note"' if meta.archived else ' aria-disabled="false"'


def _flag(flag: str, label: str, on: bool, css: str, extra: str = "") -> str:
    """A toggle for `flag`; `label` is HTML whose text names the button."""
    return (
        f'<button type="button" class="{css}" data-meta-flag="{flag}" aria-pressed="{str(on).lower()}"{extra}>'
        f"{label}</button>"
    )


def _meta_controls(pergam_id: str, meta: Meta) -> str:
    """Pin, the one metadata toggle in the bar; Archive and the tag editor are in the ⋯ menu."""
    return (
        f'<span class="meta-controls" data-meta-url="/{pergam_id}/meta" hidden>'
        f'{_flag("pinned", f"{icons.PIN}<span>Pin</span>", meta.pinned, "btn btn--pin", _pin_state(meta))}'
        '<span class="sr-only" id="pin-note">Archived pergams cannot be pinned: unarchive it first.</span></span>'
    )


def _menu(pergam_id: str, meta: Meta, version: int, editable: bool) -> str:
    """The ⋯ menu: everything the calm bar leaves out, at every width.

    Print is the frame bridge's own control (`.bar__print`), hidden until the bridge
    answers, as the inline handler in `templates/view.html` expects.
    """
    items = []
    if version > 1:
        items.append(f'<li><a href="{diff_path(pergam_id, version - 1, version)}">Compare with v{version - 1}</a></li>')
    items.append(f'<li><a href="/{pergam_id}/v{version}/text">Text</a></li>')
    items.append('<li><button type="button" class="dropdown__item bar__print" hidden>Print</button></li>')
    if editable:
        items += [
            f'<li data-meta-item hidden>{_flag("archived", "Archive", meta.archived, "dropdown__item")}</li>',
            '<li data-meta-item hidden><button type="button" class="dropdown__item" data-tags-edit>Edit tags</button></li>',
        ]
    items += [
        '<li class="dropdown__rule" role="separator"></li>',
        f'<li><a href="/{pergam_id}/v{version}/raw">Raw HTML</a></li>',
        f'<li><a href="/{pergam_id}/export" download="pergam-{pergam_id}.json">Export (JSON)</a></li>',
        f'<li><a href="{content_path(pergam_id, version)}">Open document</a></li>',
    ]
    return (
        '<details class="dropdown more"><summary class="icon-btn dropdown__toggle" aria-label="More actions" '
        f'title="Compare, text, print, archive, tags, raw HTML, export">{icons.MORE}</summary>'
        f'<ul class="dropdown__menu dropdown__menu--end">{"".join(items)}</ul></details>'
    )


def _bar_actions(pergam_id: str, meta: Meta, version: int, editable: bool) -> str:
    return (
        f"{_meta_controls(pergam_id, meta) if editable else ''}"
        '<button type="button" class="btn" data-panel-toggle aria-controls="panel" aria-expanded="false" hidden>'
        "History</button>"
        f'<nav class="bar__links" aria-label="Pergam">{_menu(pergam_id, meta, version, editable)}</nav>'
    )


def _outdated(pergam_id: str, shown: VersionInfo, latest: int, now: datetime) -> str:
    if shown.version == latest:
        return ""
    return (
        f'<p class="outdated" role="status"><span>You are viewing <strong>v{shown.version}</strong>, '
        f"published {published_at(shown.created_at, now)}.</span> "
        f'<a href="/{pergam_id}/view">Go to the current version (v{latest})</a> '
        f'<a href="{diff_path(pergam_id, shown.version, latest)}">See what changed since</a></p>'
    )


def render_view(
    pergam_id: str,
    versions: list[VersionInfo],
    meta: Meta,
    version: int,
    policy: FramePolicy = FRAME_POLICY,
    *,
    access: Access | None = None,
    editable: bool = False,
    now: datetime | None = None,
) -> str:
    """`versions` newest first, as `PergamStore.versions` returns them; `version` must be one of them.

    `editable` adds the pin, archive and tag controls, which `static/pages.js` shows
    and wires to `PATCH /{id}/meta`; the caller decides who gets them.
    """
    now = now or datetime.now(timezone.utc)
    shown = next(info for info in versions if info.version == version)
    frame = (("class", "doc"), ("src", content_path(pergam_id, version)), ("title", shown.title))
    return _PAGE.substitute(
        head_assets=HEAD_ASSETS,
        pages_script=hashed_url(PAGES_SCRIPT_PATH),
        css=_CSS,
        page_title=esc(f"{shown.title} · v{version} · pergam"),
        title_suffix=esc(f" · v{version} · pergam"),
        top_bar=top_bar(
            middle=_bar_middle(pergam_id, shown, meta, versions, editable),
            actions=_bar_actions(pergam_id, meta, version, editable),
            search=SearchBox(collapsed=True),
        ),
        meta_status='<p class="meta-status" data-meta-status role="status"></p>' if editable else "",
        outdated=_outdated(pergam_id, shown, versions[0].version, now),
        frame_attributes=_attributes(frame + policy.iframe_attributes),
        panel=render_panel(pergam_id, versions, version, meta, access, now),
    )
