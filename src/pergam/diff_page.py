"""The page behind `/{id}/diff/v{a}..v{b}`, from `templates/diff.html` and `diff.css`.

The bar holds a breadcrumb, two version pickers (plain links to other ranges, so
they work without script), a "Sections" dropdown that names every section with
its additions and removals and links to it, the summary and a link to the newer
version. The changes sit in one centred column.
Changed sections are open, with long runs of unchanged lines inside them cut
down to `CONTEXT` lines around each change; unchanged sections are collapsed.
"""

from __future__ import annotations

import html as html_lib
from pathlib import Path
from string import Template

from pergam import icons
from pergam.diff import DiffLine, Section, TooLargeToDiff
from pergam.layout import HEAD_ASSETS, SearchBox, top_bar
from pergam.static_files import PAGES_SCRIPT_PATH, hashed_url
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


def section_id(index: int) -> str:
    return f"sec-{index + 1}"


def _counts(section: Section) -> str:
    if not section.changed:
        return "unchanged"
    if all(line.kind == "added" for line in section.lines):
        return f'<span class="plus">+{section.added}</span> new'
    if all(line.kind == "removed" for line in section.lines):
        return f'<span class="minus">−{section.removed}</span> removed'
    return f'<span class="plus">+{section.added}</span> <span class="minus">−{section.removed}</span>'


def _section(index: int, section: Section) -> str:
    anchor = section_id(index)
    if not section.changed:
        return (
            f'<details class="sec sec--same" id="{anchor}"><summary><span class="sec__name">{_heading(section)}</span>'
            f'<span class="sec__count">unchanged</span></summary>'
            f'{"".join(_line(line) for line in section.lines)}</details>'
        )
    return (
        f'<section class="sec sec--changed" id="{anchor}"><h2><span class="sec__name">{_heading(section)}</span>'
        f'<span class="sec__count">{_counts(section)}</span></h2>'
        f"{_changed_lines(section.lines)}</section>"
    )


def _toc(sections: list[Section] | TooLargeToDiff) -> str:
    if isinstance(sections, TooLargeToDiff):
        return '<p class="toc__empty">Not compared, so no sections.</p>'
    if not sections:
        return '<p class="toc__empty">Neither version has text.</p>'
    items = "".join(
        f'<li><a class="toc__link{" toc__link--same" if not section.changed else ""}" href="#{section_id(i)}">'
        f'<span class="toc__name">{_heading(section)}</span><span class="toc__count">{_counts(section)}</span></a></li>'
        for i, section in enumerate(sections)
    )
    return f'<ol class="toc__list">{items}</ol>'


def _summary(sections: list[Section]) -> str:
    changed = [section for section in sections if section.changed]
    if not changed:
        return "No differences in the text."
    added, removed = sum(s.added for s in changed), sum(s.removed for s in changed)
    return (
        f'<span class="plus">+{added}</span> <span class="minus">−{removed}</span> lines · '
        f"{len(changed)} of {len(sections)} section{'s' if len(sections) != 1 else ''} changed"
    )


def _too_large(pergam_id: str, old: VersionInfo, new: VersionInfo, reason: str) -> str:
    links = " and ".join(f'<a href="/{pergam_id}/v{info.version}/text">v{info.version}</a>' for info in (old, new))
    return (
        f'<section class="too-large" role="status"><h2>Too large to diff</h2>'
        f"<p>{esc(reason[:1].upper() + reason[1:])}. Read the text of {links} instead.</p></section>"
    )


def _picker_item(href: str, info: VersionInfo, chosen: bool) -> str:
    current = ' aria-current="page"' if chosen else ""
    note = f'<span class="versions__note">{esc(info.note)}</span>' if info.note else ""
    return (
        f'<li><a href="{href}"{current}><span class="versions__n">v{info.version}</span>'
        f'<time datetime="{info.created_at.isoformat()}">{info.created_at.strftime("%Y-%m-%d %H:%M")}</time>'
        f"{note}</a></li>"
    )


