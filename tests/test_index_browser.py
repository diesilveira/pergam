"""The index and the top-bar search in a real browser: shortcuts, density, the filter column, "Load more".

Needs Playwright and a Chrome or Chromium it can launch. Without them the tests are
skipped, except when `CI` is set: there they fail.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import psycopg
import pytest

from support import Client, in_ci, patch_meta, seed, unavailable

try:
    from playwright import sync_api
except ImportError:
    if in_ci():
        raise
    pytest.skip("Playwright is not installed", allow_module_level=True)

SCHEMES = ["light", "dark"]
PHONE = {"width": 360, "height": 740}


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        for options in ({"channel": "chrome"}, {}):
            try:
                launched = playwright.chromium.launch(**options)
                break
            except sync_api.Error:
                continue
        else:
            unavailable("no Chrome or Chromium that Playwright can launch")
        yield launched
        launched.close()


@pytest.fixture
def site(client: Client) -> dict[str, str]:
    """A small catalogue with long names, several tags, a pinned and an archived pergam, and two versions."""
    long_author = "a.very-long-author-name@example.com"
    plan = client.publish(
        title="A rather long title for a plan that will not fit on a phone in one line",
        type="plan",
        author=long_author,
        tags=["roadmap", "infrastructure-migration", "q3"],
        html="<h1>Plan</h1><p>The cutover window is on Saturday morning. Then rollback.</p>",
    )["id"]
    client.publish(id=plan, title="A rather long title for a plan that will not fit on a phone in one line", type="plan",
                   author=long_author, html="<p>The cutover window moved to Sunday.</p>")
    pinned = client.publish(title="Pinned board", type="reporte", author="bo@example.com", tags=["costs"],
                            html="<p>Monthly spend by service.</p>")["id"]
    patch_meta(client, pinned, {"pinned": True})
    for n in range(12):
        client.publish(title=f"Note {n}", type="otro", author="cy@example.com", tags=[f"tag-{n:02d}"], html=f"<p>Note {n}.</p>")
    archived = client.publish(title="Old plan", type="plan", html="<p>gone</p>")["id"]
    patch_meta(client, archived, {"archived": True})
    base = f"http://127.0.0.1:{client.port}"
    return {"base": base, "index": f"{base}/", "view": f"{base}/{plan}/view", "diff": f"{base}/{plan}/diff/v1..v2", "plan": plan}


def open_page(browser, scheme: str = "light", **options):
    context = browser.new_context(color_scheme=scheme, **options)
    return context, context.new_page()


def active(page) -> dict[str, object]:
    return page.evaluate("({tag: document.activeElement.tagName, search: document.activeElement.hasAttribute('data-search')})")


@pytest.mark.parametrize("name", ["index", "view", "diff"])
def test_slash_focuses_the_search_field_and_escape_clears_it(browser, site, name: str) -> None:
    context, page = open_page(browser)
    page.goto(site[name])

    page.keyboard.press("/")
    assert active(page) == {"tag": "INPUT", "search": True}
    page.keyboard.type("plan")
    page.keyboard.press("Escape")
    assert page.locator("[data-search]").input_value() == ""
    page.keyboard.press("Escape")
    assert active(page)["search"] is False
    context.close()


def test_shortcuts_do_not_fire_while_typing_in_a_field(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["index"])
    page.evaluate("document.body.append(Object.assign(document.createElement('textarea'), {id: 'scratch'}))")

    page.locator("#scratch").focus()
    page.keyboard.type("a/b")
    assert page.locator("#scratch").input_value() == "a/b"
    page.locator("[data-search]").focus()
    page.keyboard.type("x/y")

    assert page.locator("[data-search]").input_value() == "x/y"
    context.close()


@pytest.mark.parametrize("name", ["view", "diff"])
def test_searching_from_a_document_lands_on_the_index_with_the_query(browser, site, name: str) -> None:
    context, page = open_page(browser)
    page.goto(site[name])

    page.keyboard.press("/")
    page.keyboard.type("cutover window")
    page.keyboard.press("Enter")

    page.wait_for_url(f"{site['base']}/?q=cutover+window")
    assert page.locator("h1").inner_text() == "1 result"
    assert page.locator(".row__snippet mark").first.inner_text().lower() in {"cutover", "window"}
    assert page.locator("[data-search]").input_value() == "cutover window"
    context.close()


def test_a_search_from_the_index_keeps_the_active_filters(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(f"{site['base']}/?type=plan&sort=title")

    page.locator("[data-search]").fill("cutover")
    page.keyboard.press("Enter")

    page.wait_for_url(f"{site['base']}/?q=cutover&type=plan&sort=title")
    context.close()


def test_filter_counts_on_a_search_add_up_to_the_results(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(f"{site['base']}/?q=note")

    results = int(page.locator("h1").inner_text().split()[0])
    every, *counts = [int(text) for text in page.locator("section[aria-labelledby=facets-type] .facet__count").all_inner_texts()]

    assert results == every == 12
    assert sum(counts) == results
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_density_toggle_switches_and_is_remembered(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme)
    page.goto(site["index"])
    toggle = page.get_by_role("button", name="Compact")
    row = page.locator(".list .row").first
    comfortable = row.bounding_box()["height"]

    toggle.click()

    assert toggle.get_attribute("aria-pressed") == "true"
    assert row.bounding_box()["height"] < comfortable
    page.reload()
    assert page.evaluate("document.documentElement.dataset.density") == "compact"
    assert page.get_by_role("button", name="Compact").get_attribute("aria-pressed") == "true"
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_filter_column_collapses_on_a_phone(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme, viewport=PHONE)
    page.goto(site["index"])
    tag = page.locator(".facet", has_text="roadmap")

    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    assert page.evaluate("document.querySelector('details.filters').open") is False
    assert tag.is_hidden()
    page.get_by_text("Filters", exact=True).click()
    assert tag.is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    context.close()


@pytest.mark.parametrize("query", ["/", "/?q=note", "/?q=nothing-at-all&type=plan", "/?sort=title"])
@pytest.mark.parametrize("scheme", SCHEMES)
def test_index_states_fit_a_phone(browser, site, scheme: str, query: str) -> None:
    context, page = open_page(browser, scheme, viewport=PHONE)

    page.goto(site["base"] + query)

    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    context.close()


def test_without_script_the_column_stays_open_and_the_toggle_hidden(browser, site) -> None:
    context, page = open_page(browser, java_script_enabled=False, viewport=PHONE)

    page.goto(site["index"])

    assert page.locator(".facet", has_text="roadmap").is_visible()
    assert page.locator("[data-density-toggle]").is_hidden()
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_rows_and_cards_are_reachable_with_tab_and_show_their_focus(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme)
    page.goto(site["index"])
    reached = set()

    for _ in range(80):
        page.keyboard.press("Tab")
        kind = page.evaluate("document.activeElement.className")
        if kind in ("pin__title", "row__title"):
            reached.add(kind)
            holder = page.evaluate(
                "(() => { const el = document.activeElement.closest('.pin, .row'); return getComputedStyle(el).outlineStyle; })()"
            )
            assert holder == "solid"
        if reached == {"pin__title", "row__title"}:
            break

    assert reached == {"pin__title", "row__title"}
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/[0-9a-f]{8}/view$"))
    context.close()


def test_clicking_anywhere_on_a_row_opens_it_but_its_links_still_filter(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["index"])
    row = page.locator(".row", has=page.locator(f'a[href="/{site["plan"]}/view"]'))

    row.scroll_into_view_if_needed()
    date = row.locator(".row__date").bounding_box()
    page.mouse.click(date["x"] + date["width"] / 2, date["y"] + date["height"] / 2)
    page.wait_for_url(site["view"])
    page.go_back()
    row.locator(".tag", has_text="roadmap").click()

    page.wait_for_url(f"{site['base']}/?tag=roadmap")
    context.close()


@pytest.fixture
def crowded(client: Client, db: psycopg.Connection) -> str:
    seed(db)
    return f"http://127.0.0.1:{client.port}/"


@pytest.mark.parametrize("sort", ["recent", "title"])
def test_load_more_appends_the_next_pages_without_repeating(browser, crowded: str, sort: str) -> None:
    context, page = open_page(browser)
    page.goto(f"{crowded}?archived=1&sort={sort}")
    titles = page.locator(".pin__title, .row__title")
    first_page = titles.count()

    page.get_by_role("link", name="Load more").click()
    page.wait_for_function(f"document.querySelectorAll('.pin__title, .row__title').length > {first_page}")
    page.get_by_role("link", name="Load more").click()
    page.wait_for_function("!document.querySelector('[data-load-more]')")

    hrefs = page.locator(".pin__title, .row__title").evaluate_all("(links) => links.map((a) => a.getAttribute('href'))")
    assert len(hrefs) == len(set(hrefs)) == 230
    assert page.url == f"{crowded}?archived=1&sort={sort}"
    assert page.evaluate("document.activeElement.className") == "row__title"
    assert re.fullmatch(r"\d+ more loaded, that is all", page.locator("[data-load-status]").inner_text())
    if sort == "recent":
        assert page.locator(".group[data-group='earlier']").count() == 1
    context.close()


def test_without_script_load_more_is_a_link_to_the_next_page(browser, crowded: str) -> None:
    context, page = open_page(browser, java_script_enabled=False)
    page.goto(crowded)

    page.get_by_role("link", name="Load more").click()

    page.wait_for_url(re.compile(r"\?cursor="))
    assert page.get_by_role("link", name="← first page").is_visible()
    context.close()


BAR_HEIGHT = "document.querySelector('.topbar').getBoundingClientRect().height"


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("name", ["view", "diff"])
def test_the_compact_search_adds_no_row_to_the_bar_on_a_phone(browser, site, name: str, scheme: str) -> None:
    context, page = open_page(browser, scheme, viewport=PHONE)
    page.goto(site[name])
    with_search = page.evaluate(BAR_HEIGHT)
    page.evaluate("document.querySelector('.topbar__search').style.display = 'none'")
    without_search = page.evaluate(BAR_HEIGHT)
    page.evaluate("document.querySelector('.topbar__search').style.display = ''")

    page.locator(".topbar__search-icon").click()

    assert with_search == without_search
    assert active(page) == {"tag": "INPUT", "search": True}
    assert page.evaluate("document.querySelector('[data-search]').getBoundingClientRect().width") > 200
    assert page.evaluate(BAR_HEIGHT) == with_search
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    page.keyboard.type("cutover")
    page.keyboard.press("Enter")
    page.wait_for_url(f"{site['base']}/?q=cutover")
    context.close()


def test_the_index_keeps_the_full_search_field_on_a_phone(browser, site) -> None:
    context, page = open_page(browser, viewport=PHONE)
    page.goto(site["index"])

    assert page.evaluate("document.querySelector('[data-search]').getBoundingClientRect().width") > 200
    context.close()


DESKTOP = {"width": 1280, "height": 800}
ROW_SCROLL = "document.querySelector('[data-pins]').scrollLeft"
BUTTONS_SETTLED = """(() => {
  const row = document.querySelector('[data-pins]');
  const atStart = row.scrollLeft <= 1, atEnd = row.scrollLeft + row.clientWidth >= row.scrollWidth - 1;
  return document.querySelector('[data-pins-prev]').disabled === atStart
    && document.querySelector('[data-pins-next]').disabled === atEnd;
})()"""
FULLY_VISIBLE = """() => {
  const row = document.querySelector('[data-pins]').getBoundingClientRect();
  const cards = Array.from(document.querySelectorAll('.pin')).map((card) => card.getBoundingClientRect());
  return {
    full: cards.filter((c) => c.left >= row.left - 1 && c.right <= row.right + 1).length,
    partial: cards.filter((c) => c.left < row.right - 1 && c.right > row.right + 1).length,
  };
}"""


@pytest.fixture
def pinned_row(client: Client) -> str:
    """Twelve pinned pergams, the default cap, with long titles and tags."""
    for n in range(12):
        pid = client.publish(title=f"Pinned board number {n} with a long title", tags=["costs", f"area-{n}"],
                             html=f"<p>Monthly spend by service, board {n}.</p>")["id"]
        patch_meta(client, pid, {"pinned": True})
    client.publish(title="Listed")
    return f"http://127.0.0.1:{client.port}/"


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["desktop", "phone"])
@pytest.mark.parametrize("scheme", SCHEMES)
def test_pinned_row_buttons_move_it_and_stop_at_the_ends(browser, pinned_row: str, scheme: str, viewport: dict) -> None:
    context, page = open_page(browser, scheme, viewport=viewport, reduced_motion="reduce")
    page.goto(pinned_row)
    previous = page.get_by_role("button", name="Previous pinned pergams")
    following = page.get_by_role("button", name="Next pinned pergams")

    assert page.locator(".pins__count").inner_text() == "12 of 12 pinned"
    assert previous.is_visible() and previous.is_disabled() and following.is_enabled()
    for _ in range(12):
        if following.is_disabled():
            break
        before = page.evaluate(ROW_SCROLL)
        following.click()
        page.wait_for_function(f"{ROW_SCROLL} > {before} && {BUTTONS_SETTLED}")
    assert following.is_disabled() and previous.is_enabled()
    for _ in range(12):
        if previous.is_disabled():
            break
        previous.click()
        page.wait_for_function(BUTTONS_SETTLED)
    assert page.evaluate(ROW_SCROLL) == 0
    assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]
    context.close()


@pytest.mark.parametrize(("viewport", "full"), [(DESKTOP, {3, 4}), (PHONE, {1})], ids=["desktop", "phone"])
def test_cards_keep_a_width_that_shows_more_exist(browser, pinned_row: str, viewport: dict, full: set[int]) -> None:
    context, page = open_page(browser, viewport=viewport)
    page.goto(pinned_row)

    visible = page.evaluate(FULLY_VISIBLE)

    assert visible["full"] in full
    assert visible["partial"] == 1
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_focus_reveals_a_card_that_was_off_screen(browser, pinned_row: str, scheme: str) -> None:
    context, page = open_page(browser, scheme, viewport=PHONE, reduced_motion="reduce")
    page.goto(pinned_row)
    last = page.locator(".pin__title").last

    for _ in range(120):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement === document.querySelectorAll('.pin__title')[11]"):
            break
    else:
        pytest.fail("the last pinned card is not reachable with Tab")

    page.wait_for_function(
        "(() => { const r = document.querySelector('[data-pins]').getBoundingClientRect();"
        " const c = document.querySelectorAll('.pin')[11].getBoundingClientRect();"
        " return c.left >= r.left - 1 && c.right <= r.right + 1; })()"
    )
    assert page.evaluate("getComputedStyle(document.querySelectorAll('.pin')[11]).outlineStyle") == "solid"
    assert last.is_visible()
    context.close()


def test_pinned_row_without_script_scrolls_natively(browser, pinned_row: str) -> None:
    context, page = open_page(browser, java_script_enabled=False, viewport=PHONE)
    page.goto(pinned_row)

    assert page.locator("[data-pins-prev]").is_hidden() and page.locator("[data-pins-next]").is_hidden()
    assert page.evaluate("(() => { const r = document.querySelector('[data-pins]'); return r.scrollWidth > r.clientWidth; })()")
    assert page.evaluate("getComputedStyle(document.querySelector('[data-pins]')).scrollSnapType") == "x mandatory"
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    context.close()


@pytest.mark.parametrize(("motion", "behavior"), [("reduce", "auto"), ("no-preference", "smooth")])
def test_pinned_row_respects_reduced_motion(browser, pinned_row: str, motion: str, behavior: str) -> None:
    context, page = open_page(browser, reduced_motion=motion)
    page.goto(pinned_row)

    page.get_by_role("button", name="Next pinned pergams").click()

    assert page.evaluate("getComputedStyle(document.querySelector('[data-pins]')).scrollBehavior") == behavior
    page.wait_for_function(f"{ROW_SCROLL} > 0")
    context.close()
