"""The page behind `/{id}/view` and `/{id}/v{n}/view`, from `templates/view.html` and `view.css`.

The page is pergam's own: a slim bar with the version's title, type, tags,
author and a version selector, and the published document in a full-height
iframe loaded from the version-pinned `/{id}/v{n}/content`. The document itself
is never altered, so `/raw` and `/content` stay byte-identical to what was posted.
How the framed document is isolated (iframe attributes and the headers of its
content response) lives in `FRAME_POLICY` and nowhere else.
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass
from pathlib import Path
from string import Template
from urllib.parse import urlencode

from pergam.meta import Meta
from pergam.store import VersionInfo
from pergam.taxonomy import type_rgb

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Template((_TEMPLATES / "view.html").read_text(encoding="utf-8").rstrip("\n"))
_CSS = (_TEMPLATES / "view.css").read_text(encoding="utf-8")

esc = html_lib.escape


@dataclass(frozen=True)
class FramePolicy:
    """Isolation of the framed document: extra iframe attributes, and headers sent with `/content`."""

    iframe_attributes: tuple[tuple[str, str], ...] = ()
    content_headers: tuple[tuple[str, str], ...] = ()


FRAME_POLICY = FramePolicy()


def content_path(pergam_id: str, version: int) -> str:
    return f"/{pergam_id}/v{version}/content"


def _attributes(pairs: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f'{name}="{esc(value)}"' for name, value in pairs)


def _version_item(pergam_id: str, info: VersionInfo, current: int, latest: int) -> str:
    current_attr = ' aria-current="page"' if info.version == current else ""
    badge = ' <span class="versions__latest">latest</span>' if info.version == latest else ""
    return (
        f'<li><a href="/{pergam_id}/v{info.version}/view"{current_attr}>'
        f'<span class="versions__n">v{info.version}</span>'
        f'<time datetime="{info.created_at.isoformat()}">{info.created_at.strftime("%Y-%m-%d %H:%M")}</time>'
        f"{badge}</a></li>"
    )


def _tags(tags: list[str]) -> str:
    links = "".join(f'<a class="tag" href="/?{esc(urlencode({"tag": tag}))}">{esc(tag)}</a>' for tag in tags)
    return f'<span class="bar__tags">{links}</span>' if links else ""


def _outdated(pergam_id: str, current: int, latest: int) -> str:
    if current == latest:
        return ""
    return (
        f'<p class="outdated" role="status">You are viewing v{current}, an earlier version. '
        f'<a href="/{pergam_id}/view">Go to the current version (v{latest})</a></p>'
    )


def render_view(
    pergam_id: str, versions: list[VersionInfo], meta: Meta, version: int, policy: FramePolicy = FRAME_POLICY
) -> str:
    """`versions` newest first, as `PergamStore.versions` returns them; `version` must be one of them."""
    shown = next(info for info in versions if info.version == version)
    latest = versions[0].version
    frame = (("class", "doc"), ("src", content_path(pergam_id, version)), ("title", shown.title))
    return _PAGE.substitute(
        css=_CSS,
        page_title=esc(f"{shown.title} · v{version} · pergam"),
        title=esc(shown.title),
        type=esc(shown.type),
        type_rgb=",".join(map(str, type_rgb(shown.type))),
        tags=_tags(meta.tags),
        author=esc(shown.author),
        version=version,
        total=len(versions),
        version_items="".join(_version_item(pergam_id, info, version, latest) for info in versions),
        raw_href=f"/{pergam_id}/v{version}/raw",
        text_href=f"/{pergam_id}/v{version}/text",
        outdated=_outdated(pergam_id, version, latest),
        frame_attributes=_attributes(frame + policy.iframe_attributes),
    )
