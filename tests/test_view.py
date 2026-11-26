from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from pergam.meta import Meta
from pergam.store import VersionInfo
from pergam.view_page import FramePolicy, render_view
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


def test_outer_fragment_is_forwarded_into_the_frame(client: Client) -> None:
    pid = client.publish()["id"]

    page = client.get(f"/{pid}/view").text

    assert "frame.src = src + location.hash" in page
    assert "addEventListener('hashchange'" in page


def test_content_is_byte_identical_and_cacheable(client: Client) -> None:
    html = '<!doctype html><a href="#s">s</a><h2 id="s">ñ — 日本</h2>'
    pid = client.publish(html=html)["id"]

    content, raw = client.get(f"/{pid}/v1/content"), client.get(f"/{pid}/v1/raw")

    assert content.body == raw.body == html.encode("utf-8")
    assert content.headers["content-type"] == "text/html; charset=utf-8"
    assert raw.headers["content-type"] == "text/plain; charset=utf-8"
    assert content.headers["cache-control"] == "public, max-age=31536000, immutable"


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
