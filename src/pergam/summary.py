"""The one-line summary of a pergam in the index, taken from its already-extracted plain text.

The source is `pergam_text.body` (plain lines, no markup) of the latest version.
Leading lines that only repeat the title are skipped; the rest are joined into
one line and cut at the first sentence end at least `MIN_SENTENCE_CHARS` in (so
`Dr.` or `Version 2.` do not end it), or at a word boundary before `SUMMARY_CHARS`
with an ellipsis. No text, or text not extracted yet, is no summary.
"""

from __future__ import annotations

import re

SUMMARY_CHARS = 160
MIN_SENTENCE_CHARS = 20
SOURCE_CHARS = 1_000
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def summarise(body: str | None, title: str) -> str | None:
    if not body:
        return None
    heading = " ".join(title.split()).casefold()
    lines = [" ".join(line.split()) for line in body.splitlines()]
    lines = [line for line in lines if line]
    while lines and lines[0].casefold() == heading:
        lines.pop(0)
    text = " ".join(lines)
    if not text:
        return None
    end = _SENTENCE_END.search(text, MIN_SENTENCE_CHARS - 1)
    if end and end.end() <= SUMMARY_CHARS:
        return text[: end.end()]
    if len(text) <= SUMMARY_CHARS:
        return text
    cut = text[: SUMMARY_CHARS + 1].rsplit(" ", 1)[0] if " " in text[:SUMMARY_CHARS] else text[:SUMMARY_CHARS]
    return cut.rstrip(" ,;:·-–—") + "…"
