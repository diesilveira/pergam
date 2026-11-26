"""Themes, the toggle and the shared components in a real browser.

Needs Playwright and a Chrome or Chromium it can launch. Without them the tests are
skipped, except when `CI` is set: there they fail, so a broken setup cannot pass unnoticed.
"""

from __future__ import annotations

import colorsys
from collections.abc import Iterator

import pytest

from pergam.taxonomy import DEFAULT_TYPES, type_rgb
from support import Client, in_ci, unavailable

try:
    from playwright import sync_api
except ImportError:
    if in_ci():
        raise
    pytest.skip("Playwright is not installed", allow_module_level=True)

BACKGROUND = {"light": "rgb(246, 246, 243)", "dark": "rgb(13, 15, 19)"}
OTHER = {"light": "dark", "dark": "light"}
THEME_KEY = "pergam-theme"


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
    """Absolute URLs of one page of each kind, over a pergam with two versions."""
    pid = client.publish(title="Plan", type="plan", html="<h1>one</h1>")["id"]
    client.publish(id=pid, title="Plan", type="plan", html="<h1>two</h1>", note="second")
    base = f"http://127.0.0.1:{client.port}"
    return {
        "index": f"{base}/",
        "view": f"{base}/{pid}/v1/view",
        "diff": f"{base}/{pid}/diff/v1..v2",
        "base": base,
    }


def page_in(browser, scheme: str, **options):
    context = browser.new_context(color_scheme=scheme, **options)
    return context, context.new_page()


def page_background(page) -> str:
    return page.evaluate("getComputedStyle(document.body).backgroundColor")


