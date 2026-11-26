from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from pergam.meta import Meta
from pergam.store import VersionInfo
from pergam.view_page import BRIDGE_TAG, SANDBOX, FramePolicy, frame_policy, framed, render_view
from support import Client


def publish_versions(client: Client, count: int, **fields: str) -> str:
    pid = client.publish(html="<h1>v1</h1>", **fields)["id"]
    for n in range(2, count + 1):
        client.publish(id=pid, html=f"<h1>v{n}</h1>", **fields)
    return pid


def frame_src(page: str) -> str:
    match = re.search(r'<iframe class="doc" src="([^"]+)"', page)
    assert match, page
    return match.group(1)


def test_view_frames_the_latest_version_in_a_page_of_its_own(client: Client) -> None:
    pid = publish_versions(client, 2, title="Plan & budget", type="plan", author="ana@example.com")
    client.request("PATCH", f"/{pid}/meta", b'{"tags":["q3"]}', {"Content-Type": "application/json"})

    resp = client.get(f"/{pid}/view")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert resp.headers["cache-control"] == "no-store"
    page = resp.text
    assert frame_src(page) == f"/{pid}/v2/content"
    assert '<h1 class="bar__title">Plan &amp; budget</h1>' in page
    assert '<span class="type" style="--type-rgb:240,210,138">plan</span>' in page
    assert '<a class="tag" href="/?tag=q3">q3</a>' in page
    assert "ana@example.com" in page
    assert "<summary>v2 of 2</summary>" in page
    assert '<a href="/">index</a>' in page
    assert f'<a href="/{pid}/v2/raw">raw</a>' in page
    assert 'class="outdated"' not in page


def test_earlier_version_shows_that_a_newer_one_exists(client: Client) -> None:
    pid = publish_versions(client, 5)

    page = client.get(f"/{pid}/v1/view").text

    assert frame_src(page) == f"/{pid}/v1/content"
    assert "<summary>v1 of 5</summary>" in page
    assert re.search(
        rf'<p class="outdated" role="status">.*<a href="/{pid}/view">Go to the current version \(v5\)</a>', page
    )


def test_latest_version_pinned_by_number_has_no_outdated_notice(client: Client) -> None:
    pid = publish_versions(client, 3)

    page = client.get(f"/{pid}/v3/view").text

    assert "<summary>v3 of 3</summary>" in page
    assert 'class="outdated"' not in page


def test_version_selector_links_every_version_newest_first(client: Client) -> None:
    pid = publish_versions(client, 3)

    page = client.get(f"/{pid}/v2/view").text

    links = re.findall(rf'<a href="/{pid}/v(\d+)/view"( aria-current="page")?>', page)
    assert links == [("3", ""), ("2", ' aria-current="page"'), ("1", "")]


def test_title_and_type_are_those_of_the_version_shown(client: Client) -> None:
    pid = client.publish(title="First", type="plan")["id"]
    client.publish(id=pid, title="Second", type="informe")

    page = client.get(f"/{pid}/v1/view").text

    assert '<h1 class="bar__title">First</h1>' in page
    assert ">plan</span>" in page


def test_bar_escapes_untrusted_fields(client: Client) -> None:
    pid = client.publish(title='<script>alert(1)</script>"', author="<b>x</b>@example.com")["id"]

    page = client.get(f"/{pid}/view").text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;b&gt;x&lt;/b&gt;@example.com" in page


def test_bar_links_the_document_alone_and_offers_print_once_bridged(client: Client) -> None:
    pid = client.publish()["id"]

    page = client.get(f"/{pid}/view").text

    assert f'<a href="/{pid}/v1/content">open document</a>' in page
    assert '<button type="button" class="bar__print" hidden>print</button>' in page
    assert '<body data-title-suffix=" · v1 · pergam">' in page


