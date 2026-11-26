"""Server-rendered pages may be framed only by this server; `/content` and machine routes keep their own headers."""

from __future__ import annotations

import html
import http.server
import threading
from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from support import Client, in_ci, unavailable

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PAGE_HEADERS = {"content-security-policy": "frame-ancestors 'self'", "x-frame-options": "SAMEORIGIN"}


def publish_two(client: Client) -> str:
    pid = client.publish(title="Plan", html="<h1>one</h1>")["id"]
    client.publish(id=pid, title="Plan", html="<h1>two</h1>")
    return pid


def framing(resp) -> dict[str, str]:
    return {name: resp.headers[name] for name in PAGE_HEADERS if name in resp.headers}


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/", "/?q=plan", "/{id}/view", "/{id}/v1/view", "/{id}/diff/v1..v2"])
def test_every_page_refuses_other_framers(client: Client, path: str, method: str) -> None:
    resp = client.request(method, path.format(id=publish_two(client)))

    assert resp.status == 200
    assert framing(resp) == PAGE_HEADERS


@pytest.mark.parametrize("headers", [BROWSER, {}], ids=["page", "standard"])
@pytest.mark.parametrize("path", ["/abcdef12/view", "/nope", "/?cursor=%21", "/abcdef12/diff/bad"])
def test_errors_on_page_routes_refuse_other_framers(client: Client, path: str, headers: dict[str, str]) -> None:
    resp = client.request("GET", path, headers=headers)

    assert resp.status >= 400
    assert framing(resp) == PAGE_HEADERS


def test_content_keeps_its_own_frame_policy(client: Client) -> None:
    pid = publish_two(client)

    headers = client.get(f"/{pid}/v1/content").headers

    assert "x-frame-options" not in headers
    assert headers["content-security-policy"].startswith("sandbox ")
    assert "frame-ancestors 'self' http://localhost:" in headers["content-security-policy"]


@pytest.mark.parametrize(
    "path",
    ["/api/pergams", "/api/pergams/{id}", "/{id}/meta", "/{id}/versions", "/{id}/v1/raw", "/{id}/text", "/feed.atom",
     "/healthz", "/static/pergam.css", "/api/pergams/abcdef12", "/abcdef12/meta"],
)
def test_machine_routes_and_static_files_carry_no_page_headers(client: Client, path: str) -> None:
    resp = client.request("GET", path.format(id=publish_two(client)), headers=BROWSER)

    assert framing(resp) == {}


def test_writes_carry_no_page_headers(client: Client) -> None:
    pid = publish_two(client)

    resp = client.request("PATCH", f"/{pid}/meta", b'{"pinned":true}', {"Content-Type": "application/json"})

    assert resp.status == 200
    assert framing(resp) == {}


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
def framer() -> Iterator[Callable[[str], str]]:
    """A page on another origin (`127.0.0.1` on its own port) that frames the URL it is given.

    It is a real loopback server: Chrome blocks a page the test fulfils itself from
    reaching loopback (local network access), whatever the frame's headers say.
    """

    class Framer(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            target = html.escape(parse_qs(urlparse(self.path).query)["url"][0])
            body = f'<!doctype html><iframe id="target" src="{target}" style="opacity:.01"></iframe>'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Framer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield lambda url: f"http://127.0.0.1:{server.server_port}/?{urlencode({'url': url})}"
    server.shutdown()
    server.server_close()


def framed_by_another_site(browser, framer_url: str) -> tuple[str, list[str]]:
    """Where the other origin's frame ends up, and the console messages."""
    page = browser.new_page()
    console: list[str] = []
    page.on("console", lambda message: console.append(message.text))
    try:
        page.goto(framer_url)
        page.wait_for_load_state("load")
        page.wait_for_timeout(300)
        return page.frames[1].url, console
    finally:
        page.close()


def test_another_site_cannot_frame_the_view_page(browser, client: Client, framer) -> None:
    pid = publish_two(client)
    base = f"http://127.0.0.1:{client.port}"

    view_url, console = framed_by_another_site(browser, framer(f"{base}/{pid}/view"))
    control_url, _ = framed_by_another_site(browser, framer(f"{base}/healthz"))

    assert control_url == f"{base}/healthz"
    assert not view_url.startswith(base)
    assert any("frame-ancestors" in line or "X-Frame-Options" in line for line in console), console


def test_the_view_page_still_frames_its_document(browser, client: Client) -> None:
    pid = client.publish(title="Doc", html="<p id=x>inside</p>")["id"]
    page = browser.new_page()
    try:
        page.goto(f"http://127.0.0.1:{client.port}/{pid}/view")
        frame = page.frame(url=lambda url: "/content" in url)
        frame.wait_for_selector("#x")
        assert frame.inner_text("#x") == "inside"
    finally:
        page.close()
