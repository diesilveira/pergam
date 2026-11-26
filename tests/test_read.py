from __future__ import annotations

from datetime import datetime

import pytest

from support import Client


def test_healthz(client: Client) -> None:
    resp = client.get("/healthz")

    assert resp.status == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.json() == {"ok": True}


def test_view_serves_latest_html(client: Client) -> None:
    first = client.publish(html="<p>one</p>")
    client.publish(id=first["id"], html="<p>two</p>")

    resp = client.get(f"/{first['id']}/view")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert resp.text == "<p>two</p>"


def test_raw_serves_latest_source_as_plain_text(client: Client) -> None:
    first = client.publish(html="<p>one</p>")
    client.publish(id=first["id"], html="<p>two</p>")

    resp = client.get(f"/{first['id']}/raw")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert resp.text == "<p>two</p>"


@pytest.mark.parametrize(("kind", "content_type"), [("view", "text/html"), ("raw", "text/plain")])
def test_pinned_version_serves_that_version(client: Client, kind: str, content_type: str) -> None:
    first = client.publish(html="<p>one</p>")
    client.publish(id=first["id"], html="<p>two</p>")

    resp = client.get(f"/{first['id']}/v1/{kind}")

    assert resp.status == 200
    assert resp.headers["content-type"] == f"{content_type}; charset=utf-8"
    assert resp.text == "<p>one</p>"


def test_body_is_utf8_with_matching_length(client: Client) -> None:
    html = "<p>ñandú — 日本</p>"
    body = client.publish(html=html)

    resp = client.get(f"/{body['id']}/view")

    assert resp.body == html.encode("utf-8")
    assert resp.headers["content-length"] == str(len(html.encode("utf-8")))


def test_versions_lists_newest_first(client: Client) -> None:
    first = client.publish(title="A", type="plan", html="<p>1</p>")
    client.publish(id=first["id"], title="B", type="informe", html="<p>22</p>")

    resp = client.get(f"/{first['id']}/versions")

    assert resp.status == 200
    assert resp.headers["content-type"] == "application/json"
    rows = resp.json()
    assert [(r["version"], r["title"], r["type"], r["author"], r["bytes"]) for r in rows] == [
        (2, "B", "informe", "you@example.com", 9),
        (1, "A", "plan", "you@example.com", 8),
    ]
    for row in rows:
        assert datetime.fromisoformat(row["created_at"]).tzinfo is not None


@pytest.mark.parametrize(
    "path",
    [
        "/abcdef12/view",
        "/abcdef12/raw",
        "/abcdef12/v1/view",
        "/abcdef12/versions",
        "/ZZZ/view",
        "/abcdef12/vx/view",
        "/abcdef12/v1/other",
        "/abcdef12",
        "/abcdef12/view/extra/parts",
        "/nope",
    ],
)
def test_unknown_routes_and_ids_are_not_found(client: Client, path: str) -> None:
    assert client.get(path).status == 404


def test_missing_version_is_not_found(client: Client) -> None:
    body = client.publish()

    assert client.get(f"/{body['id']}/v2/view").status == 404


def test_every_success_is_no_store(client: Client) -> None:
    body = client.publish()

    for path in ["/", "/healthz", f"/{body['id']}/view", f"/{body['id']}/raw", f"/{body['id']}/versions"]:
        assert client.get(path).headers["cache-control"] == "no-store", path
