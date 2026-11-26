"""Opaque keyset cursor for the index, which is ordered by `(pinned, created_at, id)` descending."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime


class CursorError(ValueError):
    pass


@dataclass(frozen=True)
class IndexCursor:
    """The sort key of the last row on a page; the next page starts strictly after it."""

    pinned: bool
    created_at: datetime
    id: str

    def encode(self) -> str:
        raw = json.dumps([int(self.pinned), self.created_at.isoformat(), self.id], separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, token: str) -> IndexCursor:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            pinned, created_at, pergam_id = json.loads(raw)
            moment = datetime.fromisoformat(created_at)
        except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise CursorError("invalid cursor") from exc
        if pinned not in (0, 1) or not isinstance(pergam_id, str) or moment.tzinfo is None:
            raise CursorError("invalid cursor")
        return cls(bool(pinned), moment, pergam_id)
