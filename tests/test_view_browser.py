"""The view page's bar, menus and history panel in a real browser.

Needs Playwright and a Chrome or Chromium it can launch. Without them the tests are
skipped, except when `CI` is set: there they fail, so a broken setup cannot pass unnoticed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from pergam.diff import MAX_LINES
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
def test_a_first_visit_shows_the_document_at_full_width_and_the_panel_state_is_remembered(
    open_page, pergam: str, scheme: str
) -> None:
    page = open_page(pergam, scheme, viewport=WIDE)
    toggle = page.get_by_role("button", name="History", exact=True)

    assert page.locator("#panel").is_hidden()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert frame_width(page) == WIDE["width"]

    toggle.click()

    assert page.locator("#panel").is_visible()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert frame_width(page) < WIDE["width"] - 200
    page.reload()
    assert page.locator("#panel").is_visible()
    assert page.evaluate(f"localStorage.getItem('{PANEL_KEY}')") == "open"
    page.get_by_role("button", name="History", exact=True).click()
    page.reload()
    assert page.locator("#panel").is_hidden()
    assert frame_width(page) == WIDE["width"]


def test_remembered_open_panel_is_open_before_first_paint(open_page, pergam: str) -> None:
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

    assert page.evaluate("window.atBody") == "open"


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


def test_without_script_the_panel_shows_below_the_document_and_no_dead_control_does(open_page, pergam: str) -> None:
    wide = open_page(pergam, viewport=WIDE, java_script_enabled=False)
    phone = open_page(pergam, viewport=PHONE, java_script_enabled=False)

    for page in (wide, phone):
        assert page.locator("#panel").is_visible()
        assert frame_width(page) == page.viewport_size["width"]
        assert page.locator("#panel").bounding_box()["y"] > page.locator("iframe.doc").bounding_box()["y"]
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
    assert page.evaluate("document.activeElement.textContent") == "Text"
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
    page.get_by_role("button", name="History", exact=True).click()
    if viewport is WIDE:
        page.locator("h1.bar__title").click()

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
    common = {"q3", "Version 1 of 3", "Pin", "More actions", "History"}
    assert (common | {"Index"} if viewport is WIDE else common) <= names
    assert any(name.startswith("v3") for name in names)
    assert any(name.startswith("Theme:") for name in names)
    assert any(name.startswith("Compare v1 with current") for name in names)
    assert all(item["name"] for item in controls)
    assert all(item["outline"] for item in controls)


@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_every_menu_entry_is_reachable_with_the_keyboard(open_page, client: Client, viewport) -> None:
    pid = client.publish(title="Plan", html="<h1>one</h1>")["id"]
    client.publish(id=pid, title="Plan", html="<h1>two</h1>")
    page = open_page(f"http://127.0.0.1:{client.port}/{pid}/view", viewport=viewport)
    page.wait_for_selector(".bar__print:not([hidden])", state="attached")

    page.locator("summary[aria-label='More actions']").focus()
    page.keyboard.press("Enter")
    reached = []
    for _ in range(8):
        page.keyboard.press("Tab")
        reached.append(page.evaluate("document.activeElement.textContent.trim()"))

    assert reached == ["Compare with v1", "Text", "Print", "Archive", "Edit tags", "Raw HTML", "Export (JSON)",
                       "Open document"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_outdated_notice_and_panel_text_meet_aa(open_page, pergam: str, scheme: str, viewport) -> None:
    page = open_page(pergam, scheme, viewport=viewport)
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


CURRENT_IN_PANEL = """() => {
  const panel = document.getElementById('panel').getBoundingClientRect();
  const item = document.querySelector('.history__link[aria-current]').closest('li').getBoundingClientRect();
  return item.top >= panel.top && item.bottom <= panel.bottom;
}"""


@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_the_version_shown_is_scrolled_into_view_in_a_long_history(open_page, client: Client, viewport) -> None:
    pid = client.publish(title="Long", html="<p>1</p>", note="first")["id"]
    for n in range(2, 41):
        client.publish(id=pid, title="Long", html=f"<p>{n}</p>", note=f"version {n}")
    page = open_page(f"http://127.0.0.1:{client.port}/{pid}/v3/view", viewport=viewport)
    page.get_by_role("button", name="History", exact=True).click()

    assert page.evaluate("document.getElementById('panel').scrollTop") > 0
    assert page.evaluate(CURRENT_IN_PANEL)


ONE_ROW = """() => {
  const bar = document.querySelector('.topbar');
  const shown = [...bar.children, ...bar.querySelector('.topbar__actions').children]
    .filter((node) => node.getClientRects().length && !node.classList.contains('topbar__actions'))
    .map((node) => node.getBoundingClientRect());
  const middles = shown.map((box) => box.top + box.height / 2);
  return { height: bar.getBoundingClientRect().height, spread: Math.max(...middles) - Math.min(...middles) };
}"""


LONG_TITLE = "A very long title for a quarterly planning document that keeps going well past any reasonable width"
LONG_TAGS = ["quarterly-planning", "budget-review", "operations", "infrastructure", "q3"]
WIDTHS = [360, 640, 768, 901, 1024, 1280, 1440]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("page_kind", ["view", "view-stranger", "diff", "too-large"])
def test_the_bar_keeps_one_row_at_every_width(
    open_page, client: Client, trusting: Client, scheme: str, width: int, page_kind: str
) -> None:
    owner = trusting.as_user("ana@example.com")
    pid = owner.publish(title=LONG_TITLE, tags=LONG_TAGS, html="<h1>a</h1>")["id"]
    owner.publish(id=pid, title=LONG_TITLE, html="<h1>b</h1>")
    if page_kind == "too-large":
        owner.publish(id=pid, title=LONG_TITLE, html="<p>x</p>" * (MAX_LINES + 1))
    server, headers = (trusting, {"X-Forwarded-User": "eve@example.com"}) if page_kind == "view-stranger" else (client, {})
    path = {"view": f"/{pid}/v2/view", "view-stranger": f"/{pid}/v1/view", "diff": f"/{pid}/diff/v1..v2",
            "too-large": f"/{pid}/diff/v1..v3"}[page_kind]

    page = open_page(
        f"http://127.0.0.1:{server.port}{path}", scheme, viewport={"width": width, "height": 700}, extra_http_headers=headers
    )

    bar = page.evaluate(ONE_ROW)
    assert bar["spread"] < 8, bar
    assert bar["height"] < 56, bar
    assert page.evaluate("document.documentElement.scrollWidth") <= width


@pytest.mark.parametrize("width", [360, 1280])
@pytest.mark.parametrize("kind", ["view", "diff"])
def test_the_search_is_an_icon_that_opens_over_the_bar(open_page, client: Client, width: int, kind: str) -> None:
    pid = client.publish(title=LONG_TITLE, html="<h1>a</h1>")["id"]
    client.publish(id=pid, title=LONG_TITLE, html="<h1>b</h1>")
    page = open_page(f"http://127.0.0.1:{client.port}/{pid}/{'view' if kind == 'view' else 'diff/v1..v2'}",
                     viewport={"width": width, "height": 700})
    field = page.get_by_role("searchbox", name="Search title and text")

    assert field.bounding_box()["width"] < 2
    page.locator("h1.bar__title").click()
    page.keyboard.press("/")
    box = field.bounding_box()
    assert box["width"] > 150
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    field.fill("plan")
    page.keyboard.press("Enter")
    page.wait_for_url(f"http://127.0.0.1:{client.port}/?q=plan")


def test_compare_with_current_shows_for_the_version_viewed_and_on_hover_or_focus(open_page, pergam: str) -> None:
    page = open_page(pergam, viewport=WIDE)
    page.get_by_role("button", name="History", exact=True).click()
    shown, other = (page.locator(f".history__item:has(a[href$='/v{n}/view']) .history__compare") for n in (1, 2))

    assert shown.bounding_box()["width"] > 50
    assert other.bounding_box()["width"] <= 1
    page.locator(".history__item:has(a[href$='/v2/view'])").hover()
    assert other.bounding_box()["width"] > 50
    page.locator("h1.bar__title").hover()
    assert other.bounding_box()["width"] <= 1
    page.locator(".history__item:has(a[href$='/v2/view']) .history__link").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "history__compare"
    assert other.bounding_box()["width"] > 50
    assert other.evaluate("node => getComputedStyle(node).outlineStyle") != "none"


@pytest.mark.parametrize("target", ["more", "versions"])
def test_a_click_in_the_document_closes_an_open_dropdown(open_page, pergam: str, target: str) -> None:
    page = open_page(pergam, viewport=WIDE)
    page.wait_for_selector(".bar__print:not([hidden])", state="attached")
    dropdown = page.locator(f"details.{target}")

    dropdown.locator("summary").click()
    assert dropdown.evaluate("node => node.open")
    page.frame(url=lambda url: "/content" in url).locator("body").click(position={"x": 20, "y": 400})

    page.wait_for_function(f"!document.querySelector('details.{target}').open")
