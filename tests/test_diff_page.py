"""The diff page's layout: section list, version pickers, summary in the bar, and the same layout when too large."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from pergam.diff import MAX_LINES
from support import Client, in_ci, unavailable

V1 = "<h1>Plan</h1><p>Intro stays.</p><h2>Costs</h2><p>We spend 10 dollars a month.</p><h2>Old</h2><p>Dropped.</p>"
V2 = V1.replace("10", "12") + "<h2>Risks</h2><p>A new risk.</p>"


def publish_three(client: Client) -> str:
    pid = client.publish(title="Plan", html=V1, note="first")["id"]
    client.publish(id=pid, title="Plan", html=V2, note="<b>second</b>")
    client.publish(id=pid, title="Plan", html=V2 + "<p>More.</p>", note="third")
    return pid


TOC_LINK = (
    r'<a class="(toc__link[^"]*)" href="#(sec-\d+)"><span class="toc__name">([^<]+)</span>'
    r'<span class="toc__count">(.*?)</span></a>'
)


def test_side_list_names_every_section_with_its_counts_and_links_to_it(client: Client) -> None:
    pid = publish_three(client)

    page, backwards = client.get(f"/{pid}/diff/v1..v2").text, client.get(f"/{pid}/diff/v2..v1").text

    links = re.findall(TOC_LINK, re.search(r'<ol class="toc__list">(.*?)</ol>', page).group(1))
    assert links == [
        ("toc__link toc__link--same", "sec-1", "Plan", "unchanged"),
        ("toc__link", "sec-2", "Costs", '<span class="plus">+1</span> <span class="minus">−1</span>'),
        ("toc__link toc__link--same", "sec-3", "Old", "unchanged"),
        ("toc__link", "sec-4", "Risks", '<span class="plus">+2</span> new'),
    ]
    for _, anchor, _, _ in links:
        assert f'id="{anchor}"' in page
    assert re.findall(TOC_LINK, backwards)[3] == ("toc__link", "sec-4", "Risks", '<span class="minus">−2</span> removed')


def test_pickers_link_every_other_pair_and_mark_the_current_one(client: Client) -> None:
    pid = publish_three(client)

    page = client.get(f"/{pid}/diff/v1..v2").text

    old = re.search(r'<details class="dropdown versions picker--old">(.*?)</details>', page).group(1)
    new = re.search(r'<details class="dropdown versions picker--new">(.*?)</details>', page).group(1)
    assert '<summary class="ver dropdown__toggle" aria-label="Older version: v1">v1</summary>' in old
    assert '<summary class="ver dropdown__toggle" aria-label="Newer version: v2">v2</summary>' in new
    assert re.findall(r'<a href="([^"]+)"( aria-current="page")?>', old) == [
        (f"/{pid}/diff/v3..v2", ""), (f"/{pid}/diff/v2..v2", ""), (f"/{pid}/diff/v1..v2", ' aria-current="page"'),
    ]
    assert re.findall(r'<a href="([^"]+)"( aria-current="page")?>', new) == [
        (f"/{pid}/diff/v1..v3", ""), (f"/{pid}/diff/v1..v2", ' aria-current="page"'), (f"/{pid}/diff/v1..v1", ""),
    ]
    assert "&lt;b&gt;second&lt;/b&gt;" in new


def test_bar_has_a_breadcrumb_the_summary_and_a_link_to_the_newer_version(client: Client) -> None:
    pid = publish_three(client)

    page = client.get(f"/{pid}/diff/v1..v2").text

    header = re.search(r'<header class="topbar">(.*?)</header>', page).group(1)
    assert '<nav aria-label="Breadcrumb"><a href="/">Index</a>' in header
    assert f'<a class="crumbs__doc" href="/{pid}/v2/view">Plan</a>' in header
    assert '<h1 class="bar__title">Changes</h1>' in header
    assert (
        '<span class="bar__summary"><span class="plus">+3</span> <span class="minus">−1</span> lines · '
        "2 of 4 sections changed</span>"
    ) in header
    assert f'<a class="btn btn--primary" href="/{pid}/v2/view">Open v2</a>' in header


def test_too_large_answer_keeps_the_layout(client: Client) -> None:
    pid = client.publish(html="<p>small</p>")["id"]
    client.publish(id=pid, html="<p>x</p>" * (MAX_LINES + 1))

    page = client.get(f"/{pid}/diff/v1..v2").text

    assert '<span class="bar__summary">Not compared.</span>' in page
    assert '<p class="toc__empty">Not compared, so no sections.</p>' in page
    assert 'picker--old' in page and 'picker--new' in page
    assert re.search(r'<main class="dmain">.*<section class="too-large" role="status">', page, re.S)


try:
    from playwright import sync_api
except ImportError:
    sync_api = None


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
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


@pytest.fixture
def page_at(browser) -> Iterator:
    contexts = []

    def page_at(url: str, scheme: str = "light", **options):
        context = browser.new_context(color_scheme=scheme, **options)
        contexts.append(context)
        page = context.new_page()
        page.goto(url)
        return page

    yield page_at
    for context in contexts:
        context.close()


def test_choosing_versions_navigates_to_the_matching_range(page_at, client: Client) -> None:
    pid = publish_three(client)
    base = f"http://127.0.0.1:{client.port}"
    page = page_at(f"{base}/{pid}/diff/v1..v2")

    page.get_by_label("Newer version: v2").click()
    page.locator(".picker--new").get_by_role("link", name=re.compile(r"^v3")).click()
    page.wait_for_url(f"{base}/{pid}/diff/v1..v3")
    page.get_by_label("Older version: v1").focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")

    page.wait_for_url(f"{base}/{pid}/diff/v2..v3")


def test_section_link_opens_a_collapsed_section(page_at, client: Client) -> None:
    pid = publish_three(client)
    page = page_at(f"http://127.0.0.1:{client.port}/{pid}/diff/v1..v2")
    plan = page.locator("#sec-1")

    assert plan.evaluate("node => node.open") is False
    page.locator(".toc").get_by_role("link", name=re.compile(r"^Plan")).click()

    page.wait_for_function("document.querySelector('#sec-1').open")
    assert page.url.endswith("#sec-1")


def test_without_script_pickers_and_section_links_still_work(page_at, client: Client) -> None:
    pid = publish_three(client)
    base = f"http://127.0.0.1:{client.port}"
    page = page_at(f"{base}/{pid}/diff/v1..v2", java_script_enabled=False)

    page.locator(".toc").get_by_role("link", name=re.compile(r"^Costs")).click()
    assert page.url.endswith("#sec-2")
    page.get_by_label("Older version: v1").click()
    page.locator(".picker--old").get_by_role("link", name=re.compile(r"^v2")).click()

    page.wait_for_url(f"{base}/{pid}/diff/v2..v2")


MARK_CONTRAST = """
() => {
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
  return Array.from(document.querySelectorAll('ins, del, .line--added .line__text, .line--removed .line__text, .toc__link, .toc__count, .bar__summary')).map((node) => {
    const [high, low] = [luminance(rgba(getComputedStyle(node).color)), luminance(backdrop(node))].sort((a, b) => b - a);
    return [node.tagName + '.' + node.className, (high + 0.05) / (low + 0.05)];
  });
}
"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_word_marks_and_side_list_stay_readable(page_at, client: Client, scheme: str) -> None:
    pid = publish_three(client)

    page = page_at(f"http://127.0.0.1:{client.port}/{pid}/diff/v1..v2", scheme)

    ratios = page.evaluate(MARK_CONTRAST)
    assert {name.split(".")[0] for name, _ in ratios} >= {"INS", "DEL", "A", "SPAN"}
    assert min(ratio for _, ratio in ratios) >= 4.5, ratios


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("too_large", [False, True], ids=["diff", "too-large"])
def test_diff_page_fits_a_phone(page_at, client: Client, scheme: str, too_large: bool) -> None:
    pid = publish_three(client)
    if too_large:
        client.publish(id=pid, title="Plan", html="<p>x</p>" * (MAX_LINES + 1))
    page = page_at(
        f"http://127.0.0.1:{client.port}/{pid}/diff/v1..v{4 if too_large else 2}", scheme,
        viewport={"width": 360, "height": 740},
    )

    page.get_by_label("Newer version").click()

    assert page.locator(".picker--new .dropdown__menu").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
