"""The Filters button, the summary of active filters, the filter dialog's markup and the `/filters` page."""

from __future__ import annotations

import html
import re
from urllib.parse import urlencode

import pytest

from pergam.filters_ui import AUTHORS_SHOWN, CHIPS_SHOWN, FIND_AUTHORS_ABOVE, FIND_TAGS_ABOVE, TAGS_SHOWN
from support import Client, listed_order, patch_meta


@pytest.fixture
def library(client: Client) -> dict[str, str]:
    ids = {
        "plan": client.publish(title="Plan", type="plan", author="ana@example.com", tags=["infra", "ops"])["id"],
        "report": client.publish(title="Report", type="reporte", author="bo@example.com", tags=["ops"])["id"],
        "other": client.publish(title="Other", type="otro", author="ana@example.com")["id"],
        "old": client.publish(title="Old", type="plan", author="cy@example.com", tags=["infra"])["id"],
    }
    patch_meta(client, ids["old"], {"archived": True})
    return ids


def page(client: Client, query: str = "") -> str:
    resp = client.get(f"/{query}")
    assert resp.status == 200, resp.text
    return resp.text


def trigger(text: str) -> str:
    return re.search(r'<a class="btn filters-btn[^"]*"[^>]*>.*?</a>', text).group(0)


def dialog(client: Client, query: str = "") -> str:
    """The dialog's form, as filters.js fetches it on first open."""
    resp = client.get(f"/filters/form{query}")
    assert resp.status == 200, resp.text
    return resp.text


def section(text: str, key: str) -> str:
    match = re.search(rf'<fieldset class="fform__section fform__section--{key}".*?</fieldset>', text, re.S)
    return match.group(0) if match else ""


@pytest.mark.parametrize(
    ("query", "count"),
    [("", 0), ("?q=plan&sort=title", 0), ("?type=otro&type=plan&tag=ops", 3), ("?state=archived", 1), ("?author=ana@example.com&state=all", 2)],
)
def test_the_filters_button_counts_values_and_a_status_but_never_search_or_sort(
    client: Client, library: dict[str, str], query: str, count: int
) -> None:
    button = trigger(page(client, query))
    href = "/filters" + query.replace("&", "&amp;").replace("@", "%40")

    assert f'href="{href}"' in button
    assert 'aria-haspopup="dialog"' in button and "data-filters-open" in button
    if count:
        assert f'aria-label="Filters, {count} active"' in button
        assert f'<span class="filters-btn__count" aria-hidden="true">{count}</span>' in button
        assert "is-on" in button
    else:
        assert "aria-label" not in button and "filters-btn__count" not in button


def test_each_active_value_is_a_chip_whose_cross_removes_only_it(client: Client, library: dict[str, str]) -> None:
    text = page(client, "?type=plan&type=otro&author=ana@example.com&state=all&q=x&sort=title")

    assert '<span class="fchip__key">Type:</span> <span class="fchip__value">plan</span>' in text
    assert 'href="/?type=otro&amp;author=ana%40example.com&amp;state=all&amp;q=x&amp;sort=title" aria-label="Remove filter Type: plan"' in text
    assert 'aria-label="Remove filter Author: ana"' in text and 'title="ana@example.com"' in text
    assert '<span class="fchip__value">Active and archived</span>' in text
    assert 'href="/?type=otro&amp;type=plan&amp;author=ana%40example.com&amp;q=x&amp;sort=title" aria-label="Remove filter Active and archived"' in text
    assert '<a class="summary__clear" href="/?q=x&amp;sort=title" aria-label="Clear all filters">Clear all</a>' in text


def test_archived_only_has_its_own_chip(client: Client, library: dict[str, str]) -> None:
    text = page(client, "?state=archived")

    assert '<span class="fchip__value">Archived only</span>' in text
    assert 'href="/" aria-label="Remove filter Archived only"' in text


