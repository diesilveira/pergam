"""The filter dialog in a real browser: draft and live count, apply, every way to cancel, keyboard, phone, no script.

Needs Playwright and a Chrome or Chromium it can launch. Without them the tests are
skipped, except when `CI` is set: there they fail.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urlencode

import pytest

from support import Client, in_ci, listed_order, patch_meta, unavailable

try:
    from playwright import sync_api
except ImportError:
    if in_ci():
        raise
    pytest.skip("Playwright is not installed", allow_module_level=True)

SCHEMES = ["light", "dark"]
PHONE = {"width": 360, "height": 740}
DESKTOP = {"width": 1280, "height": 860}


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
    """Fourteen authors and twenty tags (so the find fields show), one archived and one pinned pergam."""
    ids = {}
    for n in range(14):
        ids[f"doc{n}"] = client.publish(
            title=f"Doc {n}",
            type="plan" if n % 2 else "reporte",
            author=f"person{n:02d}@example.com",
            tags=[f"tag{n:02d}", f"tag{n + 6:02d}", "shared"],
            html=f"<p>Body {n}.</p>",
        )["id"]
    ids["old"] = client.publish(title="Old", type="otro", author="person00@example.com", tags=["shared"])["id"]
    patch_meta(client, ids["old"], {"archived": True})
    patch_meta(client, ids["doc1"], {"pinned": True})
    return {**ids, "base": f"http://127.0.0.1:{client.port}"}


def open_page(browser, scheme: str = "light", **options):
    options.setdefault("reduced_motion", "reduce")
    context = browser.new_context(color_scheme=scheme, **options)
    return context, context.new_page()


def open_dialog(page) -> None:
    page.locator(".filters-btn").click()
    page.wait_for_function("document.querySelector('[data-filters-dialog]').open")


def apply_label(page) -> str:
    return page.locator("[data-filters-apply]").inner_text()


def wait_for_label(page, text: str) -> None:
    page.wait_for_function(f"document.querySelector('[data-filters-apply]').textContent === {text!r}")


def is_open(page) -> bool:
    return page.evaluate("document.querySelector('[data-filters-dialog]').open")


@pytest.mark.parametrize("scheme", SCHEMES)
def test_choices_are_a_draft_with_a_live_count_and_apply_navigates_once(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme)
    page.goto(site["base"] + "/")
    history = page.evaluate("history.length")
    open_dialog(page)

    page.locator(".pill", has_text="plan").first.click()
    wait_for_label(page, "Show 7 pergams")
    page.locator(".pill", has_text="tag03").click()
    wait_for_label(page, "Show 1 pergam")

    assert page.url == site["base"] + "/"
    assert page.locator("[data-filters-live]").inner_text() == "1 pergam match"
    page.locator("[data-filters-apply]").click()
    page.wait_for_url(site["base"] + "/?type=plan&tag=tag03")
    assert listed_order(page.content()) == [site["doc3"]]
    assert page.evaluate("history.length") == history + 1
    page.wait_for_function("document.activeElement.hasAttribute('data-results')")
    assert page.locator("[data-results]").inner_text().startswith("1 of 14")
    assert page.get_by_role("link", name="Filters, 2 active").is_visible()
    context.close()


@pytest.mark.parametrize("how", ["escape", "close", "backdrop", "back"])
def test_every_way_of_cancelling_discards_the_draft_and_returns_focus(browser, site, how: str) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/?tag=shared")
    open_dialog(page)
    page.locator(".pill", has_text="plan").first.click()
    wait_for_label(page, "Show 7 pergams")

    if how == "escape":
        page.keyboard.press("Escape")
    elif how == "close":
        page.get_by_role("button", name="Close filters").click()
    elif how == "backdrop":
        page.mouse.click(10, 400)
    else:
        page.go_back()
    page.wait_for_function("!document.querySelector('[data-filters-dialog]').open")

    assert page.url == site["base"] + "/?tag=shared"
    assert page.evaluate("document.activeElement.classList.contains('filters-btn')")
    open_dialog(page)
    assert not page.locator("[data-filters-dialog] input[name=type][value=plan]").is_checked()
    assert apply_label(page) == "Show 14 pergams"
    context.close()


def test_values_without_results_are_dimmed_announced_and_cannot_be_ticked(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/?tag=tag03")
    open_dialog(page)
    informe = page.locator("[data-filters-dialog] input[name=type][value=informe]")

    assert informe.get_attribute("aria-disabled") == "true"
    assert page.get_by_role("checkbox", name="informe, 0 pergams, unavailable").count() == 1
    assert page.get_by_role("checkbox", name="tag03, 1 pergam").count() == 1
    page.locator(".pill", has_text="informe").click(force=True)
    assert not informe.is_checked()
    informe.focus()
    page.keyboard.press("Space")
    assert not informe.is_checked()
    context.close()


def test_a_draft_with_no_result_disables_apply_and_says_why(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/")
    open_dialog(page)

    page.locator(".pill", has_text="tag03").click()
    page.locator(".pill", has_text="tag05").click()
    page.locator(".seg__option", has_text="Archived").click()
    wait_for_label(page, "No matches")

    assert page.locator("[data-filters-apply]").is_disabled()
    assert page.locator("[data-filters-none]").inner_text() == "No pergams match. Try removing a filter."
    context.close()


def test_clearing_a_section_or_everything_keeps_the_search(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/?type=plan&tag=shared&state=all&q=Doc")
    open_dialog(page)

    page.get_by_role("button", name="Clear tag filters").click()
    assert not page.locator("[data-filters-dialog] input[name=tag][value=shared]").is_checked()
    assert page.locator("[data-filters-dialog] input[name=type][value=plan]").is_checked()
    page.locator("[data-filters-reset]").click()
    wait_for_label(page, "Show 14 pergams")
    assert page.locator("[data-filters-dialog] input[name=state][value='']").is_checked()
    page.locator("[data-filters-apply]").click()
    page.wait_for_url(site["base"] + "/?q=Doc")
    context.close()


def test_long_lists_expand_in_place_and_find_fields_filter_them(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/")
    open_dialog(page)
    more = page.get_by_role("button", name="Show all 14 authors")

    assert page.locator("[data-section=author] [data-item]:visible").count() == 8
    more.click()
    assert page.locator("[data-section=author] [data-item]:visible").count() == 14
    assert page.get_by_role("button", name="Show fewer authors").get_attribute("aria-expanded") == "true"
    find = page.get_by_role("searchbox", name="Find a tag")
    find.fill("tag1")
    assert page.locator("[data-section=tag] [data-item]:visible").count() == 10
    find.fill("zzz")
    assert page.locator("[data-section=tag] [data-nomatch]").inner_text() == "No tags match “zzz”"
    page.keyboard.press("Escape")
    assert find.input_value() == "" and is_open(page)
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('[data-filters-dialog]').open")
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_whole_flow_works_from_the_keyboard(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme)
    page.goto(site["base"] + "/?type=reporte")
    page.locator(".filters-btn").focus()

    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('[data-filters-dialog]').open")
    assert page.evaluate("document.activeElement.value") == "reporte"
    assert page.evaluate("getComputedStyle(document.activeElement.closest('label')).outlineStyle") == "solid"
    page.keyboard.press("Space")
    page.keyboard.press("Tab")
    page.keyboard.press("Space")
    wait_for_label(page, "Show 7 pergams")
    page.locator("[data-filters-dialog] input[name=state][value=all]").focus()
    page.keyboard.press("ArrowLeft")
    assert page.locator("[data-filters-dialog] input[name=state][value=archived]").is_checked()
    page.keyboard.press("ArrowRight")
    page.locator("[data-filters-apply]").focus()
    page.keyboard.press("Enter")
    page.wait_for_url(site["base"] + "/?type=plan&state=all")
    page.locator(".filters-btn").focus()
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('[data-filters-dialog]').open")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('[data-filters-dialog]').open")
    assert page.evaluate("document.activeElement.classList.contains('filters-btn')")
    context.close()


def test_chips_remove_a_filter_without_opening_the_dialog(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/?type=plan&tag=shared&state=all")

    page.get_by_role("link", name="Remove filter Tag: shared").click()
    page.wait_for_url(site["base"] + "/?type=plan&state=all")
    page.get_by_role("link", name="Remove filter Active and archived").click()
    page.wait_for_url(site["base"] + "/?type=plan")
    page.get_by_role("link", name="Clear all filters").click()
    page.wait_for_url(site["base"] + "/")
    assert not is_open(page)
    context.close()


def test_edit_filters_and_more_chips_open_the_dialog(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/?tag=tag01&tag=tag02&tag=tag03&tag=tag04&tag=tag05&tag=tag06&state=archived")

    page.get_by_role("link", name="Edit filters").click()
    page.wait_for_function("document.querySelector('[data-filters-dialog]').open")
    page.keyboard.press("Escape")
    page.get_by_role("link", name="Show all 7 active filters").click()
    page.wait_for_function("document.querySelector('[data-filters-dialog]').open")
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_on_a_phone_the_dialog_fills_the_screen(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme, viewport=PHONE)
    page.goto(site["base"] + "/?type=plan&tag=shared&author=person01@example.com")

    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    open_dialog(page)
    box = page.locator("[data-filters-dialog]").bounding_box()

    assert (round(box["width"]), round(box["height"])) == (PHONE["width"], PHONE["height"])
    assert page.locator(".fform__title--narrow").inner_text() == "Filters"
    assert page.locator("[data-filters-apply]").bounding_box()["height"] >= 44
    assert page.locator(".pill").first.bounding_box()["height"] >= 40
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_on_a_desktop_the_dialog_is_centred_and_only_its_body_scrolls(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme, viewport=DESKTOP)
    page.goto(site["base"] + "/")
    open_dialog(page)
    box = page.locator("[data-filters-dialog]").bounding_box()

    assert round(box["width"]) == 560
    assert abs(box["x"] + box["width"] / 2 - DESKTOP["width"] / 2) <= 1
    assert page.evaluate("getComputedStyle(document.querySelector('.fform__body')).overflowY") == "auto"
    assert page.evaluate("getComputedStyle(document.querySelector('.fdialog'), '::backdrop').backgroundColor") != "rgba(0, 0, 0, 0)"
    context.close()


def test_without_script_the_button_leads_to_the_filters_page(browser, site) -> None:
    context, page = open_page(browser, java_script_enabled=False, viewport=PHONE)
    page.goto(site["base"] + "/?tag=shared&sort=title")

    page.locator(".filters-btn").click()
    page.wait_for_url(site["base"] + "/filters?tag=shared&sort=title")
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    page.locator(".pill", has_text="plan").first.click()
    page.get_by_role("button", name="Show results").click()
    page.wait_for_url(re.compile(r"/\?type=plan&tag=shared&state=&sort=title$"))
    assert set(listed_order(page.content())) == {site[f"doc{n}"] for n in range(1, 14, 2)}
    page.goto(site["base"] + "/filters?tag=shared")
    page.get_by_role("link", name="Cancel").click()
    page.wait_for_url(site["base"] + "/?tag=shared")
    context.close()


def test_the_pinned_row_keeps_the_pinned_pergams_that_match(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/?type=plan")

    assert page.locator(".pin__title").all_inner_texts() == ["Doc 1"]
    page.goto(site["base"] + "/?type=reporte")
    assert page.locator(".pin__title").count() == 0
    context.close()


def test_the_dialog_form_is_fetched_once_on_first_open(browser, site) -> None:
    context, page = open_page(browser)
    fetched: list[str] = []
    page.on("request", lambda request: fetched.append(request.url) if "/filters/form" in request.url else None)
    page.goto(site["base"] + "/?tag=shared")

    assert page.locator("[data-filters-dialog] form").count() == 0
    open_dialog(page)
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('[data-filters-dialog]').open")
    open_dialog(page)

    assert fetched == [site["base"] + "/filters/form?tag=shared"]
    context.close()


def test_when_the_form_cannot_be_fetched_the_button_leads_to_the_filters_page(browser, site) -> None:
    context, page = open_page(browser)
    page.route("**/filters/form*", lambda route: route.abort())
    page.goto(site["base"] + "/?tag=shared")

    page.locator(".filters-btn").click()

    page.wait_for_url(site["base"] + "/filters?tag=shared")
    assert page.locator("h1").inner_text() == "Filter pergams"
    context.close()


def test_enter_in_a_find_field_does_not_apply(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/")
    open_dialog(page)
    page.locator(".pill", has_text="plan").first.click()

    page.get_by_role("searchbox", name="Find a tag").fill("tag0")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)

    assert page.url == site["base"] + "/" and is_open(page)
    context.close()


def test_applying_gives_the_same_url_whatever_the_order_values_were_ticked(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/")
    open_dialog(page)

    page.locator(".pill", has_text="tag07").click()
    page.locator(".pill", has_text="tag03").click()
    page.locator(".pill", has_text="reporte").click()
    page.locator(".pill", has_text="plan").first.click()
    wait_for_label(page, "Show 3 pergams")
    page.locator("[data-filters-apply]").click()

    page.wait_for_url(site["base"] + "/?type=plan&type=reporte&tag=tag03&tag=tag07")
    context.close()


def test_reloading_with_the_dialog_open_opens_it_again_and_back_still_closes_it(browser, site) -> None:
    context, page = open_page(browser)
    page.goto(site["base"] + "/?tag=shared")
    open_dialog(page)
    history = page.evaluate("history.length")

    page.reload()
    page.wait_for_function("document.querySelector('[data-filters-dialog]').open")
    page.go_back()
    page.wait_for_function("!document.querySelector('[data-filters-dialog]').open")

    assert page.url == site["base"] + "/?tag=shared"
    assert page.evaluate("history.length") == history
    context.close()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_on_a_phone_rows_keep_their_type_and_dialog_targets_are_large(browser, site, scheme: str) -> None:
    context, page = open_page(browser, scheme, viewport=PHONE)
    page.goto(site["base"] + "/?tag=shared")

    assert page.locator(".list .row .badge").first.is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    open_dialog(page)
    clear = page.get_by_role("button", name="Clear tag filters").bounding_box()
    more = page.get_by_role("button", name="Show all 14 authors").bounding_box()

    assert clear["height"] >= 44 and clear["width"] >= 44
    assert more["height"] >= 44
    context.close()


def test_without_script_a_value_with_no_result_can_still_be_ticked(browser, site) -> None:
    context, page = open_page(browser, java_script_enabled=False)
    page.goto(site["base"] + "/filters?tag=tag03")

    page.locator(".pill", has_text="informe").click()

    assert page.locator("input[name=type][value=informe]").is_checked()
    context.close()


def test_tags_outside_the_basic_plane_sort_like_the_server(browser, client: Client) -> None:
    """JavaScript compares UTF-16 code units, Python code points; the dialog must give the server's URL."""
    astral, wide_z = "\U0001d538x", "ｚx"
    client.publish(title="A", tags=[astral])
    client.publish(title="B", tags=[wide_z])
    base = f"http://127.0.0.1:{client.port}"
    context, page = open_page(browser)
    page.goto(base + "/")
    open_dialog(page)

    page.locator(".pill", has_text=astral).click()
    page.locator(".pill", has_text=wide_z).click()
    wait_for_label(page, "Show 2 pergams")
    page.locator("[data-filters-apply]").click()
    page.wait_for_url(re.compile(r"\?tag="))

    server = client.get("/?" + urlencode([("tag", astral), ("tag", wide_z)])).text
    expected = re.search(r'href="/filters\?([^"]+)"', server).group(1).replace("&amp;", "&")
    assert page.url == f"{base}/?{expected}"
    assert expected.index("%EF%BD%9Ax") < expected.index("%F0%9D%94%B8x")
    context.close()

