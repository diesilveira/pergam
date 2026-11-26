"""Line diff of the extracted Markdown of two versions, grouped by section heading.

Both versions go through `extract_text`, so the diff compares what a reader
sees, not markup. Blank lines outside code fences are dropped, the rest is
compared line by line with `difflib`, and every heading line (outside a fence)
opens a section. Replaced lines are shown in pairs, each old line next to its
replacement and compared word by word, so the changed words inside a long
paragraph stand out.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Literal

from pergam.text import extract_text

Kind = Literal["same", "added", "removed"]

_HEADING = re.compile(r"^#{1,6} ")
_FENCE = re.compile(r"^(`{3,})")
_WORDS = re.compile(r"\s+|[^\s]+")


@dataclass(frozen=True)
class Segment:
    text: str
    changed: bool = False


@dataclass(frozen=True)
class DiffLine:
    kind: Kind
    segments: tuple[Segment, ...]

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments)


@dataclass
class Section:
    heading: str | None
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(line.kind == "added" for line in self.lines)

    @property
    def removed(self) -> int:
        return sum(line.kind == "removed" for line in self.lines)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


@dataclass(frozen=True)
class _Line:
    text: str
    heading: bool


def _lines(markdown: str) -> list[_Line]:
    out: list[_Line] = []
    fence: str | None = None
    for text in markdown.splitlines():
        opener = _FENCE.match(text)
        if fence is None and opener:
            fence = opener.group(1)
        elif fence is not None and text == fence:
            fence = None
        elif fence is None and not text.strip():
            continue
        out.append(_Line(text, fence is None and bool(_HEADING.match(text))))
    return out


def _words(old: str, new: str) -> tuple[tuple[Segment, ...], tuple[Segment, ...]]:
    a, b = _WORDS.findall(old), _WORDS.findall(new)
    removed: list[Segment] = []
    added: list[Segment] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        removed.append(Segment("".join(a[i1:i2]), tag != "equal"))
        added.append(Segment("".join(b[j1:j2]), tag != "equal"))
    return tuple(s for s in removed if s.text), tuple(s for s in added if s.text)


def _diff_lines(old: list[_Line], new: list[_Line]) -> list[tuple[_Line, DiffLine]]:
    matcher = difflib.SequenceMatcher(None, [line.text for line in old], [line.text for line in new], autojunk=False)
    out: list[tuple[_Line, DiffLine]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend((line, DiffLine("same", (Segment(line.text),))) for line in new[j1:j2])
            continue
        removed, added = old[i1:i2], new[j1:j2]
        for a, b in zip(removed, added):
            was, now = _words(a.text, b.text)
            out.extend([(a, DiffLine("removed", was)), (b, DiffLine("added", now))])
        paired = min(len(removed), len(added))
        out.extend((line, DiffLine("removed", (Segment(line.text, True),))) for line in removed[paired:])
        out.extend((line, DiffLine("added", (Segment(line.text, True),))) for line in added[paired:])
    return out


def diff_sections(old_html: str, new_html: str) -> list[Section]:
    """Sections in reading order; the first has `heading` None when text precedes every heading.

    A heading replaced on its own (a rename) keeps one section, named after the new heading.
    """
    sections: list[Section] = []
    after_removed_heading = False
    for source, line in _diff_lines(_lines(extract_text(old_html).markdown), _lines(extract_text(new_html).markdown)):
        heading = source.text.lstrip("#").strip()
        if source.heading and line.kind == "added" and after_removed_heading:
            sections[-1].heading = heading
        elif source.heading:
            sections.append(Section(heading=heading))
        elif not sections:
            sections.append(Section(heading=None))
        sections[-1].lines.append(line)
        after_removed_heading = source.heading and line.kind == "removed"
    return sections