def test_more_than_five_chips_show_four_and_a_link_to_all(client: Client, library: dict[str, str]) -> None:
    pairs = [("tag", f"t{n}") for n in range(7)] + [("state", "all")]

    text = page(client, "?" + urlencode(pairs))

    assert text.count('class="fchip__remove"') == CHIPS_SHOWN - 1
    assert '<a class="fchip fchip--more" href="/filters?' in text
    assert 'data-filters-open aria-label="Show all 8 active filters">+4 more</a>' in text


def test_the_dialog_lists_every_value_with_counts_and_dims_empty_ones(client: Client, library: dict[str, str]) -> None:
    form = dialog(client, "?type=reporte&tag=infra")
    types = section(form, "type")

    assert '<h2 class="fform__title" id="filters-title">' in form and 'aria-label="Close filters"' in form
    assert re.findall(r'name="type" value="(\w+)"', types) == ["reporte", "plan", "investigacion", "informe", "otro"]
    assert re.search(r'<input type="checkbox" name="type" value="reporte" checked', types)
    assert re.search(r'<label class="pill is-zero"[^>]*data-value="informe"><input type="checkbox" name="type" value="informe" aria-disabled="true"', types)
    assert 'aria-label="informe, 0 pergams, unavailable"' in types and 'aria-label="reporte, 0 pergams"' in types
    assert '<span class="fform__selected" data-selected>1 selected</span>' in types
    assert 'aria-label="Clear type filters" data-section-clear>Clear</button>' in types
    assert "<legend class=\"fform__legend\">Type</legend>" in types and "Any of" in types


def test_a_selected_value_is_never_dimmed_and_comes_first(client: Client, library: dict[str, str]) -> None:
    form = dialog(client, "?tag=nothing-has-this&tag=ops")
    tags = section(form, "tag")

    assert set(re.findall(r'name="tag" value="([\w-]+)"', tags)[:2]) == {"nothing-has-this", "ops"}
    assert '<input type="checkbox" name="tag" value="nothing-has-this" checked aria-label="nothing-has-this, 0 pergams">' in tags


def test_the_status_is_a_three_way_radio_group(client: Client, library: dict[str, str]) -> None:
    status = section(dialog(client, "?state=archived"), "state")

    assert re.findall(r'<input type="radio" name="state" value="(\w*)"', status) == ["", "archived", "all"]
    assert '<input type="radio" name="state" value="archived" checked>' in status
    assert "Archived shows only archived pergams." in status


def test_the_apply_button_says_what_it_shows(client: Client, library: dict[str, str]) -> None:
    some, one, none = dialog(client, "?tag=ops"), dialog(client, "?type=otro"), dialog(client, "?tag=nope")

    assert ">Show 2 pergams</button>" in some
    assert ">Show 1 pergam</button>" in one
    assert "data-filters-apply disabled>No matches</button>" in none


def test_search_and_sort_ride_along_as_hidden_fields(client: Client, library: dict[str, str]) -> None:
    form = dialog(client, "?q=plan&sort=title")

    assert '<input type="hidden" name="q" value="plan"><input type="hidden" name="sort" value="title">' in form
    assert 'method="get" action="/"' in form


def test_sections_with_nothing_to_choose_are_left_out(client: Client) -> None:
    client.publish(type="plan", author="ana@example.com")

    form = dialog(client)

    assert section(form, "type") == "" and section(form, "author") == "" and section(form, "tag") == ""
    assert section(form, "state")


@pytest.fixture
def crowd(client: Client) -> None:
    for n in range(20):
        client.publish(title=f"Doc {n}", author=f"person{n:02d}@example.com", tags=[f"tag{n:02d}", f"tag{n + 20:02d}"])


