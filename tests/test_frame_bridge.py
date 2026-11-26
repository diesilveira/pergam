"""The view page and the frame bridge in a real browser, with and without the sandbox planned in #28.

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

SANDBOX = "allow-scripts allow-top-navigation-by-user-activation allow-modals allow-popups"
DOCUMENT = """<!doctype html><html><head><title>Inner  title</title></head><body>
<a id="to-c" href="#sec-c">to c</a> <a id="to-index" href="/">index</a> <a id="blank" href="/" target="_blank">new tab</a>
<div style="height:3000px"></div><h2 id="sec-b">B</h2>
<div style="height:3000px"></div><h2 id="sec-c">C</h2>
<div style="height:3000px"></div>
</body></html>"""


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


@pytest.fixture(params=[False, True], ids=["plain", "sandboxed"])
def view(request: pytest.FixtureRequest, browser, client: Client) -> Iterator[tuple[object, str, str]]:
    """A page opened on nothing yet, the base URL, and the id of a published two-version pergam."""
    base = f"http://127.0.0.1:{client.port}"
    pid = client.publish(title="Bridge", html=DOCUMENT)["id"]
    context = browser.new_context()
    page = context.new_page()
    if request.param:

        def sandboxed(route) -> None:
            response = route.fetch()
            body = response.text().replace('<iframe class="doc"', f'<iframe class="doc" sandbox="{SANDBOX}"', 1)
            route.fulfill(response=response, body=body)

        page.route("**/view*", sandboxed)
    yield page, base, pid
    context.close()


def open_view(page, url: str):
    page.goto(url)
    page.wait_for_selector(".bar__print:not([hidden])")
    page.wait_for_load_state("load")
    page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
    return page.frame(url=lambda frame_url: "/content" in frame_url)


def test_outer_fragment_scrolls_the_document_without_adding_history(view) -> None:
    page, base, pid = view
    unscrolled = page.context.new_page()
    open_view(unscrolled, f"{base}/{pid}/view")

    frame = open_view(page, f"{base}/{pid}/view#sec-b")

    frame.wait_for_function("scrollY > 2500")
    assert page.evaluate("history.length") == unscrolled.evaluate("history.length")
    assert page.evaluate("location.hash") == "#sec-b"


def test_anchor_clicked_in_the_document_updates_the_outer_url(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/view")

    frame.click("#to-c")

    page.wait_for_function("location.hash === '#sec-c'")
    assert page.evaluate("location.pathname") == f"/{pid}/view"
    assert frame.evaluate("scrollY") > 5500


def test_outer_hash_change_moves_the_document(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/view")

    page.evaluate("location.hash = '#sec-c'")

    frame.wait_for_function("scrollY > 5500")


def test_document_title_names_the_tab(view) -> None:
    page, base, pid = view

    open_view(page, f"{base}/{pid}/v1/view")

    page.wait_for_function("document.title === 'Inner title · v1 · pergam'")


def test_links_leaving_the_document_navigate_the_window(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/view")

    frame.click("#to-index")

    page.wait_for_url(f"{base}/")
    assert len(page.frames) == 1


def test_links_with_their_own_target_are_left_alone(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/view")

    with page.context.expect_page() as opened:
        frame.click("#blank")

    assert opened.value.url == f"{base}/"
    assert page.url == f"{base}/{pid}/view"


def test_print_control_prints_the_framed_document(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/view")
    frame.evaluate("() => { window.print = () => { window.printed = (window.printed || 0) + 1; }; }")
    page.evaluate("() => { window.print = () => { window.printed = true; }; }")

    page.click(".bar__print")
    page.keyboard.press("Control+p")

    frame.wait_for_function("window.printed === 2")
    assert page.evaluate("window.printed") is None


def test_print_control_stays_hidden_without_a_bridge(browser, client: Client) -> None:
    pid = client.publish(html="<p>x</p>")["id"]
    page = browser.new_page()
    page.route("**/static/frame-bridge.js", lambda route: route.fulfill(status=404, body=""))
    try:
        page.goto(f"http://127.0.0.1:{client.port}/{pid}/view")
        page.wait_for_load_state("load")

        assert page.is_hidden(".bar__print")
    finally:
        page.close()


def test_messages_are_untrusted_data(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/v1/view")
    page.wait_for_function("document.title.startsWith('Inner title')")

    page.evaluate("postMessage({pergam: 1, type: 'hash', value: '#from-the-page-itself'}, '*')")
    frame.evaluate(
        """() => {
            const send = (type, value) => parent.postMessage({pergam: 1, type, value}, '*');
            send('hash', 'no-leading-hash');
            send('hash', '#' + 'x'.repeat(3000));
            send('hash', 42);
            send('title', ['array']);
            send('title', '<img src=x onerror=alert(1)>');
        }"""
    )

    page.wait_for_function("document.title.startsWith('<img')")
    assert page.evaluate("location.hash") == ""
    assert page.evaluate("document.title") == "<img src=x onerror=alert(1)> · v1 · pergam"
    assert page.evaluate("document.querySelectorAll('img').length") == 0


def test_document_sees_the_content_url_and_the_frame_as_its_viewport(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/v1/view")

    inside = frame.evaluate(
        "() => [location.pathname, new URL('notes', location.href).pathname, window.top !== window, innerHeight]"
    )

    assert inside[:3] == [f"/{pid}/v1/content", f"/{pid}/v1/notes", True]
    assert 0 < inside[3] < page.evaluate("innerHeight")


def test_imported_pergam_shows_its_origin_next_to_a_working_bridge(view, client: Client) -> None:
    page, base, _ = view
    version = {"version": 1, "title": "Moved", "html": DOCUMENT, "type": "otro", "author": "you@example.com"}
    doc = {
        "format_version": 1,
        "id": "0123abcd",
        "server": "https://pergam.example.com",
        "versions": [{**version, "created_at": "2025-01-01T00:00:00+00:00"}],
    }
    assert client.post_json(doc, "/import").status == 201

    frame = open_view(page, f"{base}/0123abcd/view")

    origin = page.locator(".bar__origin a")
    assert origin.is_visible()
    assert origin.inner_text() == "pergam.example.com/0123abcd"
    assert origin.get_attribute("href") == "https://pergam.example.com/0123abcd/view"
    page.wait_for_function("document.title === 'Inner title · v1 · pergam'")
    frame.click("#to-c")
    page.wait_for_function("location.hash === '#sec-c'")
