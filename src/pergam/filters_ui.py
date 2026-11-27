"""The index's filter controls: the Filters button, the summary of active filters and the filter form.

The form is one partial rendered twice: inside a `<dialog>` on the index, which
`static/filters.js` opens when the Filters button is clicked, and as the `/filters`
page the same button links to, which works without script. Its fields are named as
the index's query (`type`, `author`, `tag`, `state`, plus `q` and `sort` kept
hidden), so submitting it with GET lands on the filtered index.

Within Type, Author and Tags a pergam matches any selected value; sections combine.
Every value is listed with what selecting it would give (see `store.Facets`);
values with no result are dimmed and moved to the end, never hidden, and selected
values come first. In the dialog long lists show their most used values first and
expand in place, with a find field for long author and tag lists; the page shows
every value.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from string import Template

from pergam.api import facet_values
from pergam.index_links import avatar, esc, index_link, query_pairs, short_author, without_value
from pergam.layout import HEAD_ASSETS, SearchBox, top_bar
from pergam.states import ACTIVE, ALL, ARCHIVED
from pergam.store import Facets, IndexFilters, IndexStats

_TEMPLATES = Path(__file__).resolve().parent / "templates"
FORM_CSS = (_TEMPLATES / "filters.css").read_text(encoding="utf-8")
_PAGE = Template((_TEMPLATES / "filters.html").read_text(encoding="utf-8").rstrip("\n"))

AUTHORS_SHOWN = 8
TAGS_SHOWN = 15
FIND_AUTHORS_ABOVE = 12
FIND_TAGS_ABOVE = 15
FEW_HIDDEN = 3
CHIPS_SHOWN = 5
STATE_CHIPS = {ARCHIVED: "Archived only", ALL: "Active and archived"}
STATUS_OPTIONS = ((ACTIVE, "Active"), (ARCHIVED, "Archived"), (ALL, "All"))

_SVG = 'aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"'
FUNNEL = f'<svg class="icon" {_SVG} width="16" height="16" stroke-width="2"><path d="M3 5h18l-7 8v6l-4-2v-4z"/></svg>'
CROSS = f'<svg class="icon" {_SVG} width="11" height="11" stroke-width="2.5"><path d="M6 6l12 12M18 6L6 18"/></svg>'
CLOSE = f'<svg class="icon" {_SVG} width="16" height="16" stroke-width="2"><path d="M6 6l12 12M18 6L6 18"/></svg>'


def filter_count(filters: IndexFilters) -> int:
    """Selected values across sections, plus one for a status other than Active; search and sort never count."""
    return len(filters.types) + len(filters.authors) + len(filters.tags) + (filters.state != ACTIVE)


def filters_page_link(filters: IndexFilters) -> str:
    return index_link(replace(filters, cursor=None), path="/filters")


def trigger(filters: IndexFilters) -> str:
    """The Filters button: a link to `/filters`, which filters.js turns into opening the dialog."""
    count = filter_count(filters)
    label = f' aria-label="Filters, {count} active"' if count else ""
    badge = f'<span class="filters-btn__count" aria-hidden="true">{count}</span>' if count else ""
    return (
        f'<a class="btn filters-btn{" is-on" if count else ""}" href="{esc(filters_page_link(filters))}"'
        f' aria-haspopup="dialog"{label} data-filters-open>{FUNNEL}<span>Filters</span>{badge}</a>'
    )


@dataclass(frozen=True)
class Chip:
    key: str
    value: str
    title: str
    remove: IndexFilters

    @property
    def label(self) -> str:
        return f"{self.key}: {self.value}" if self.key else self.value


def chips(filters: IndexFilters) -> list[Chip]:
    """One chip per active value, in the order of the URL; removing one keeps everything else."""
    out = [Chip("Type", value, value, without_value(filters, "types", value)) for value in sorted(filters.types)]
    out += [
        Chip("Author", short_author(value), value, without_value(filters, "authors", value)) for value in sorted(filters.authors)
    ]
    out += [Chip("Tag", value, value, without_value(filters, "tags", value)) for value in sorted(filters.tags)]
    if filters.state != ACTIVE:
        out.append(Chip("", STATE_CHIPS[filters.state], STATE_CHIPS[filters.state], replace(filters, state=ACTIVE, cursor=None)))
    return out


def _chip(chip: Chip) -> str:
    key = f'<span class="fchip__key">{esc(chip.key)}:</span> ' if chip.key else ""
    return (
        f'<span class="fchip" title="{esc(chip.title)}">{key}<span class="fchip__value">{esc(chip.value)}</span>'
        f'<a class="fchip__remove" href="{esc(index_link(chip.remove))}" aria-label="Remove filter {esc(chip.label)}">'
        f"{CROSS}</a></span>"
    )


def scope_total(stats: IndexStats, state: str) -> int:
    """What "everything" means for the summary's "N of M": the active pergams, or all of them once archived ones show."""
    return stats.total_pergams - (stats.total_archived if state == ACTIVE else 0)


