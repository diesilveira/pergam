"""The page behind `/{id}/diff/v{a}..v{b}`, from `templates/diff.html` and `diff.css`.

Changed sections are open, with long runs of unchanged lines inside them cut
down to `CONTEXT` lines around each change; unchanged sections are collapsed.
"""

from __future__ import annotations

import html as html_lib
from pathlib import Path
from string import Template

from pergam.diff import DiffLine, Section
from pergam.store import VersionInfo

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Template((_TEMPLATES / "diff.html").read_text(encoding="utf-8").rstrip("\n"))
_CSS = (_TEMPLATES / "diff.css").read_text(encoding="utf-8")

CONTEXT = 2
SIGNS = {"same": " ", "added": "+", "removed": "−"}
MARKS = {"added": "ins", "removed": "del"}

esc = html_lib.escape


def _line(line: DiffLine) -> str:
    mark = MARKS.get(line.kind)
    body = "".join(
        f"<{mark}>{esc(segment.text)}</{mark}>" if mark and segment.changed else esc(segment.text)
        for segment in line.segments
    )
    return (
        f'<div class="line line--{line.kind}"><span class="line__sign" aria-hidden="true">{SIGNS[line.kind]}</span>'
        f'<span class="line__text">{body}</span></div>'
    )


def _gap(count: int) -> str:
    return f'<div class="gap">… {count} unchanged line{"s" if count != 1 else ""}</div>'


def _changed_lines(lines: list[DiffLine]) -> str:
    near = {
        k
        for i, line in enumerate(lines)
        if line.kind != "same"
        for k in range(max(0, i - CONTEXT), min(len(lines), i + CONTEXT + 1))
    }
    out: list[str] = []
    skipped = 0
    for i, line in enumerate(lines):
        if i in near:
            if skipped:
                out.append(_gap(skipped))
                skipped = 0
            out.append(_line(line))
        else:
            skipped += 1
    if skipped:
        out.append(_gap(skipped))
    return "".join(out)


def _heading(section: Section) -> str:
    return esc(section.heading) if section.heading is not None else "Before the first heading"


def _section(section: Section) -> str:
    if not section.changed:
        return (
            f'<details class="sec sec--same"><summary><span class="sec__name">{_heading(section)}</span>'
            f'<span class="sec__count">unchanged</span></summary>'
            f'{"".join(_line(line) for line in section.lines)}</details>'
        )
    return (
        f'<section class="sec sec--changed"><h2><span class="sec__name">{_heading(section)}</span>'
        f'<span class="sec__count"><span class="plus">+{section.added}</span> '
        f'<span class="minus">−{section.removed}</span></span></h2>'
        f"{_changed_lines(section.lines)}</section>"
    )


def _summary(sections: list[Section]) -> str:
    changed = [section for section in sections if section.changed]
    if not changed:
        return "No differences in the text."
    added, removed = sum(s.added for s in changed), sum(s.removed for s in changed)
    return (
        f'<span class="plus">+{added}</span> <span class="minus">−{removed}</span> lines · '
        f"{len(changed)} of {len(sections)} section{'s' if len(sections) != 1 else ''} changed"
    )


def render_diff(pergam_id: str, old: VersionInfo, new: VersionInfo, sections: list[Section]) -> str:
    return _PAGE.substitute(
        css=_CSS,
        page_title=esc(f"{new.title} · v{old.version} → v{new.version} · pergam"),
        title=esc(new.title),
        old=old.version,
        new=new.version,
        old_href=f"/{pergam_id}/v{old.version}/view",
        new_href=f"/{pergam_id}/v{new.version}/view",
        old_note=f'<span class="note">{esc(old.note)}</span>' if old.note else "",
        new_note=f'<span class="note">{esc(new.note)}</span>' if new.note else "",
        summary=_summary(sections),
        sections="".join(_section(section) for section in sections) or '<p class="empty">Neither version has text.</p>',
    )
