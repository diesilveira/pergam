"""Index URLs and the small author helpers the index and the filter dialog share.

`query_pairs` is the canonical query of a set of filters, in the order the filter
form submits it, so a link and a submitted form give the same URL.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import replace
from urllib.parse import urlencode

from pergam.cursor import DEFAULT_SORT
from pergam.states import ACTIVE
from pergam.store import IndexFilters

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


def avatar(author: str) -> str:
    """Initials on a hue (`--h`, 0-359) picked deterministically from the author string."""
    h = 0
    for ch in author:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    initials = "".join(p[0] for p in re.split(r"[.\-_+ ]+", short_author(author)) if p)[:2].upper() or "?"
    return f'<span class="avatar" aria-hidden="true" style="--h:{h % 360}">{esc(initials)}</span>'
