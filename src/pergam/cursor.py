"""Opaque keyset cursors for the index, one per sort order.

`recent` orders by `(pinned, created_at, id)` descending and pages with an
`IndexCursor`; `title` orders by `(lower(title), id)` ascending and pages with a
`TitleCursor`. A cursor only decodes for the order that issued it, so following a
link after switching the order is a 400 rather than a silently wrong page.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SORTS = ("recent", "title")
DEFAULT_SORT = "recent"
_TITLE_MARK = "title"


class CursorError(ValueError):
    pass


class SortError(ValueError):
    pass


def parse_sort(raw: str | None) -> str:
    """`sort` from a query string; blank means `recent`, anything not in `SORTS` raises `SortError`."""
    sort = (raw or "").strip().lower() or DEFAULT_SORT
    if sort not in SORTS:
        raise SortError(f"sort must be one of {list(SORTS)}")
    return sort


def _pack(values: list[Any]) -> str:
    raw = json.dumps(values, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _unpack(token: str) -> Any:
    try:
        return json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CursorError("invalid cursor") from exc


@dataclass(frozen=True)
class IndexCursor:
    """The `recent` sort key of the last row on a page; the next page starts strictly after it."""

    pinned: bool
    created_at: datetime
    id: str

    def encode(self) -> str:
        return _pack([int(self.pinned), self.created_at.isoformat(), self.id])

    @classmethod
    def decode(cls, token: str) -> IndexCursor:
        try:
            pinned, created_at, pergam_id = _unpack(token)
            moment = datetime.fromisoformat(created_at)
        except (ValueError, TypeError) as exc:
            raise CursorError("invalid cursor") from exc
        if pinned not in (0, 1) or not isinstance(pergam_id, str) or moment.tzinfo is None:
            raise CursorError("invalid cursor")
        return cls(bool(pinned), moment, pergam_id)


@dataclass(frozen=True)
class TitleCursor:
    """The `title` sort key (`lower(title)` as Postgres computes it, then id) of the last row on a page."""

    title_key: str
    id: str

    def encode(self) -> str:
        return _pack([_TITLE_MARK, self.title_key, self.id])

    @classmethod
    def decode(cls, token: str) -> TitleCursor:
        try:
            mark, title_key, pergam_id = _unpack(token)
        except (ValueError, TypeError) as exc:
            raise CursorError("invalid cursor") from exc
        if mark != _TITLE_MARK or not isinstance(title_key, str) or not isinstance(pergam_id, str):
            raise CursorError("invalid cursor")
        return cls(title_key, pergam_id)


Cursor = IndexCursor | TitleCursor


def decode_cursor(token: str, sort: str) -> Cursor:
    return TitleCursor.decode(token) if sort == "title" else IndexCursor.decode(token)
