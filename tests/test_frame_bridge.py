"""The view page, its frame bridge and the sandbox of published documents, in a real browser.

Needs Playwright and a Chrome or Chromium it can launch. Without them the tests are
skipped, except when `CI` is set: there they fail, so a broken setup cannot pass unnoticed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import psycopg
import pytest

from pergam.static_files import FILES, MERMAID_CDN_URL, MERMAID_PATH
from support import Client, in_ci, unavailable

try:
    from playwright import sync_api
except ImportError:
    if in_ci():
        raise
    pytest.skip("Playwright is not installed", allow_module_level=True)

DOCUMENT = """<!doctype html><html><head><title>Inner  title</title></head><body>
<a id="to-c" href="#sec-c">to c</a> <a id="to-index" href="/">index</a> <a id="blank" href="/" target="_blank">new tab</a>
<div style="height:3000px"></div><h2 id="sec-b">B</h2>
<div style="height:3000px"></div><h2 id="sec-c">C</h2>
<div style="height:3000px"></div>
</body></html>"""

INTRUDER = """<!doctype html><title>Intruder</title><body><script>
window.attempts = {};
const note = (name) => [() => { attempts[name] = 'reached'; }, () => { attempts[name] = 'blocked'; }];
const post = JSON.stringify({title: 'owned', html: '<p>owned</p>', type: 'otro', author: 'intruder@example.com'});
const json = {'Content-Type': 'application/json'};
fetch('/api/pergams').then(...note('read'));
fetch('/pergam', {method: 'POST', headers: json, body: post}).then(...note('write'));
fetch('/pergam', {method: 'POST', mode: 'no-cors', headers: {'Content-Type': 'text/plain'}, body: post}).then(...note('blind-write'));
try { navigator.sendBeacon('/pergam', post); } catch (e) {}
attempts.storage = localStorage instanceof Storage ? 'reached' : 'stand-in';
try { document.cookie; attempts.cookie = 'reached'; } catch (e) { attempts.cookie = 'blocked'; }
attempts.origin = origin;
const form = document.createElement('form');
form.method = 'POST'; form.action = '/pergam'; form.enctype = 'text/plain';
form.innerHTML = '<input name="x" value="y">';
document.body.append(form);
form.submit();
</script></body>"""


STORAGE_USER = """<!doctype html><title>Storage</title><script>
const theme = localStorage.getItem('theme') || 'light';
localStorage.setItem('theme', 'dark');
localStorage.tab = 'costs';
sessionStorage.setItem('visits', String(Number(sessionStorage.getItem('visits') || 0) + 1));
window.seen = {
  theme, stored: localStorage.getItem('theme'), named: localStorage.getItem('tab'), property: localStorage.theme,
  length: localStorage.length, key: localStorage.key(0), keys: Object.keys(localStorage).sort(),
  visits: sessionStorage.visits, has: 'tab' in localStorage,
};
localStorage.removeItem('tab');
delete localStorage.theme;
window.seen.after = localStorage.length;
sessionStorage.clear();
window.seen.cleared = sessionStorage.length;
</script><body><p>ok</p></body>"""
VENDORED_MERMAID = FILES[MERMAID_PATH].body
DIAGRAM = """<!doctype html><title>Diagram</title><body>
<nav><a id="to-flow" href="#flow">flow</a></nav>
<div style="height:3000px"></div>
<h2 id="flow">Flow</h2>
<pre class="mermaid">
flowchart TB
  classDef step fill:#161b22,stroke:#58a6ff,color:#e6edf3
  A["Input"]:::step --> B{"decision"}:::step
  B -- "ok" --> C["Result"]
</pre>
<pre class="mermaid">
sequenceDiagram
  Alice->>Bob: hello