def test_content_is_the_posted_html_plus_the_bridge_tag_and_raw_is_untouched(client: Client) -> None:
    html = '<!doctype html><a href="#s">s</a><h2 id="s">ñ — 日本</h2>'
    pid = client.publish(html=html)["id"]

    content, raw = client.get(f"/{pid}/v1/content"), client.get(f"/{pid}/v1/raw")

    assert raw.body == html.encode("utf-8")
    assert content.body == (
        '<!doctype html><script src="/static/frame-bridge.js" defer></script><a href="#s">s</a><h2 id="s">ñ — 日本</h2>'
    ).encode("utf-8")
    assert content.headers["content-length"] == str(len(content.body))
    assert content.headers["content-type"] == "text/html; charset=utf-8"
    assert raw.headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>x</p>", f"{BRIDGE_TAG}<p>x</p>"),
        ("<!DOCTYPE html>\n<title>t", f"<!DOCTYPE html>{BRIDGE_TAG}\n<title>t"),
        ("\ufeff <!-- a -->\n<!doctype html><p>", f"\ufeff <!-- a -->\n<!doctype html>{BRIDGE_TAG}<p>"),
        ("<p>x</p><!doctype html>", f"{BRIDGE_TAG}<p>x</p><!doctype html>"),
        ("<!-- never closed <!doctype html>", f"{BRIDGE_TAG}<!-- never closed <!doctype html>"),
    ],
    ids=["no-doctype", "doctype", "bom-and-comment", "late-doctype", "open-comment"],
)
def test_bridge_tag_goes_after_a_leading_doctype_or_first(html: str, expected: str) -> None:
    assert framed(html) == expected


def test_content_is_sandboxed_away_from_the_api_origin(client: Client) -> None:
    pid = client.publish()["id"]

    headers = client.get(f"/{pid}/v1/content").headers

    directives = dict(d.strip().split(" ", 1) for d in headers["content-security-policy"].split(";"))
    own = f"'self' http://localhost:{client.port}"
    assert directives == {
        "sandbox": SANDBOX,
        "default-src": "'none'",
        "script-src": f"'unsafe-inline' {own} https://cdn.jsdelivr.net",
        "style-src": "'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src": "data:",
        "font-src": "data: https://cdn.jsdelivr.net",
        "connect-src": "https://cdn.jsdelivr.net",
        "form-action": "'none'",
        "frame-ancestors": own,
        "base-uri": "'none'",
    }
    assert "allow-same-origin" not in SANDBOX
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_view_frames_the_document_in_a_sandbox_without_its_origin(client: Client) -> None:
    pid = client.publish(title="Doc")["id"]

    page = client.get(f"/{pid}/view").text

    assert f'<iframe class="doc" src="/{pid}/v1/content" title="Doc" sandbox="{SANDBOX}"></iframe>' in page


def test_frame_policy_names_the_public_origin_only_when_a_csp_can() -> None:
    named = frame_policy("https://docs.example.com")
    unnamed = frame_policy(None)

    assert "script-src 'unsafe-inline' 'self' https://docs.example.com https://cdn.jsdelivr.net" in named.content_headers[0][1]
    assert "frame-ancestors 'self';" in unnamed.content_headers[0][1]


@pytest.mark.parametrize("path", ["/abcdef12/view", "/abcdef12/v1/view", "/abcdef12/v1/content", "/{pid}/v2/view"])
def test_unknown_ids_and_versions_are_not_found(client: Client, path: str) -> None:
    pid = client.publish()["id"]

    assert client.get(path.format(pid=pid)).status == 404


def test_content_has_no_unversioned_route(client: Client) -> None:
    pid = client.publish()["id"]

    assert client.get(f"/{pid}/content").status == 404


def test_frame_policy_is_the_one_place_that_isolates_the_document() -> None:
    versions = [VersionInfo(1, "Doc", "otro", "you@example.com", 9, datetime(2025, 1, 1, tzinfo=timezone.utc))]
    policy = FramePolicy(iframe_attributes=(("sandbox", "allow-scripts"),))

    page = render_view("abcdef12", versions, Meta([], False, False), 1, policy)

    assert '<iframe class="doc" src="/abcdef12/v1/content" title="Doc" sandbox="allow-scripts"></iframe>' in page


def test_bridge_script_is_served_with_a_short_cache(client: Client) -> None:
    resp = client.get("/static/frame-bridge.js")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/javascript; charset=utf-8"
    assert resp.headers["cache-control"] == "public, max-age=300"
    assert resp.body.startswith(b"/*\n * pergam frame bridge")


@pytest.mark.parametrize("path", ["/static/other.js", "/static/", "/static/../server.py", "/static/frame-bridge.js/x"])
def test_only_the_bridge_is_served_from_static(client: Client, path: str) -> None:
    assert client.get(path).status == 404