PAGES = ["index", "view", "diff"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", PAGES)
def test_pages_follow_the_system_scheme(browser, site, name: str, scheme: str) -> None:
    context, page = page_in(browser, scheme)

    page.goto(site[name])

    assert page_background(page) == BACKGROUND[scheme]
    assert page.evaluate("document.documentElement.dataset.theme") is None
    context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_toggle_switches_theme_and_the_choice_survives_reload_and_other_pages(browser, site, scheme: str) -> None:
    context, page = page_in(browser, scheme)
    page.goto(site["index"])
    toggle = page.locator("[data-theme-toggle]")

    assert toggle.get_attribute("aria-label") == f"Switch to {OTHER[scheme]} theme"
    toggle.click()

    assert page_background(page) == BACKGROUND[OTHER[scheme]]
    assert toggle.get_attribute("aria-label") == f"Switch to {scheme} theme"
    page.reload()
    assert page_background(page) == BACKGROUND[OTHER[scheme]]
    for name in ("view", "diff"):
        page.goto(site[name])
        assert page_background(page) == BACKGROUND[OTHER[scheme]]
    page.locator("[data-theme-toggle]").click()
    page.goto(site["index"])
    assert page_background(page) == BACKGROUND[scheme]
    context.close()


def test_remembered_choice_is_on_the_page_before_it_is_painted(browser, site) -> None:
    context, page = page_in(browser, "light")
    page.goto(site["index"])
    page.locator("[data-theme-toggle]").click()
    page.add_init_script(
        """
        window.atBody = null;
        new MutationObserver((records, observer) => {
          if (!document.body) return;
          window.atBody = {
            theme: document.documentElement.dataset.theme,
            background: getComputedStyle(document.documentElement).backgroundColor,
          };
          observer.disconnect();
        }).observe(document, { childList: true, subtree: true });
        """
    )

    page.goto(site["view"])

    assert page.evaluate("window.atBody") == {"theme": "dark", "background": BACKGROUND["dark"]}
    context.close()


def test_unusable_stored_value_is_ignored(browser, site) -> None:
    context, page = page_in(browser, "dark")
    page.add_init_script(f"localStorage.setItem('{THEME_KEY}', 'purple')")

    page.goto(site["index"])

    assert page.evaluate("document.documentElement.dataset.theme") is None
    assert page_background(page) == BACKGROUND["dark"]
    context.close()


def test_toggle_works_from_the_keyboard_with_a_visible_focus(browser, site) -> None:
    context, page = page_in(browser, "light")
    page.goto(site["index"])

    for _ in range(20):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.hasAttribute('data-theme-toggle')"):
            break
    else:
        pytest.fail("the theme toggle is not reachable with Tab")

    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
    page.keyboard.press("Enter")
    assert page_background(page) == BACKGROUND["dark"]
    context.close()


def test_without_script_the_system_scheme_applies_and_no_dead_toggle_shows(browser, site) -> None:
    context, page = page_in(browser, "dark", java_script_enabled=False)

    page.goto(site["index"])

    assert page_background(page) == BACKGROUND["dark"]
    assert page.locator("[data-theme-toggle]").is_hidden()
    context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", PAGES)
def test_pages_fit_a_phone_in_both_themes(browser, site, name: str, scheme: str) -> None:
    context, page = page_in(browser, scheme, viewport={"width": 360, "height": 740})

    page.goto(site[name])

    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    context.close()


def every_hue() -> list[tuple[int, int, int]]:
    """What `type_rgb` yields for a name whose hash lands on each of the 360 hues."""
    out = []
    for degrees in range(360):
        red, green, blue = colorsys.hls_to_rgb(degrees / 360, 0.72, 0.8)
        out.append((round(red * 255), round(green * 255), round(blue * 255)))
    return out


CONTRAST_PROBE = """
([colours, theme]) => {
  document.documentElement.dataset.theme = theme;
  const canvas = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const rgba = (css) => {
    canvas.clearRect(0, 0, 1, 1);
    canvas.fillStyle = '#000';
    canvas.fillStyle = css;
    canvas.fillRect(0, 0, 1, 1);
    return Array.from(canvas.getImageData(0, 0, 1, 1).data);
  };
  const luminance = ([r, g, b]) => {
    const [lr, lg, lb] = [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
  };
  const over = ([r, g, b, a], [br, bg, bb]) => [r * a / 255 + br * (1 - a / 255), g * a / 255 + bg * (1 - a / 255), b * a / 255 + bb * (1 - a / 255)];
  const card = document.createElement('div');
  card.className = 'card';
  document.body.append(card);
  const surface = rgba(getComputedStyle(card).backgroundColor);
  return colours.map((rgb) => {
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.style.setProperty('--type-rgb', rgb.join(','));
    badge.textContent = 'type';
    card.append(badge);
    const style = getComputedStyle(badge);
    const background = over(rgba(style.backgroundColor), surface);
    const text = rgba(style.color).slice(0, 3);
    const [high, low] = [luminance(text), luminance(background)].sort((a, b) => b - a);
    return { rgb, ratio: (high + 0.05) / (low + 0.05) };
  });
}
"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_type_badges_are_legible_for_every_default_and_derived_colour(browser, site, theme: str) -> None:
    context, page = page_in(browser, theme)
    page.goto(site["index"])
    colours = [list(type_rgb(name)) for name in (*DEFAULT_TYPES, "runbook", "adr", "retro", "spec")]
    colours += [list(rgb) for rgb in every_hue()]

    results = page.evaluate(CONTRAST_PROBE, [colours, theme])

    worst = min(results, key=lambda result: result["ratio"])
    assert worst["ratio"] >= 4.5, worst
    context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_missing_pergam_shows_the_styled_page_with_status_404(browser, site, scheme: str) -> None:
    context, page = page_in(browser, scheme)

    response = page.goto(f"{site['base']}/abcdef12/view")

    assert response.status == 404
    assert page.locator("h1").inner_text() == "Not found"
    assert page.locator(".error__code").inner_text() == "404 · NOT FOUND"
    assert page_background(page) == BACKGROUND[scheme]
    page.locator("[data-theme-toggle]").click()
    assert page_background(page) == BACKGROUND[OTHER[scheme]]
    context.close()


def test_error_page_offers_the_index_and_a_search(browser, site) -> None:
    context, page = page_in(browser, "light")
    page.goto(f"{site['base']}/abcdef12/view")

    page.get_by_role("searchbox", name="Search pergams").fill("plan")
    page.keyboard.press("Enter")
    page.wait_for_url(f"{site['base']}/?q=plan")
    page.goto(f"{site['base']}/abcdef12/view")
    page.get_by_role("link", name="Go to the index").click()

    page.wait_for_url(f"{site['base']}/")
    context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_error_page_fits_a_phone(browser, site, scheme: str) -> None:
    context, page = page_in(browser, scheme, viewport={"width": 360, "height": 740})

    page.goto(f"{site['base']}/abcdef12/view")

    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    context.close()


def test_error_page_has_no_scripted_content_beyond_the_theme_script(browser, site) -> None:
    context, page = page_in(browser, "light")

    page.goto(f"{site['base']}/nope")

    assert page.evaluate("Array.from(document.scripts).map((s) => s.getAttribute('src'))") == ["/static/theme.js"]
    context.close()
