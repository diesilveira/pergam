"""The optional note of a version, sent once with `POST /pergam` and never edited.

Whitespace runs collapse to one space; any other control character is rejected,
like in tags, since a note ends up in HTML and JSON and Postgres refuses NUL.
"""

from __future__ import annotations

import unicodedata

from pergam.text import collapse

MAX_NOTE_LENGTH = 280


class NoteError(ValueError):
    pass


def _is_control(char: str) -> bool:
    return unicodedata.category(char) in ("Cc", "Cs") or char in ("￾", "￿")


def parse_note(value: object) -> str | None:
    """A JSON `note` with whitespace collapsed; None when absent or blank. Raises `NoteError` on bad input."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise NoteError("note must be a string")
    note = collapse(value)
    if any(_is_control(char) for char in note):
        raise NoteError("note must not contain control characters")
    if len(note) > MAX_NOTE_LENGTH:
        raise NoteError(f"note must be at most {MAX_NOTE_LENGTH} characters")
    return note or None
