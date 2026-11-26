"""Errors are a page for browsers (`Accept: text/html`) and the library's response for everyone else."""

from __future__ import annotations

import pytest

from pergam.error_page import is_machine_route, prefers_html, render_error, wants_error_page
from support import Client

BROWSER = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}
STANDARD_PAGE = "<title>Error response</title>"


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        (BROWSER["Accept"], True),
        ("text/html", True),
        ("TEXT/HTML; charset=utf-8", True),
        ("application/json, text/html", True),
        ("*/*", False),
        ("", False),
        (None, False),
        ("application/json", False),
        ("application/json, text/html;q=0.1", False),
        ("text/html;q=0", False),
        ("text/html;q=oops", False),
        ("text/*", False),
    ],
)
def test_only_a_client_that_asks_for_html_prefers_it(accept: str | None, expected: bool) -> None:
    assert prefers_html(accept) is expected


@pytest.mark.parametrize(
    "path",
    ["/api/pergams", "/api/pergams/abcdef12", "/healthz", "/feed.atom", "/abcdef12/versions", "/abcdef12/meta",
     "/abcdef12/collaborators", "/abcdef12/export", "/abcdef12/raw", "/abcdef12/text", "/abcdef12/v1/raw",
     "/abcdef12/v12/text"],
)
def test_machine_routes_are_never_pages(path: str) -> None:
    assert is_machine_route(path)
    assert not wants_error_page("GET", path, BROWSER["Accept"])


@pytest.mark.parametrize("path", ["/", "/abcdef12/view", "/abcdef12/v1/view", "/abcdef12/diff/v1..v2", "/abcdef12/v1/content", "/nope"])
def test_page_routes_may_be_pages(path: str) -> None:
    assert wants_error_page("GET", path, BROWSER["Accept"])
    assert wants_error_page("HEAD", path, BROWSER["Accept"])
    assert not wants_error_page("POST", path, BROWSER["Accept"])


def test_missing_pergam_in_a_browser_is_a_styled_404(client: Client) -> None:
    resp = client.request("GET", "/abcdef12/view", headers=BROWSER)

    assert resp.status == 404
    page = resp.text
    assert page.startswith("<!doctype html>")
    assert '<header class="topbar">' in page
    assert "404 · NOT FOUND" in page
    assert "<h1>Not found</h1>" in page
    assert "<code>Pergam not found</code>" in page
    assert '<a class="btn btn--primary" href="/">Go to the index</a>' in page
    assert 'action="/" method="get" role="search"' in page
    assert '<link rel="stylesheet" href="/static/pergam.css?v=' in page


@pytest.mark.parametrize(
    ("path", "status"),
    [("/?cursor=%21", 400), ("/abcdef12/diff/bad", 400), ("/abcdef12/diff/v1..v2", 404), ("/nope", 404), ("/abcdef12/v9/content", 404)],
)
def test_browser_errors_across_routes(client: Client, path: str, status: int) -> None:
    resp = client.request("GET", path, headers=BROWSER)

    assert resp.status == status
    assert '<header class="topbar">' in resp.text


@pytest.mark.parametrize("accept", [None, "*/*", "application/json", "application/json, text/html;q=0.1"])
@pytest.mark.parametrize("path", ["/abcdef12/view", "/nope", "/?cursor=%21"])
def test_other_clients_keep_the_standard_error(client: Client, path: str, accept: str | None) -> None:
    headers = {"Accept": accept} if accept is not None else {}

    resp = client.request("GET", path, headers=headers)

    assert STANDARD_PAGE in resp.text
    assert "topbar" not in resp.text


@pytest.mark.parametrize(
    "path",
    ["/api/pergams/abcdef12", "/abcdef12/versions", "/abcdef12/meta", "/abcdef12/collaborators", "/abcdef12/export",
     "/abcdef12/raw", "/abcdef12/text", "/abcdef12/v1/raw", "/abcdef12/v1/text"],
)
def test_json_routes_keep_the_standard_error_even_for_a_browser(client: Client, path: str) -> None:
    resp = client.request("GET", path, headers=BROWSER)

    assert resp.status == 404
    assert STANDARD_PAGE in resp.text