def test_long_lists_fold_after_the_most_used_and_get_a_find_field(client: Client, crowd: None) -> None:
    form = dialog(client)
    authors, tags = section(form, "author"), section(form, "tag")

    assert authors.count("data-extra hidden") == 20 - AUTHORS_SHOWN
    assert f'data-more-label="Show all 20 authors" data-less-label="Show fewer authors">Show all 20 authors</button>' in authors
    assert 'aria-label="Find an author"' in authors and 20 > FIND_AUTHORS_ABOVE
    assert tags.count("data-extra hidden") == 40 - TAGS_SHOWN
    assert 'aria-label="Find a tag"' in tags and 40 > FIND_TAGS_ABOVE
    assert "No tags match “<span data-nomatch-text></span>”" in tags


def test_a_list_that_would_hide_three_or_fewer_shows_everything(client: Client) -> None:
    for n in range(AUTHORS_SHOWN + 3):
        client.publish(author=f"p{n}@example.com")

    authors = section(dialog(client), "author")

    assert "data-extra" not in authors and "data-more" not in authors and "data-find" not in authors


def test_the_filters_page_has_the_same_form_without_folding(client: Client, crowd: None) -> None:
    resp = client.get("/filters?tag=tag03&sort=title&q=doc")
    text = resp.text
    form = re.search(r'<form class="fform".*?</form>', text, re.S).group(0)

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert '<form class="fform" method="get" action="/" aria-labelledby="filters-title">' in text
    assert "data-extra" not in text and "data-find" not in text and "data-more" not in text
    assert form.count('name="author"') == 20 and form.count('name="tag"') == 40
    assert '<input type="checkbox" name="tag" value="tag03" checked' in text
    assert '<a href="/?tag=tag03&amp;q=doc&amp;sort=title">Cancel</a>' in text
    assert '<button type="submit" class="btn btn--primary fform__apply">Show results</button>' in text
    assert '<input type="hidden" name="q" value="doc"><input type="hidden" name="sort" value="title">' in text
    assert "<script" not in text.split("</head>")[1]


@pytest.mark.parametrize("query", ["?state=gone", "?cursor=nope", "?" + urlencode([("tag", str(n)) for n in range(51)])])
def test_the_filters_page_rejects_what_the_index_rejects(client: Client, query: str) -> None:
    assert client.get(f"/filters{query}").status == 400


def submit(text: str) -> str:
    """The query a GET form submits, from the checked and hidden inputs, in document order."""
    form = re.search(r'<form class="fform".*?</form>', text, re.S).group(0)
    pairs = []
    for tag in re.findall(r"<input [^>]*>", form):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if attrs.get("type") == "hidden" or " checked" in tag:
            pairs.append((attrs["name"], html.unescape(attrs["value"])))
    return urlencode(pairs)


def test_submitting_the_page_reproduces_the_selection(client: Client, library: dict[str, str]) -> None:
    query = "type=plan&type=otro&author=ana%40example.com&tag=infra&state=all&q=plan&sort=title"

    submitted = submit(client.get(f"/filters?{query}").text)

    assert submitted == query
    assert set(listed_order(client.get(f"/?{submitted}").text)) == {library["plan"]}


def test_values_are_escaped_in_chips_and_the_form(client: Client) -> None:
    client.publish(author='<b>"x"</b>@example.com', tags=["<i>"])

    text = page(client, "?" + urlencode([("author", '<b>"x"</b>@example.com'), ("tag", "<i>")]))

    assert "<b>" not in text.split("<body>")[1] and "<i>" not in text.split("<body>")[1]
    assert "&lt;b&gt;&quot;x&quot;&lt;/b&gt;" in text


def test_pinned_cards_keep_showing_the_pinned_pergams_that_match(client: Client, library: dict[str, str]) -> None:
    for pid in (library["plan"], library["report"]):
        patch_meta(client, pid, {"pinned": True})

    text = page(client, "?type=plan&type=otro")

    assert re.findall(r'<a class="pin__title" href="/([0-9a-f]+)/view"', text) == [library["plan"]]
    assert listed_order(text).count(library["plan"]) == 1
