"""The page behind `/{id}/view` and `/{id}/v{n}/view`, from `templates/view.html` and `view.css`.

The page is pergam's own: a slim bar with the version's title, type, tags,
author, the server it was imported from (if any) and a version selector with
each version's note, and the published document in a full-height iframe loaded
from the version-pinned `/{id}/v{n}/content`. The document itself
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
from pathlib import Path
from string import Template
from urllib.parse import urlencode, urlparse

from pergam.layout import HEAD_ASSETS, top_bar
from pergam.meta import Meta, Origin
from pergam.static_files import BRIDGE_PATH, STORAGE_SHIM
from pergam.store import VersionInfo
from pergam.taxonomy import type_rgb

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


def diff_path(pergam_id: str, old: int, new: int) -> str:
    return f"/{pergam_id}/diff/v{old}..v{new}"


def _changes_link(pergam_id: str, version: int) -> str:
    if version == 1:
        return ""
    return f'<a href="{diff_path(pergam_id, version - 1, version)}" title="diff with v{version - 1}">changes</a>'


def _attributes(pairs: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f'{name}="{esc(value)}"' for name, value in pairs)


def _version_item(pergam_id: str, info: VersionInfo, current: int, latest: int) -> str:
    current_attr = ' aria-current="page"' if info.version == current else ""
    badge = ' <span class="versions__latest">latest</span>' if info.version == latest else ""
    note = f'<span class="versions__note">{esc(info.note)}</span>' if info.note else ""
    return (
        f'<li><a href="/{pergam_id}/v{info.version}/view"{current_attr}>'
        f'<span class="versions__n">v{info.version}</span>'
        f'<time datetime="{info.created_at.isoformat()}">{info.created_at.strftime("%Y-%m-%d %H:%M")}</time>'
        f"{badge}{note}</a></li>"
    )


def _tags(tags: list[str]) -> str:
    links = "".join(f'<a class="tag" href="/?{esc(urlencode({"tag": tag}))}">{esc(tag)}</a>' for tag in tags)
    return f'<span class="bar__tags">{links}</span>' if links else ""


def _origin(origin: Origin | None) -> str:
    if origin is None:
        return ""
    shown = f"{urlparse(origin.url).netloc}/{origin.id}"
    return (
        f'<span class="bar__origin">imported from '
        f'<a href="{esc(origin.view_url)}" rel="noreferrer" title="{esc(origin.view_url)}">{esc(shown)}</a></span>'
    )


def _outdated(pergam_id: str, current: int, latest: int) -> str:
    if current == latest:
        return ""
    return (
        f'<p class="outdated" role="status">You are viewing v{current}, an earlier version. '
        f'<a href="/{pergam_id}/view">Go to the current version (v{latest})</a> · '
        f'<a href="{diff_path(pergam_id, current, latest)}">what changed since</a></p>'
    )


def _bar_middle(pergam_id: str, shown: VersionInfo, meta: Meta, versions: list[VersionInfo]) -> str:
    latest = versions[0].version
    items = "".join(_version_item(pergam_id, info, shown.version, latest) for info in versions)
    return (
        f'<h1 class="bar__title">{esc(shown.title)}</h1>'
        f'<span class="badge" style="--type-rgb:{",".join(map(str, type_rgb(shown.type)))}">{esc(shown.type)}</span>'
        f"{_tags(meta.tags)}"
        f'<span class="bar__author">{esc(shown.author)}</span>'
        f"{_origin(meta.origin)}"
        f'<details class="versions"><summary class="ver">v{shown.version} of {len(versions)}</summary>'
        f'<ol class="versions__list" reversed>{items}</ol></details>'
    )


def _bar_actions(pergam_id: str, version: int) -> str:
    return (
        '<nav class="bar__links" aria-label="Pergam">'
        '<a href="/">index</a>'
        f'<a href="/{pergam_id}/v{version}/raw">raw</a>'
        f'<a href="/{pergam_id}/v{version}/text">text</a>'
        f"{_changes_link(pergam_id, version)}"
        f'<a href="{content_path(pergam_id, version)}">open document</a>'
        '<button type="button" class="btn bar__print" hidden>print</button></nav>'
    )


def render_view(
    pergam_id: str, versions: list[VersionInfo], meta: Meta, version: int, policy: FramePolicy = FRAME_POLICY
) -> str:
    """`versions` newest first, as `PergamStore.versions` returns them; `version` must be one of them."""
    shown = next(info for info in versions if info.version == version)
    frame = (("class", "doc"), ("src", content_path(pergam_id, version)), ("title", shown.title))
    return _PAGE.substitute(
        head_assets=HEAD_ASSETS,
        css=_CSS,
        page_title=esc(f"{shown.title} · v{version} · pergam"),
        title_suffix=esc(f" · v{version} · pergam"),
        top_bar=top_bar(middle=_bar_middle(pergam_id, shown, meta, versions), actions=_bar_actions(pergam_id, version)),
        outdated=_outdated(pergam_id, version, versions[0].version),
        frame_attributes=_attributes(frame + policy.iframe_attributes),
    )