</pre>
<div style="height:3000px"></div>
<script src="{src}"></script>
<script>mermaid.initialize({startOnLoad: true, theme: 'dark', securityLevel: 'loose'});</script>
</body>"""


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
def view(browser, client: Client) -> Iterator[tuple[object, str, str]]:
    """A page opened on nothing yet, the base URL, and the id of a published pergam."""
    base = f"http://127.0.0.1:{client.port}"
    pid = client.publish(title="Bridge", html=DOCUMENT)["id"]
    context = browser.new_context()
    yield context.new_page(), base, pid
    context.close()


def open_view(page, url: str):
    page.goto(url)
    page.wait_for_selector(".bar__print:not([hidden])", state="attached")
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

    opened.value.wait_for_load_state()
    assert opened.value.url == f"{base}/"
    assert opened.value.evaluate("origin") == base
    assert page.url == f"{base}/{pid}/view"


def test_print_control_prints_the_framed_document(view) -> None:
    page, base, pid = view
    frame = open_view(page, f"{base}/{pid}/view")
    frame.evaluate("() => { window.print = () => { window.printed = (window.printed || 0) + 1; }; }")
    page.evaluate("() => { window.print = () => { window.printed = true; }; }")

    page.click("summary[aria-label='More actions']")
    page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
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

        assert page.locator(".bar__print").evaluate("button => button.hidden")
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
    page.click("[data-panel-toggle]")

    origin = page.locator(".bar__origin a")
    assert origin.is_visible()
    assert origin.inner_text() == "pergam.example.com/0123abcd"
    assert origin.get_attribute("href") == "https://pergam.example.com/0123abcd/view"
    page.wait_for_function("document.title === 'Inner title · v1 · pergam'")
    frame.click("#to-c")
    page.wait_for_function("location.hash === '#sec-c'")


def intruder_attempts(page, frame) -> dict[str, str]:
    frame.wait_for_function("['read', 'write', 'blind-write'].every(name => name in window.attempts)")
    page.wait_for_timeout(300)
    return frame.evaluate("window.attempts")


@pytest.mark.parametrize("opened", ["framed", "alone"])
def test_a_published_script_cannot_read_or_write_through_the_api(
    browser, client: Client, db: psycopg.Connection, opened: str
) -> None:
    base = f"http://127.0.0.1:{client.port}"
    pid = client.publish(title="Intruder", html=INTRUDER)["id"]
    page = browser.new_page()
    console: list[str] = []
    page.on("console", lambda message: console.append(message.text))
    try:
        if opened == "framed":
            page.goto(f"{base}/{pid}/view")
            frame = page.frame(url=lambda url: "/content" in url)
        else:
            page.goto(f"{base}/{pid}/v1/content")
            frame = page.main_frame

        attempts = intruder_attempts(page, frame)
    finally:
        page.close()

    assert attempts == {
        "read": "blocked",
        "write": "blocked",
        "blind-write": "blocked",
        "storage": "stand-in",
        "cookie": "blocked",
        "origin": "null",
    }
    assert any("form submission" in line and "allow-forms" in line for line in console)
    assert any("connect-src" in line for line in console)
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (1,)


def test_messages_from_another_origin_are_dropped(view, browser) -> None:
    page, base, pid = view
    foreign = "http://foreign.example.com/"
    forged = json.dumps({"pergam": 1, "type": "title", "value": "Forged"})
    page.route(foreign, lambda route: route.fulfill(
        content_type="text/html", body=f"<script>parent.postMessage({forged}, '*')</script>"
    ))
    open_view(page, f"{base}/{pid}/v1/view")
    page.wait_for_function("document.title === 'Inner title · v1 · pergam'")

    page.evaluate(f"() => {{ const f = document.querySelector('iframe.doc'); f.removeAttribute('sandbox'); f.src = '{foreign}'; }}")

    page.wait_for_function("document.querySelector('iframe.doc').contentWindow.length === 0")
    page.wait_for_timeout(300)
    assert page.evaluate("document.title") == "Inner title · v1 · pergam"


@pytest.mark.parametrize("src", [MERMAID_PATH, MERMAID_CDN_URL], ids=["vendored", "jsdelivr"])
def test_mermaid_diagrams_and_anchors_work_in_the_sandbox(view, src: str) -> None:
    page, base, _ = view
    client = Client(int(base.rsplit(":", 1)[1]))
    pid = client.publish(title="Diagram", html=DIAGRAM.replace("{src}", src))["id"]
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    page.route(MERMAID_CDN_URL, lambda route: route.fulfill(content_type="text/javascript", body=VENDORED_MERMAID))
    frame = open_view(page, f"{base}/{pid}/view")

    frame.wait_for_function("document.querySelectorAll('pre.mermaid svg').length === 2", timeout=15000)
    frame.click("#to-flow")
    page.wait_for_function("location.hash === '#flow'")

    assert frame.evaluate("scrollY") > 2500
    assert frame.evaluate("document.querySelector('pre.mermaid svg').textContent").count("Input") == 1
    outside = [url for url in requested if not url.startswith(base)]
    assert outside == ([] if src == MERMAID_PATH else [MERMAID_CDN_URL])


@pytest.mark.parametrize("opened", ["framed", "alone"])
def test_unguarded_storage_keeps_working_in_memory_inside_the_sandbox(browser, client: Client, opened: str) -> None:
    base = f"http://127.0.0.1:{client.port}"
    pid = client.publish(title="Storage", html=STORAGE_USER)["id"]
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"{base}/{pid}/view" if opened == "framed" else f"{base}/{pid}/v1/content")
        frame = page.frame(url=lambda url: "/content" in url)
        frame.wait_for_function("window.seen !== undefined")

        seen = frame.evaluate("window.seen")
        page.reload()
        frame = page.frame(url=lambda url: "/content" in url)
        frame.wait_for_function("window.seen !== undefined")
        after_reload = frame.evaluate("window.seen.theme")
    finally:
        page.close()

    assert errors == []
    assert seen == {
        "theme": "light", "stored": "dark", "named": "costs", "property": "dark", "length": 2, "key": "theme",
        "keys": ["tab", "theme"], "visits": "1", "has": True, "after": 0, "cleared": 0,
    }
    assert after_reload == "light"