def summary(matching: int, stats: IndexStats, filters: IndexFilters) -> str:
    """"23 of 312", one removable chip per active value and "Clear all"; the count alone on a plain search."""
    count = filter_count(filters)
    if not count and not filters.q:
        return ""
    results = (
        f'<span class="summary__count" tabindex="-1" data-results>{matching} of {scope_total(stats, filters.state)}'
        '<span class="sr-only"> pergams shown</span></span>'
    )
    if not count:
        return f'<div class="summary">{results}</div>'
    active = chips(filters)
    shown = active if len(active) <= CHIPS_SHOWN else active[: CHIPS_SHOWN - 1]
    more = ""
    if len(shown) < len(active):
        more = (
            f'<a class="fchip fchip--more" href="{esc(filters_page_link(filters))}" data-filters-open'
            f' aria-label="Show all {len(active)} active filters">+{len(active) - len(shown)} more</a>'
        )
    clear = esc(index_link(IndexFilters(q=filters.q, sort=filters.sort)))
    return (
        f'<div class="summary">{results}{"".join(_chip(chip) for chip in shown)}{more}'
        f'<a class="summary__clear" href="{clear}" aria-label="Clear all filters">Clear all</a></div>'
    )


@dataclass(frozen=True)
class Item:
    value: str
    count: int
    checked: bool

    @property
    def unavailable(self) -> bool:
        return self.count == 0 and not self.checked


def items(counts: list[tuple[str, int]], selected: tuple[str, ...], first: tuple[str, ...] = ()) -> list[Item]:
    """Selected values first, then the others in facet order (configured types, else most used), then the empty ones."""
    listed = [Item(entry["value"], entry["count"], entry["value"] in selected) for entry in facet_values(counts, selected, first)]
    return (
        [item for item in listed if item.checked]
        + [item for item in listed if not item.checked and item.count]
        + [item for item in listed if item.unavailable]
    )


def _count(item: Item) -> str:
    return f'<span class="pill__count" data-count>{item.count}</span>'


def accessible_name(shown: str, item: Item) -> str:
    """What assistive technology reads: "plan, 2 pergams", and ", unavailable" for a value with no result."""
    noun = "pergam" if item.count == 1 else "pergams"
    return f"{shown}, {item.count} {noun}" + (", unavailable" if item.unavailable else "")


def _label(cls: str, text: str) -> str:
    return f'<span class="{cls}">{esc(text)}</span>'


def _input(name: str, item: Item, shown: str) -> str:
    checked = " checked" if item.checked else ""
    disabled = ' aria-disabled="true"' if item.unavailable else ""
    label = esc(accessible_name(shown, item))
    return f'<input type="checkbox" name="{name}" value="{esc(item.value)}"{checked}{disabled} aria-label="{label}">'


def _extra(extra: bool) -> str:
    return " data-extra hidden" if extra else ""


def pill(name: str, item: Item, extra: bool = False) -> str:
    return (
        f'<label class="pill{" is-zero" if item.unavailable else ""}" data-item data-value="{esc(item.value)}"{_extra(extra)}>'
        f'{_input(name, item, item.value)}{_label("pill__label", item.value)}{_count(item)}</label>'
    )


def author_row(item: Item, extra: bool = False) -> str:
    return (
        f'<label class="arow{" is-zero" if item.unavailable else ""}" data-item data-value="{esc(item.value)}"'
        f' title="{esc(item.value)}"{_extra(extra)}>'
        f'{_input("author", item, short_author(item.value))}{avatar(item.value)}{_label("arow__name", short_author(item.value))}'
        f"{_count(item)}</label>"
    )


@dataclass(frozen=True)
class Section:
    key: str
    name: str
    legend: str
    noun: str
    shown: int
    find_above: int
    find_label: str


TYPE = Section("type", "type", "Type", "types", 10**6, 10**6, "")
AUTHOR = Section("author", "author", "Author", "authors", AUTHORS_SHOWN, FIND_AUTHORS_ABOVE, "Find an author")
TAG = Section("tag", "tag", "Tags", "tags", TAGS_SHOWN, FIND_TAGS_ABOVE, "Find a tag")


