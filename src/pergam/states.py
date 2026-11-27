"""The status filter of the index, the API and the feed: active, archived or all pergams.

`?state=` takes `active` (the default, also when blank), `archived` (only archived
pergams) or `all` (both). The older `?archived=1` meant both, so it reads as `all`.
"""

from __future__ import annotations

ACTIVE, ARCHIVED, ALL = "active", "archived", "all"
STATES = (ACTIVE, ARCHIVED, ALL)


class StateError(ValueError):
    pass


def parse_state(raw: str | None) -> str:
    state = (raw or "").strip().lower() or ACTIVE
    if state not in STATES:
        raise StateError(f"state must be one of {list(STATES)}")
    return state


def legacy_archived(raw: str | None) -> bool:
    """Whether an old `archived` parameter asked for archived pergams alongside the rest."""
    return (raw or "").strip().lower() in ("1", "true")


def scope_sql(state: str, archived: str = "COALESCE(m.archived, false)") -> str:
    """The `AND …` condition that keeps the pergams in `state`, over an `archived` column expression."""
    if state == ARCHIVED:
        return f" AND {archived}"
    if state == ACTIVE:
        return f" AND NOT {archived}"
    return ""
