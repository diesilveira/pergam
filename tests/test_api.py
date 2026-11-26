from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
import pytest

from support import Client, crawl, listed_order, patch_meta, seed


def api_crawl(client: Client, query: str) -> list[str]:
    visited: list[str] = []
    separator = "&" if query else "?"
    path = f"/api/pergams{query}"
    for _ in range(10):
        body = client.get(path).json()
        visited.extend(row["id"] for row in body["pergams"])
        if not body["next_cursor"]:
            return visited
        path = f"/api/pergams{query}{separator}cursor={body['next_cursor']}"
    raise AssertionError("too many pages")


@pytest.fixture
def catalogue(client: Client) -> dict[str, str]:
    ids = {
        "plan": client.publish(title="Deploy plan", type="plan", author="ana@example.com", tags=["ops"])["id"],
        "report": client.publish(
            title="Weekly report", type="reporte", author="bo@example.com", html="<p>error budget burned</p>"
        )["id"],
        "notes": client.publish(title="Release notes", type="plan", author="bo@example.com", tags=["ops", "q3"])["id"],
        "archived": client.publish(title="Old plan", type="plan", author="ana@example.com", tags=["ops"])["id"],
    }
    client.publish(id=ids["notes"], title="Release notes v2", type="plan", author="bo@example.com")
    patch_meta(client, ids["archived"], {"archived": True})
    patch_meta(client, ids["report"], {"pinned": True})
    return ids


@pytest.mark.parametrize(
    "query",
    ["", "?type=plan", "?author=bo@example.com", "?tag=OPS", "?q=budget", "?q=notes", "?archived=1", "?tag=ops&archived=1"],
)
def test_api_and_html_index_list_the_same_pergams(client: Client, catalogue: dict[str, str], query: str) -> None:
    html_ids = listed_order(client.get(f"/{query}").text)
    body = client.get(f"/api/pergams{query}").json()

    assert [row["id"] for row in body["pergams"]] == html_ids
    assert body["matching"] == len(html_ids)


@pytest.mark.parametrize("query", ["", "?archived=1", "?tag=seeded&type=plan"])
def test_api_pages_like_the_html_index(client: Client, db: psycopg.Connection, query: str) -> None:
    seed(db)

    visited = api_crawl(client, query)

    assert visited == crawl(client, f"/{query}")
    assert len(visited) == len(set(visited))


def test_api_index_row_shape(client: Client, catalogue: dict[str, str]) -> None:
    resp = client.get("/api/pergams?tag=q3")

    assert resp.status == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-store"
    (row,) = resp.json()["pergams"]
    created_at = row.pop("created_at")
    assert datetime.fromisoformat(created_at).tzinfo is not None
    port = client.port
    assert row == {
        "id": catalogue["notes"],
        "version": 2,
        "title": "Release notes v2",
        "type": "plan",
        "author": "bo@example.com",
        "bytes": len("<h1>doc</h1>"),
        "versions": 2,
        "tags": ["ops", "q3"],
        "archived": False,
        "pinned": False,
        "view_url": f"http://localhost:{port}/{catalogue['notes']}/view",
        "snippet": None,
    }


def test_api_index_counts_and_stats(client: Client, catalogue: dict[str, str]) -> None:
    body = client.get("/api/pergams").json()

    assert body["archived_count"] == 1
    assert body["next_cursor"] is None
    assert body["counts"]["types"] == [{"type": "plan", "count": 2}, {"type": "reporte", "count": 1}]
    assert body["counts"]["authors"] == [{"author": "bo@example.com", "count": 2}, {"author": "ana@example.com", "count": 1}]
    assert body["counts"]["tags"] == [{"tag": "ops", "count": 2}, {"tag": "q3", "count": 1}]
    assert {k: body["stats"][k] for k in ("pergams", "authors", "versions")} == {
        "pergams": 4,
        "authors": 2,
        "versions": 5,
    }


def test_api_index_body_match_has_a_plain_text_snippet(client: Client, catalogue: dict[str, str]) -> None:
    (row,) = client.get("/api/pergams?q=budget").json()["pergams"]

    assert row["snippet"] == "error budget burned"


def test_api_index_rejects_an_invalid_cursor(client: Client) -> None:
    assert client.get("/api/pergams?cursor=nope").status == 400


def test_api_pergam_has_metadata_tags_and_versions(client: Client, catalogue: dict[str, str]) -> None:
    pid, port = catalogue["notes"], client.port

    resp = client.get(f"/api/pergams/{pid}")

    assert resp.status == 200
    assert resp.headers["cache-control"] == "no-store"
    body: dict[str, Any] = resp.json()
    versions = body.pop("versions")
    body.pop("created_at")
    assert body == {
        "id": pid,
        "version": 2,
        "title": "Release notes v2",
        "type": "plan",
        "author": "bo@example.com",
        "bytes": len("<h1>doc</h1>"),
        "tags": ["ops", "q3"],
        "archived": False,
        "pinned": False,
        "view_url": f"http://localhost:{port}/{pid}/view",
    }
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["title"] for v in versions] == ["Release notes v2", "Release notes"]
    assert versions[1]["view_url"] == f"http://localhost:{port}/{pid}/v1/view"
    assert set(versions[0]) == {"version", "title", "type", "author", "bytes", "created_at", "view_url"}


def test_api_pergam_reports_archived_and_pinned(client: Client, catalogue: dict[str, str]) -> None:
    assert client.get(f"/api/pergams/{catalogue['archived']}").json()["archived"] is True
    assert client.get(f"/api/pergams/{catalogue['report']}").json()["pinned"] is True


@pytest.mark.parametrize("path", ["/api/pergams/abcdef99", "/api/pergams/NOT-AN-ID", "/api/pergams/x/y"])
def test_api_pergam_unknown_is_404(client: Client, path: str) -> None:
    assert client.get(path).status == 404
