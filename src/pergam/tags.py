"""Free-form tags, normalised to lowercase words joined by hyphens.

`" Q3  Review_notes "` becomes `"q3-review-notes"`. Tags are stored sorted and
deduplicated, so the same set always reads back the same way.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_TAG_LENGTH = 40
MAX_TAGS = 20

_SEPARATORS = re.compile(r"[\s_\-]+")


class TagError(ValueError):
    pass


def normalise_tag(raw: str) -> str:
    return _SEPARATORS.sub("-", raw.strip().lower()).strip("-")


def parse_tags(value: object) -> list[str]:
    """Validate a JSON `tags` value and return it normalised; raises `TagError` on bad input."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TagError("tags must be an array of strings")
    tags = merge_tags([], (normalise_tag(item) for item in value))
    too_long = [tag for tag in tags if len(tag) > MAX_TAG_LENGTH]
    if too_long:
        raise TagError(f"tags must be at most {MAX_TAG_LENGTH} characters: {too_long[0]!r}")
    return tags


def merge_tags(existing: Iterable[str], new: Iterable[str]) -> list[str]:
    """Union of both sets, empties dropped; raises `TagError` above `MAX_TAGS`."""
    merged = sorted({tag for tag in (*existing, *new) if tag})
    if len(merged) > MAX_TAGS:
        raise TagError(f"a pergam can have at most {MAX_TAGS} tags")
    return merged