def _picker(pergam_id: str, side: str, chosen: int, other: int, versions: list[VersionInfo]) -> str:
    """Links to the same comparison with this side set to each version in turn, newest first."""

    def href(version: int) -> str:
        pair = (version, other) if side == "old" else (other, version)
        return f"/{pergam_id}/diff/v{pair[0]}..v{pair[1]}"

    items = "".join(_picker_item(href(info.version), info, info.version == chosen) for info in versions)
    label = "older version" if side == "old" else "newer version"
    menu_class = "dropdown__menu versions__list" + (" dropdown__menu--end" if side == "new" else "")
    return (
        f'<details class="dropdown versions picker--{side}">'
        f'<summary class="ver dropdown__toggle">v{chosen}<span class="sr-only">, {label}</span></summary>'
        f'<ol class="{menu_class}" reversed>{items}</ol></details>'
    )


def _sections_menu(toc: str) -> str:
    """The section list, collapsed behind a "Sections" button in the bar, so the changes keep the page."""
    return (
        '<details class="dropdown toc"><summary class="btn dropdown__toggle">'
        f'{icons.SECTIONS}<span class="toc__label">Sections</span></summary>'
        '<nav class="dropdown__menu dropdown__menu--end toc__menu" aria-labelledby="toc-title">'
        f'<h2 class="toc__title" id="toc-title">Sections</h2>{toc}</nav></details>'
    )


def _bar(
    pergam_id: str, old: VersionInfo, new: VersionInfo, versions: list[VersionInfo], summary: str, toc: str
) -> tuple[str, str]:
    middle = (
        '<div class="crumbs"><nav aria-label="Breadcrumb"><a href="/">Index</a>'
        '<span class="crumbs__sep" aria-hidden="true">/</span>'
        f'<a class="crumbs__doc" href="/{pergam_id}/v{new.version}/view">{esc(new.title)}</a></nav>'
        '<span class="crumbs__sep" aria-hidden="true">/</span><h1 class="bar__title">Changes</h1></div>'
        '<div class="pickers" role="group" aria-label="Versions compared">'
        f'{_picker(pergam_id, "old", old.version, new.version, versions)}'
        '<span class="pickers__arrow" aria-hidden="true">→</span>'
        f'{_picker(pergam_id, "new", new.version, old.version, versions)}</div>'
    )
    actions = (
        f"{_sections_menu(toc)}"
        f'<span class="bar__summary">{summary}</span>'
        f'<a class="btn btn--primary" href="/{pergam_id}/v{new.version}/view">Open v{new.version}</a>'
    )
    return middle, actions


def render_diff(
    pergam_id: str,
    old: VersionInfo,
    new: VersionInfo,
    sections: list[Section] | TooLargeToDiff,
    versions: list[VersionInfo] | None = None,
) -> str:
    """`sections` is what `diff_sections` returned, or the `TooLargeToDiff` it raised.

    `versions` (newest first) fill the pickers; without them they offer just the two compared.
    """
    choices = versions or sorted({old.version: old, new.version: new}.values(), key=lambda info: -info.version)
    if isinstance(sections, TooLargeToDiff):
        summary, body = "Not compared.", _too_large(pergam_id, old, new, str(sections))
    else:
        body = "".join(_section(i, section) for i, section in enumerate(sections))
        body = body or '<p class="empty">Neither version has text.</p>'
        summary = _summary(sections)
    middle, actions = _bar(pergam_id, old, new, choices, summary, _toc(sections))
    return _PAGE.substitute(
        head_assets=HEAD_ASSETS,
        pages_script=hashed_url(PAGES_SCRIPT_PATH),
        css=_CSS,
        top_bar=top_bar(middle=middle, actions=actions, search=SearchBox(collapsed=True)),
        page_title=esc(f"{new.title} · v{old.version} → v{new.version} · pergam"),
        old=old.version,
        new=new.version,
        old_href=f"/{pergam_id}/v{old.version}/view",
        new_href=f"/{pergam_id}/v{new.version}/view",
        old_note=f'<span class="note">{esc(old.note)}</span>' if old.note else "",
        new_note=f'<span class="note">{esc(new.note)}</span>' if new.note else "",
        summary=summary,
        sections=body,
    )
