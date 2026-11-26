"""The view page's bar, menus and history panel in a real browser.

Needs Playwright and a Chrome or Chromium it can launch. Without them the tests are
skipped, except when `CI` is set: there they fail, so a broken setup cannot pass unnoticed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from support import Client, in_ci, unavailable

try:
    from playwright import sync_api
except ImportError:
    if in_ci():
        raise
    pytest.skip("Playwright is not installed", allow_module_level=True)

PANEL_KEY = "pergam-history-panel"
WIDE = {"width": 1280, "height": 760}
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
def pergam(client: Client) -> str:
    """URL of v1 of a pergam with three versions and notes."""
    pid = client.publish(title="Plan", type="plan", html="<h1>one</h1>", note="first", tags=["q3"])["id"]
    client.publish(id=pid, title="Plan", type="plan", html="<h1>two</h1>", note="second")
    client.publish(id=pid, title="Plan", type="plan", html="<h1>three</h1>", note="third")
    return f"http://127.0.0.1:{client.port}/{pid}/v1/view"


@pytest.fixture
def open_page(browser) -> Iterator:
    contexts = []

    def open_page(url: str, scheme: str = "light", **options):
        context = browser.new_context(color_scheme=scheme, **options)
        contexts.append(context)
        page = context.new_page()
        page.goto(url)
        return page

    yield open_page
    for context in contexts:
        context.close()


def frame_width(page) -> float:
    return page.evaluate("document.querySelector('iframe.doc').getBoundingClientRect().width")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_panel_starts_open_on_a_wide_screen_and_its_state_is_remembered(open_page, pergam: str, scheme: str) -> None:
    page = open_page(pergam, scheme, viewport=WIDE)
    toggle = page.get_by_role("button", name="History", exact=True)

    assert page.locator("#panel").is_visible()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert frame_width(page) < WIDE["width"] - 200

    toggle.click()

    assert page.locator("#panel").is_hidden()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert frame_width(page) == WIDE["width"]
    page.reload()
    assert page.locator("#panel").is_hidden()
    assert frame_width(page) == WIDE["width"]
    assert page.evaluate(f"localStorage.getItem('{PANEL_KEY}')") == "closed"
    page.get_by_role("button", name="History", exact=True).click()
    page.reload()
    assert page.locator("#panel").is_visible()


def test_remembered_closed_panel_is_closed_before_first_paint(open_page, pergam: str) -> None:
    page = open_page(pergam, viewport=WIDE)
    page.get_by_role("button", name="History", exact=True).click()
    page.add_init_script(
        """
        window.atBody = null;
        new MutationObserver((records, observer) => {
          if (!document.body) return;
          window.atBody = document.documentElement.dataset.panel;
          observer.disconnect();
        }).observe(document, { childList: true, subtree: true });
        """
    )

    page.reload()

    assert page.evaluate("window.atBody") == "closed"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_on_a_phone_the_panel_opens_over_the_document_and_closes_with_escape(open_page, pergam: str, scheme: str) -> None:
    page = open_page(pergam, scheme, viewport=PHONE)
    toggle = page.get_by_role("button", name="History", exact=True)

    assert page.locator("#panel").is_hidden()
    assert frame_width(page) == PHONE["width"]
    toggle.click()

    panel = page.locator("#panel")
    assert panel.is_visible()
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"]
    assert page.evaluate("document.activeElement.id") == "panel-title"
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    page.keyboard.press("Escape")
    assert panel.is_hidden()
    assert page.evaluate("document.activeElement.dataset.panelToggle") == ""
    toggle.click()
    page.get_by_role("button", name="Close history").click()
    assert panel.is_hidden()


def test_a_phone_does_not_reopen_a_panel_remembered_open_on_a_wide_screen(open_page, pergam: str) -> None:
    page = open_page(pergam, viewport=PHONE)
    page.evaluate(f"localStorage.setItem('{PANEL_KEY}', 'open')")

    page.reload()

    assert page.locator("#panel").is_hidden()


def test_without_script_the_panel_shows_and_no_dead_control_does(open_page, pergam: str) -> None:
    wide = open_page(pergam, viewport=WIDE, java_script_enabled=False)
    phone = open_page(pergam, viewport=PHONE, java_script_enabled=False)

    for page in (wide, phone):
        assert page.locator("#panel").is_visible()
        assert page.get_by_role("link", name="v2", exact=False).first.is_visible()
        assert page.locator("[data-panel-toggle], [data-panel-close], .bar__print").evaluate_all(
            "nodes => nodes.every(node => node.hidden)"
        )
        assert page.evaluate("document.documentElement.scrollWidth") <= page.viewport_size["width"]


def test_menu_and_version_selector_open_with_the_keyboard_and_close_with_escape(open_page, pergam: str) -> None:
    page = open_page(pergam, viewport=WIDE)
    page.locator("summary[aria-label='More actions']").focus()
    page.keyboard.press("Enter")
    assert page.get_by_role("link", name="Raw HTML").is_visible()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.textContent") == "Raw HTML"
    page.keyboard.press("Escape")
    assert page.get_by_role("link", name="Raw HTML").is_hidden()
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "More actions"

    page.locator("summary.ver").click()
    assert page.locator(".versions__list").is_visible()
    page.locator("h1").click()
    assert page.locator(".versions__list").is_hidden()


@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_every_control_in_the_bar_and_panel_is_reachable_and_named(open_page, pergam: str, viewport) -> None:
    page = open_page(pergam, viewport=viewport)
    if viewport is PHONE:
        page.get_by_role("button", name="History", exact=True).click()

    reached = []
    for _ in range(40):
        page.keyboard.press("Tab")
        reached.append(page.evaluate(
            """() => {
                const node = document.activeElement;
                const style = getComputedStyle(node);
                return {
                    tag: node.tagName,
                    name: (node.getAttribute('aria-label') || node.textContent || '').trim(),
                    outline: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0,
                };
            }"""
        ))

    controls = [item for item in reached if item["tag"] in ("A", "BUTTON", "SUMMARY")]
    names = {item["name"] for item in controls}
    assert {"Index", "q3", "v1 of 3", "Text", "More actions", "History"} <= names
    assert any(name.startswith("v3") for name in names)
    assert any(name.startswith("Theme:") for name in names)
    assert any(name.startswith("Compare v1 with current") for name in names)
    assert all(item["name"] for item in controls)
    assert all(item["outline"] for item in controls)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_outdated_notice_and_panel_text_meet_aa(open_page, pergam: str, scheme: str, viewport) -> None:
    page = open_page(pergam, scheme, viewport=viewport)
    if viewport is PHONE:
        page.get_by_role("button", name="History", exact=True).click()

    ratios = page.evaluate(CONTRAST_OF, ".outdated span, .outdated a, .history__n, .history__when, .history__note, "
                                        ".history__compare, .facts dt, .facts dd, .panel__title, .crumbs a, .bar__title")

    assert ratios and min(ratio for _, ratio in ratios) >= 4.5, ratios


CONTRAST_OF = """
(selector) => {
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
  const backdrop = (node) => {
    const layers = [];
    for (let at = node; at; at = at.parentElement) {
      const colour = rgba(getComputedStyle(at).backgroundColor);
      if (colour[3] > 0) layers.push(colour);
      if (colour[3] === 255) break;
    }
    return layers.reverse().reduce((under, layer) => over(layer, under), [255, 255, 255]);
  };
  return Array.from(document.querySelectorAll(selector)).filter((node) => node.offsetParent).map((node) => {
    const text = rgba(getComputedStyle(node).color).slice(0, 3);
    const [high, low] = [luminance(text), luminance(backdrop(node))].sort((a, b) => b - a);
    return [node.className || node.tagName, (high + 0.05) / (low + 0.05)];
  });
}
"""
