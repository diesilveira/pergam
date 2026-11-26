"""Per-id metadata (`tags`, `archived`, `pinned`) and the validation of a `PATCH /{id}/meta` body."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pergam.tags import TagError, parse_tags

FIELDS = ("tags", "archived", "pinned")


class MetaError(ValueError):
    pass


@dataclass(frozen=True)
class Meta:
    tags: list[str]
    archived: bool
    pinned: bool

    def to_json(self, pergam_id: str) -> dict[str, Any]:
        return {"id": pergam_id, "tags": self.tags, "archived": self.archived, "pinned": self.pinned}


@dataclass(frozen=True)
class MetaChange:
    """Fields left as None keep their current value; `tags` replaces the whole set."""

    tags: list[str] | None = None
    archived: bool | None = None
    pinned: bool | None = None


def parse_meta_change(data: dict[str, Any]) -> MetaChange:
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise MetaError(f"unknown field {unknown[0]!r}; allowed: {', '.join(FIELDS)}")
    if not data:
        raise MetaError(f"send at least one of {', '.join(FIELDS)}")
    for flag in ("archived", "pinned"):
        if flag in data and not isinstance(data[flag], bool):
            raise MetaError(f"{flag} must be true or false")
    try:
        tags = parse_tags(data["tags"]) if "tags" in data else None
    except TagError as exc:
        raise MetaError(str(exc)) from None
    return MetaChange(tags=tags, archived=data.get("archived"), pinned=data.get("pinned"))
