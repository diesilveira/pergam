from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import pytest

from pergam.meta import Meta
from pergam.store import VersionInfo
from pergam.static_files import MERMAID_PATH, MERMAID_VERSION, STORAGE_SHIM
from pergam.view_page import PREAMBLE, SANDBOX, FramePolicy, frame_policy, framed, render_view
from support import Client

MERMAID_SHA256 = "581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8"


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
    assert '<span class="badge" style="--type-rgb:240,210,138">plan</span>' in page
    assert '<a class="tag" href="/?tag=q3">q3</a>' in page
    assert "ana@example.com" in page
    assert '<summary class="ver dropdown__toggle">v2<span class="ver__of"> of 2</span></summary>' in page
    assert '<nav aria-label="Breadcrumb"><a href="/">Index</a></nav>' in page
    assert f'<a href="/{pid}/v2/raw">Raw HTML</a>' in page
    assert 'class="outdated"' not in page


def test_earlier_version_shows_that_a_newer_one_exists(client: Client) -> None:
    pid = publish_versions(client, 5)

    page = client.get(f"/{pid}/v1/view").text

    assert frame_src(page) == f"/{pid}/v1/content"
    assert '<summary class="ver dropdown__toggle">v1<span class="ver__of"> of 5</span></summary>' in page
    assert re.search(
        rf'<p class="outdated" role="status">.*<a href="/{pid}/view">Go to the current version \(v5\)</a>', page
    )


def test_latest_version_pinned_by_number_has_no_outdated_notice(client: Client) -> None:
    pid = publish_versions(client, 3)

    page = client.get(f"/{pid}/v3/view").text

    assert '<summary class="ver dropdown__toggle">v3<span class="ver__of"> of 3</span></summary>' in page
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

    assert f'<a href="/{pid}/v1/content">Open document</a>' in page
    assert '<li><button type="button" class="dropdown__item bar__print" hidden>Print</button></li>' in page
    assert '<body data-title-suffix=" · v1 · pergam">' in page


def test_content_is_the_posted_html_plus_the_bridge_tag_and_raw_is_untouched(client: Client) -> None:
    html = '<!doctype html><a href="#s">s</a><h2 id="s">ñ — 日本</h2>'
    pid = client.publish(html=html)["id"]

    content, raw = client.get(f"/{pid}/v1/content"), client.get(f"/{pid}/v1/raw")

    assert raw.body == html.encode("utf-8")
    assert content.body == (
        '<!doctype html><script>' + STORAGE_SHIM + '</script><script src="/static/frame-bridge.js" defer></script>'
        '<a href="#s">s</a><h2 id="s">ñ — 日本</h2>'
    ).encode("utf-8")
    assert content.headers["content-length"] == str(len(content.body))
    assert content.headers["content-type"] == "text/html; charset=utf-8"
    assert raw.headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>x</p>", f"{PREAMBLE}<p>x</p>"),
        ("<!DOCTYPE html>\n<title>t", f"<!DOCTYPE html>{PREAMBLE}\n<title>t"),
        ("\ufeff <!-- a -->\n<!doctype html><p>", f"\ufeff <!-- a -->\n<!doctype html>{PREAMBLE}<p>"),
        ("<p>x</p><!doctype html>", f"{PREAMBLE}<p>x</p><!doctype html>"),
        ("<!-- never closed <!doctype html>", f"{PREAMBLE}<!-- never closed <!doctype html>"),
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


def test_vendored_mermaid_and_its_licence_are_served_for_good(client: Client) -> None:
    script = client.get(MERMAID_PATH)
    licence = client.get(f"/static/mermaid/{MERMAID_VERSION}/LICENSE")

    assert script.status == 200
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert script.headers["x-content-type-options"] == "nosniff"
    assert hashlib.sha256(script.body).hexdigest() == MERMAID_SHA256
    assert licence.headers["content-type"] == "text/plain; charset=utf-8"
    assert licence.text.startswith("The MIT License (MIT)")


def test_static_files_answer_head_without_a_body(client: Client) -> None:
    resp = client.request("HEAD", MERMAID_PATH)

    assert resp.status == 200
    assert resp.body == b""
    assert int(resp.headers["content-length"]) > 1_000_000


@pytest.mark.parametrize(
    "path",
    ["/static/other.js", "/static/", "/static/../server.py", "/static/frame-bridge.js/x", "/static/mermaid/",
     f"/static/mermaid/{MERMAID_VERSION}/", "/static/mermaid/11.4.1/mermaid.min.js", "/static/mermaid/../frame-bridge.js"],
)
def test_only_listed_files_are_served_from_static(client: Client, path: str) -> None:
    assert client.get(path).status == 404


def test_storage_stand_in_is_inlined_without_its_comment_and_never_served() -> None:
    assert STORAGE_SHIM.startswith("(() => {")
    assert "</script" not in STORAGE_SHIM.lower()
    assert len(STORAGE_SHIM) < 2048


def test_storage_stand_in_has_no_route(client: Client) -> None:
    assert client.get("/static/storage-shim.js").status == 404