def test_writes_keep_the_standard_error_even_with_a_browser_accept(client: Client) -> None:
    posted = client.request("POST", "/pergam", b"", {**BROWSER, "Content-Type": "application/json"})
    deleted = client.request("DELETE", "/abcdef12", headers=BROWSER)

    assert (posted.status, deleted.status) == (400, 405)
    assert STANDARD_PAGE in posted.text and STANDARD_PAGE in deleted.text


def test_error_headers_are_the_same_for_a_page_and_for_the_standard_response(client: Client) -> None:
    page = client.request("GET", "/abcdef12/view", headers=BROWSER)
    plain = client.request("GET", "/abcdef12/view")

    def shape(resp) -> dict[str, str]:
        return {k: v for k, v in resp.headers.items() if k not in ("date", "content-length")}

    assert shape(page) == shape(plain)
    assert "cache-control" not in page.headers
    assert page.headers["connection"] == "close"
    assert page.headers["content-type"] == "text/html;charset=utf-8"
    assert page.headers["content-length"] == str(len(page.body))


def test_head_on_a_browser_error_mirrors_get(client: Client) -> None:
    get = client.request("GET", "/abcdef12/view", headers=BROWSER)
    head = client.request("HEAD", "/abcdef12/view", headers=BROWSER)

    assert head.status == get.status == 404
    assert {k: v for k, v in head.headers.items() if k != "date"} == {k: v for k, v in get.headers.items() if k != "date"}
    assert head.body == b""


def test_the_status_line_keeps_the_standard_reason_and_the_body_never_echoes_the_path(client: Client) -> None:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    try:
        conn.request("GET", "/%3Cscript%3Ealert(1)%3C/script%3E", headers=BROWSER)
        resp = conn.getresponse()
        body = resp.read().decode()
    finally:
        conn.close()

    assert (resp.status, resp.reason) == (404, "Not Found")
    assert "alert(1)" not in body


def test_render_error_escapes_the_message_and_the_host() -> None:
    page = render_error(403, '<script>alert("x")</script> 100% & more', 'evil"><b>host')

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; 100% &amp; more" in page
    assert "<b>host" not in page
    assert "403 · FORBIDDEN" in page


@pytest.mark.parametrize(
    ("status", "heading"),
    [(400, "Bad request"), (403, "Not allowed"), (404, "Not found"), (413, "Too large"), (418, "Request not accepted"),
     (500, "Something went wrong"), (599, "Something went wrong")],
)
def test_every_status_has_a_heading(status: int, heading: str) -> None:
    assert f"<h1>{heading}</h1>" in render_error(status, "x", None)


def test_an_unknown_status_still_renders() -> None:
    page = render_error(499, "x", None)

    assert "499 · ERROR" in page


def test_a_message_that_is_the_standard_phrase_is_not_repeated(client: Client) -> None:
    page = client.request("GET", "/nope", headers=BROWSER).text

    assert '<p class="error__detail">' not in page


@pytest.mark.parametrize("accept", [None, "*/*", BROWSER["Accept"]])
@pytest.mark.parametrize("path", ["/abcdef12/view", "/nope", "/?cursor=%21"])
def test_errors_that_depend_on_accept_say_so(client: Client, path: str, accept: str | None) -> None:
    headers = {"Accept": accept} if accept is not None else {}

    assert client.request("GET", path, headers=headers).headers["vary"] == "Accept"
    assert client.request("HEAD", path, headers=headers).headers["vary"] == "Accept"


@pytest.mark.parametrize("path", ["/api/pergams/abcdef12", "/abcdef12/raw", "/abcdef12/v1/text"])
def test_machine_route_errors_do_not_vary(client: Client, path: str) -> None:
    assert "vary" not in client.request("GET", path, headers=BROWSER).headers


def test_vary_does_not_leak_to_the_next_response(client: Client) -> None:
    client.request("GET", "/nope", headers=BROWSER)

    assert "vary" not in client.get("/healthz").headers
    assert "vary" not in client.request("POST", "/pergam", b"", {"Content-Type": "application/json"}).headers
