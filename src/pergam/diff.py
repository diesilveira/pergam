"""Line diff of the extracted Markdown of two versions, grouped by section heading.

Both versions go through `extract_text`, so the diff compares what a reader
sees, not markup. Blank lines outside code fences are dropped, the rest is
compared line by line with `difflib`, and every heading line (outside a fence)
opens a section. Replaced lines are shown in pairs, each old line next to its
replacement and compared word by word, so the changed words inside a long
paragraph stand out.

The route is public, and `difflib` can take time quadratic or worse in the
input, so the work is bounded in units of `find_longest_match` scanning. Common
leading and trailing elements are matched for free. In what remains, elements
that occur exactly once on each side are anchors, kept in order on both sides
(the longest increasing run, as in patience diff, O(n log n) and unmetered), so
scattered edits leave many small gaps instead of one long, lopsided recursion.
Only the gaps go through `difflib`, metered against one budget; in a gap, an
element seen more than `POPULAR` times on the new side is junk (it extends
matches but never anchors one, like `autojunk` but independent of length). A
replaced pair whose words overrun `PAIR_BUDGET`, or every pair once a request
has spent `REQUEST_BUDGET` on words, is marked as whole lines instead.
Documents over `MAX_HTML_BYTES` or `MAX_LINES`, or whose lines overrun
`LINE_BUDGET`, raise `TooLargeToDiff`.
"""

from __future__ import annotations

import bisect
import difflib
import re
from collections import Counter
from collections.abc import Hashable, Sequence
from itertools import accumulate
from dataclasses import dataclass, field
from typing import Literal

from pergam.text import extract_text

Kind = Literal["same", "added", "removed"]
Opcode = tuple[str, int, int, int, int]

MAX_HTML_BYTES = 2_000_000
MAX_LINES = 20_000
POPULAR = 8
LINE_BUDGET = 2_000_000
PAIR_BUDGET = 200_000
REQUEST_BUDGET = 1_000_000

_HEADING = re.compile(r"^#{1,6} ")
_FENCE = re.compile(r"^(`{3,})")
_WORDS = re.compile(r"^\s+|\S+\s*")


class TooLargeToDiff(Exception):
    """The versions are too large, or too repetitive, to compare within the budget."""


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


@dataclass
class Budget:
    """Units of matching work; `spend` refuses, and spends nothing, once a cost would overrun it."""

    remaining: int
    spent: int = 0

    def spend(self, cost: int) -> bool:
        if cost > self.remaining:
            return False
        self.remaining -= cost
        self.spent += cost
        return True


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


def popular(b: Sequence[Hashable]) -> frozenset[Hashable]:
    return frozenset(item for item, seen in Counter(b).items() if seen > POPULAR)


class _OverBudget(Exception):
    pass


class _BoundedMatcher(difflib.SequenceMatcher):
    """A `SequenceMatcher` that charges every `find_longest_match` call to a budget.

    A call scans `a[alo:ahi]` and, for each element, its positions in `b`, so it
    costs `ahi - alo` plus those positions, read from prefix sums in O(1).
    """

    def __init__(self, a: Sequence[Hashable], b: Sequence[Hashable], budget: Budget) -> None:
        junk = popular(b)
        super().__init__(junk.__contains__, a, b, autojunk=False)
        counts = Counter(item for item in b if item not in junk)
        self.budget = budget
        self.positions = list(accumulate((counts[item] for item in a), initial=0))

    def find_longest_match(
        self, alo: int = 0, ahi: int | None = None, blo: int = 0, bhi: int | None = None
    ) -> difflib.Match:
        ahi = len(self.a) if ahi is None else ahi
        if not self.budget.spend(ahi - alo + self.positions[ahi] - self.positions[alo]):
            raise _OverBudget
        return super().find_longest_match(alo, ahi, blo, bhi)


def anchors(a: Sequence[Hashable], b: Sequence[Hashable]) -> list[tuple[int, int]]:
    """Positions `(i, j)` of elements unique on both sides, the longest run increasing on both."""
    in_a, in_b = Counter(a), Counter(b)
    where_b = {item: j for j, item in enumerate(b) if in_b[item] == 1}
    pairs = [(i, where_b[item]) for i, item in enumerate(a) if in_a[item] == 1 and item in where_b]
    tails: list[int] = []
    tail_pair: list[int] = []
    previous = [-1] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        slot = bisect.bisect_left(tails, j)
        if slot == len(tails):
            tails.append(j)
            tail_pair.append(k)
        else:
            tails[slot] = j
            tail_pair[slot] = k
        previous[k] = tail_pair[slot - 1] if slot else -1
    run: list[tuple[int, int]] = []
    k = tail_pair[-1] if tail_pair else -1
    while k >= 0:
        run.append(pairs[k])
        k = previous[k]
    return run[::-1]


