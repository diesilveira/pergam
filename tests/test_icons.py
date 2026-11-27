"""Icons are inline SVG from `pergam.icons`, never text glyphs: the top bar's theme toggle and search included."""

from __future__ import annotations

import re

import pytest

from pergam import icons
from support import Client

GLYPHS = re.compile("[←-⯿☀-➿\U0001f300-\U0001faff]")


def test_every_icon_is_hidden_svg_drawn_in_the_text_colour() -> None:
    for icon in (icons.MORE, icons.CLOSE, icons.PIN, icons.SECTIONS, icons.SEARCH,
                 icons.THEME_SYSTEM, icons.THEME_LIGHT, icons.THEME_DARK):
        assert icon.startswith('<svg class="icon')
        assert 'aria-hidden="true" focusable="false"' in icon
        assert 'stroke="currentColor"' in icon
        assert not GLYPHS.search(icon)


@pytest.mark.parametrize("path", ["/", "/{id}/view", "/{id}/diff/v1..v2", "/nope"])
def test_the_top_bar_draws_its_theme_and_search_icons(client: Client, path: str) -> None:
    pid = client.publish(html="<p>one</p>")["id"]
    client.publish(id=pid, html="<p>two</p>")

    page = client.request("GET", path.format(id=pid), headers={"Accept": "text/html"}).text
    header = re.search(r'<header class="topbar">(.*?)</header>', page, re.S).group(1)
    toggle = re.search(r"<button[^>]*data-theme-toggle[^>]*>(.*?)</button>", header).group(1)

    assert toggle == icons.THEME_SYSTEM + icons.THEME_LIGHT + icons.THEME_DARK
    search = re.search(r'<label class="topbar__search-icon"[^>]*>(.*?)</label>', header).group(1)
    assert search == icons.SEARCH


try:
    from playwright import sync_api
except ImportError:
    sync_api = None


@pytest.fixture(scope="module")
def browser():
    from support import in_ci, unavailable

    if sync_api is None:
        if in_ci():
            pytest.fail("Playwright is not installed", pytrace=False)
        pytest.skip("Playwright is not installed")
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


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_theme_toggle_shows_the_icon_of_the_current_mode(browser, client: Client, scheme: str) -> None:
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    try:
        page.goto(f"http://127.0.0.1:{client.port}/")
        toggle = page.locator("[data-theme-toggle]")
        shown = []
        for _ in range(3):
            mode = toggle.get_attribute("data-theme-mode")
            visible = toggle.locator("svg").evaluate_all(
                "icons => icons.filter((icon) => getComputedStyle(icon).display !== 'none').map((icon) => icon.getAttribute('class'))"
            )
            shown.append((mode, visible))
            toggle.click()
    finally:
        context.close()

    assert shown == [("system", ["icon icon--theme-system"]), ("light", ["icon icon--theme-light"]),
                     ("dark", ["icon icon--theme-dark"])]
