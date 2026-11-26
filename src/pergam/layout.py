"""What every server-rendered page shares: the head assets and the top bar.

A page puts `HEAD_ASSETS` in its `<head>` and `top_bar(...)` first in its `<body>`;
the stylesheet, the theme script and the theme toggle then come with them. Page templates fill `${head_assets}` and `${top_bar}`
and keep only their own layout CSS.
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass

from pergam.search import MAX_QUERY_CHARS
from pergam.static_files import STYLESHEET_PATH, THEME_SCRIPT_PATH, hashed_url

HEAD_ASSETS = (
    '<meta name="color-scheme" content="light dark">'
    f'<script src="{hashed_url(THEME_SCRIPT_PATH)}"></script>'
    f'<link rel="stylesheet" href="{hashed_url(STYLESHEET_PATH)}">'
)

_THEME_TOGGLE = (
    '<button type="button" class="icon-btn" data-theme-toggle hidden aria-label="Switch theme">'
    '<span aria-hidden="true">◐</span></button>'
)


@dataclass(frozen=True)
class SearchBox:
    """What the top bar's search field shows: the current `query` and the index filters a new search keeps."""

    query: str = ""
    keep: tuple[tuple[str, str], ...] = ()

    def html(self) -> str:
        esc = html_lib.escape
        kept = "".join(f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">' for name, value in self.keep)
        return (
            '<form class="topbar__search" action="/" method="get" role="search">'
            '<span class="topbar__search-icon" aria-hidden="true">⌕</span>'
            f'<input class="topbar__search-input" type="search" name="q" value="{esc(self.query)}"'
            ' placeholder="Search title and text" aria-label="Search title and text"'
            f' maxlength="{MAX_QUERY_CHARS}" autocomplete="off" data-search>'
            f"{kept}</form>"
        )


def top_bar(*, host: str | None = None, middle: str = "", actions: str = "", search: SearchBox | None = None) -> str:
    """The `<header class="topbar">`: logo, the server's `host` (plain text, escaped here), `middle`, the search field.

    `middle` and `actions` are HTML the caller has already escaped. `middle` sits after
    the host and grows with the bar; the search field, when `search` is given, submits
    to the index; `actions` and the theme toggle are pushed to the right edge, the toggle last.
    """
    host_html = f'<span class="topbar__host">{html_lib.escape(host)}</span>' if host else ""
    return (
        '<header class="topbar">'
        '<a class="logo" href="/" title="pergam index"><span class="logo__mark" aria-hidden="true">p</span>'
        '<span class="logo__name">pergam</span></a>'
        f"{host_html}{middle}{search.html() if search else ''}"
        f'<div class="topbar__actions">{actions}{_THEME_TOGGLE}</div>'
        "</header>"
    )