def _gap_opcodes(a: Sequence[Hashable], b: Sequence[Hashable], budget: Budget) -> list[Opcode]:
    """Opcodes of `a` against `b` between anchors; raises `_OverBudget`."""
    found: list[Opcode] = []
    i = j = 0
    for anchor_i, anchor_j in [*anchors(a, b), (len(a), len(b))]:
        if anchor_i > i or anchor_j > j:
            gap = _BoundedMatcher(a[i:anchor_i], b[j:anchor_j], budget).get_opcodes()
            found.extend((tag, i1 + i, i2 + i, j1 + j, j2 + j) for tag, i1, i2, j1, j2 in gap)
        if anchor_i < len(a):
            found.append(("equal", anchor_i, anchor_i + 1, anchor_j, anchor_j + 1))
        i, j = anchor_i + 1, anchor_j + 1
    return found


def _merged(opcodes: list[Opcode]) -> list[Opcode]:
    out: list[Opcode] = []
    for opcode in opcodes:
        if out and opcode[0] == "equal" and out[-1][0] == "equal":
            out[-1] = ("equal", out[-1][1], opcode[2], out[-1][3], opcode[4])
        else:
            out.append(opcode)
    return out


def bounded_opcodes(a: Sequence[Hashable], b: Sequence[Hashable], budget: Budget) -> list[Opcode] | None:
    """Opcodes of `a` against `b`, or None when matching the gaps between anchors overruns `budget`."""
    end = min(len(a), len(b))
    head = 0
    while head < end and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < end - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    try:
        middle = _gap_opcodes(a[head : len(a) - tail], b[head : len(b) - tail], budget)
    except _OverBudget:
        return None
    opcodes: list[Opcode] = [("equal", 0, head, 0, head)] if head else []
    opcodes.extend((tag, i1 + head, i2 + head, j1 + head, j2 + head) for tag, i1, i2, j1, j2 in middle)
    if tail:
        opcodes.append(("equal", len(a) - tail, len(a), len(b) - tail, len(b)))
    return _merged(opcodes)


def _marked(tokens: list[str], changed: bool) -> list[Segment]:
    """A run of words; a changed run leaves its trailing whitespace unmarked."""
    text = "".join(tokens)
    if not text or not changed:
        return [Segment(text)] if text else []
    stripped = text.rstrip()
    return [Segment(stripped, True)] + ([Segment(text[len(stripped) :])] if stripped != text else [])


def _words(old: str, new: str, budget: Budget) -> tuple[tuple[Segment, ...], tuple[Segment, ...]]:
    a, b = _WORDS.findall(old), _WORDS.findall(new)
    pair = Budget(min(PAIR_BUDGET, budget.remaining))
    opcodes = bounded_opcodes(a, b, pair)
    budget.remaining -= pair.spent
    if opcodes is None:
        return (Segment(old, True),), (Segment(new, True),)
    removed: list[Segment] = []
    added: list[Segment] = []
    for tag, i1, i2, j1, j2 in opcodes:
        removed.extend(_marked(a[i1:i2], tag != "equal"))
        added.extend(_marked(b[j1:j2], tag != "equal"))
    return tuple(removed), tuple(added)


def _diff_lines(old: list[_Line], new: list[_Line]) -> list[tuple[_Line, DiffLine]]:
    old_texts, new_texts = [line.text for line in old], [line.text for line in new]
    opcodes = bounded_opcodes(old_texts, new_texts, Budget(LINE_BUDGET))
    if opcodes is None:
        raise TooLargeToDiff("the versions differ in too many places to compare")
    budget = Budget(REQUEST_BUDGET)
    out: list[tuple[_Line, DiffLine]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            out.extend((line, DiffLine("same", (Segment(line.text),))) for line in new[j1:j2])
            continue
        removed, added = old[i1:i2], new[j1:j2]
        for a, b in zip(removed, added):
            was, now = _words(a.text, b.text, budget)
            out.extend([(a, DiffLine("removed", was)), (b, DiffLine("added", now))])
        paired = min(len(removed), len(added))
        out.extend((line, DiffLine("removed", (Segment(line.text, True),))) for line in removed[paired:])
        out.extend((line, DiffLine("added", (Segment(line.text, True),))) for line in added[paired:])
    return out


def check_size(*html_bytes: int) -> None:
    """Refuse documents over `MAX_HTML_BYTES` before reading or parsing them."""
    if max(html_bytes) > MAX_HTML_BYTES:
        raise TooLargeToDiff(f"a version is larger than {MAX_HTML_BYTES // 1_000_000} MB of HTML")


def diff_sections(old_html: str, new_html: str) -> list[Section]:
    """Sections in reading order; the first has `heading` None when text precedes every heading.

    A heading replaced on its own (a rename) keeps one section, named after the new heading.
    Raises `TooLargeToDiff`.
    """
    check_size(len(old_html.encode("utf-8")), len(new_html.encode("utf-8")))
    old, new = _lines(extract_text(old_html).markdown), _lines(extract_text(new_html).markdown)
    if max(len(old), len(new)) > MAX_LINES:
        raise TooLargeToDiff(f"a version has more than {MAX_LINES:,} lines of text")
    sections: list[Section] = []
    after_removed_heading = False
    for source, line in _diff_lines(old, new):
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
