"""The one-line summary of a pergam in the index, taken from its already-extracted plain text.

The source is `pergam_text.body` (plain lines, no markup) of the latest version.
Leading lines that only repeat the title are skipped; the rest are joined into
one line and cut at the first sentence end, or at a word boundary before
`SUMMARY_CHARS` with an ellipsis. No text, or text not extracted yet, is no summary.
"""

from __future__ import annotations

import re

SUMMARY_CHARS = 160
SOURCE_CHARS = 1_000
_SENTENCE = re.compile(r"^(.+?[.!?])(?=\s|$)")


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
    sentence = _SENTENCE.match(text)
    if sentence and len(sentence.group(1)) <= SUMMARY_CHARS:
        return sentence.group(1)
    if len(text) <= SUMMARY_CHARS:
        return text
    cut = text[: SUMMARY_CHARS + 1].rsplit(" ", 1)[0] if " " in text[:SUMMARY_CHARS] else text[:SUMMARY_CHARS]
    return cut.rstrip(" ,;:·-–—") + "…"
