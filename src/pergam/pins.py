"""The cap on pinned pergams, from `PERGAM_MAX_PINNED`, and why a pin is refused.

Pinned pergams are the cards above the index list, so the instance keeps few of
them: at most `max_pinned` (default 12, 0 turns pinning off). Only pergams that
are pinned and not archived count. An instance already over the cap keeps its
pins; it cannot pin more until it is back under.
"""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_MAX_PINNED = 12


class PinRefused(Exception):
    """A pin the server will not take (409); the message is fixed text, never user data."""


def max_pinned(env: Mapping[str, str]) -> int:
    """The cap, written as plain ASCII digits (no sign, no other digit scripts); blank means the default."""
    raw = env.get("PERGAM_MAX_PINNED", "").strip()
    if not raw:
        return DEFAULT_MAX_PINNED
    if not (raw.isascii() and raw.isdigit()):
        raise ValueError(f"PERGAM_MAX_PINNED must be a whole number, 0 or more, in plain digits, got {raw!r}")
    return int(raw)


def over_cap_reason(pinned: int, limit: int) -> str:
    if limit == 0:
        return "Pinning is turned off on this server (PERGAM_MAX_PINNED=0)"
    return f"{pinned} pergams are already pinned, the most this server allows ({limit}); unpin one first"


ARCHIVED_REASON = "An archived pergam cannot be pinned; unarchive it first"
