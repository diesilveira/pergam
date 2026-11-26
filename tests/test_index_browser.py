"""The index in a real browser: density, the filter column, "Load more", keyboard and phone width.

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
