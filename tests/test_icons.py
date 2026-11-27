"""Icons are inline SVG from `pergam.icons`, never text glyphs: the top bar's theme toggle and search included."""

from __future__ import annotations

import re

import pytest

from pergam import icons
from support import Client

GLYPHS = re.compile("[\u2190-\u21ff\u2300-\u23ff\u2500-\u27bf\u2b00-\u2bff\U0001f300-\U0001faff]")
"""Arrows, technical and geometric shapes, dingbats and emoji: what an icon would be drawn with; not typography like − or ·."""


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


CHROME = (
    r'<header class="topbar">.*?</header>', r'<footer class="index-foot">.*?</footer>', r'<details class="dropdown sortmenu".*?</summary>',
    r'<section class="pins">.*?<ul class="pins__row"', r'<div class="dwrap">\s*<main class="dmain">\s*<p class="summary--phone">',
)


def chrome_text(page: str) -> str:
    """The text of a page's own controls (bar, footer, sort menu, pinned row head), without any pergam's content."""
    parts = [match.group(0) for pattern in CHROME for match in re.finditer(pattern, page, re.S)]
    return re.sub(r"<[^>]+>", " ", " ".join(parts))


@pytest.mark.parametrize("path", ["/", "/?state=all&sort=title", "/filters", "/{id}/view", "/{id}/diff/v1..v2", "/nope"])
def test_page_controls_write_no_glyphs(client: Client, path: str) -> None:
    pid = client.publish(title="Plan", html="<p>one</p>")["id"]
    client.publish(id=pid, title="Plan", html="<p>two</p>")
    patch = client.request("PATCH", f"/{pid}/meta", b'{"pinned":true}', {"Content-Type": "application/json"})
    assert patch.status == 200

    page = client.request("GET", path.format(id=pid), headers={"Accept": "text/html"}).text

    assert "<header" in page
    assert not GLYPHS.search(chrome_text(page)), GLYPHS.findall(chrome_text(page))


def test_filters_and_index_controls_draw_with_the_shared_icons(client: Client) -> None:
    pid = client.publish(tags=["q3"])["id"]
    assert client.request("PATCH", f"/{pid}/meta", b'{"pinned":true}', {"Content-Type": "application/json"}).status == 200

    index = client.get("/?tag=q3").text

    for icon in (icons.FILTER, icons.CHIP_REMOVE, icons.CHEVRON_DOWN, icons.CHEVRON_LEFT, icons.CHEVRON_RIGHT, icons.CLOSE,
                 icons.CHECK):
        assert icon in index
    assert "<svg" not in re.sub("|".join(re.escape(icon) for icon in vars(icons).values()
                                         if isinstance(icon, str) and icon.startswith("<svg")), "", index)


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