def section(spec: Section, values: list[Item], dialog: bool, existing: int) -> str:
    """One fieldset; in the dialog, values past `spec.shown` start folded (never a selected one, never just a few).

    A section with nothing to choose is left out: no pergam has two different values
    of it (`existing` counts the values any pergam has), and none is selected.
    """
    selected = sum(item.checked for item in values)
    if not selected and existing <= 1:
        return ""
    keep = max(spec.shown, selected)
    folds = dialog and len(values) - keep > FEW_HIDDEN
    render = pill if spec.key != "author" else (lambda _name, item, extra=False: author_row(item, extra))
    entries = "".join(render(spec.name, item, folds and index >= keep) for index, item in enumerate(values))
    find = ""
    if dialog and len(values) > spec.find_above:
        noun = spec.noun
        find = (
            f'<input class="input fform__find" type="search" placeholder="{spec.find_label}" aria-label="{spec.find_label}"'
            f' autocomplete="off" data-find>'
            f'<p class="fform__nomatch" data-nomatch hidden>No {noun} match “<span data-nomatch-text></span>”</p>'
        )
    more = ""
    if folds:
        more = (
            f'<button type="button" class="fform__more" aria-expanded="false" data-more'
            f' data-more-label="Show all {len(values)} {spec.noun}" data-less-label="Show fewer {spec.noun}">'
            f"Show all {len(values)} {spec.noun}</button>"
        )
    selected_text = f"{selected} selected" if selected else ""
    return (
        f'<fieldset class="fform__section fform__section--{spec.key}" data-section="{spec.key}">'
        f'<legend class="fform__legend">{spec.legend}</legend>'
        f'<div class="fform__meta"><span class="fform__hint">Any of</span>'
        f'<span class="fform__selected" data-selected>{selected_text}</span>'
        f'<button type="button" class="fform__clear" aria-label="Clear {spec.name} filters" data-section-clear'
        f'{"" if dialog and selected else " hidden"}>Clear</button></div>'
        f"{find}"
        f'<div class="fform__values fform__values--{spec.key}">{entries}</div>{more}</fieldset>'
    )


def status_section(filters: IndexFilters) -> str:
    options = "".join(
        f'<label class="seg__option"><input type="radio" name="state" value="{"" if state == ACTIVE else state}"'
        f'{" checked" if filters.state == state else ""}><span>{label}</span></label>'
        for state, label in STATUS_OPTIONS
    )
    return (
        '<fieldset class="fform__section fform__section--state" data-section="state">'
        '<legend class="fform__legend">Status</legend>'
        f'<div class="seg">{options}</div>'
        '<p class="fform__note">Archived shows only archived pergams.</p></fieldset>'
    )


def form_body(facets: Facets, filters: IndexFilters, types: tuple[str, ...], dialog: bool) -> str:
    kept = "".join(
        f'<input type="hidden" name="{key}" value="{esc(value)}">'
        for key, value in (("q", filters.q), ("sort", None if filters.sort == "recent" else filters.sort))
        if value
    )
    return (
        section(TYPE, items(facets.types, filters.types, types), dialog, len(facets.types))
        + section(AUTHOR, items(facets.authors, filters.authors), dialog, len(facets.authors))
        + section(TAG, items(facets.tags, filters.tags), dialog, len(facets.tags))
        + status_section(filters)
        + kept
    )


def dialog_shell(filters: IndexFilters) -> str:
    """The empty modal on the index; filters.js fills it from `data-src` (`/filters/form`) on first open.

    Keeping the form out of the page keeps the index light however many authors and
    tags there are; without script the dialog is never shown.
    """
    source = esc(index_link(replace(filters, cursor=None), path="/filters/form"))
    return f'<dialog class="fdialog" aria-labelledby="filters-title" data-filters-dialog data-src="{source}"></dialog>'


def dialog_form(facets: Facets, filters: IndexFilters, types: tuple[str, ...]) -> str:
    """`/filters/form`: the dialog's form, the same partial as the `/filters` page with folding, find fields and live counts."""
    total = facets.total
    apply_label = f"Show {total} pergam{'s' if total != 1 else ''}" if total else "No matches"
    return (
        '<form class="fform" method="get" action="/" data-filters-form>'
        '<header class="fform__head"><h2 class="fform__title" id="filters-title">'
        '<span class="fform__title--wide">Filter pergams</span><span class="fform__title--narrow">Filters</span></h2>'
        f'<button type="button" class="icon-btn fform__close" aria-label="Close filters" data-filters-close>{CLOSE}</button>'
        "</header>"
        f'<div class="fform__body">{form_body(facets, filters, types, dialog=True)}</div>'
        '<footer class="fform__foot" data-filters-foot>'
        '<p class="fform__none" data-filters-none hidden>No pergams match. Try removing a filter.</p>'
        '<button type="button" class="fform__reset" data-filters-reset>Clear all</button>'
        f'<button type="submit" class="btn btn--primary fform__apply" data-filters-apply{" disabled" if not total else ""}>'
        f"{apply_label}</button>"
        '<p class="sr-only" aria-live="polite" data-filters-live></p>'
        "</footer></form>"
    )


def render_filters_page(facets: Facets, filters: IndexFilters, types: tuple[str, ...], host_display: str) -> str:
    """`/filters`: the same form as a page, every value shown, for readers without script."""
    back = esc(index_link(replace(filters, cursor=None)))
    keep = tuple(query_pairs(replace(filters, q=None, cursor=None)))
    return _PAGE.substitute(
        head_assets=HEAD_ASSETS,
        css=FORM_CSS,
        top_bar=top_bar(host=host_display, search=SearchBox(query=filters.q or "", keep=keep, compact=False)),
        body=form_body(facets, filters, types, dialog=False),
        back=back,
    )
