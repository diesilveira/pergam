from __future__ import annotations

import json

import psycopg
import pytest

from support import Client, Response, listed_order


def patch(client: Client, pergam_id: str, payload: object) -> Response:
    return client.request(
        "PATCH", f"/{pergam_id}/meta", json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"}
    )


def version_count(db: psycopg.Connection, pergam_id: str) -> int:
    return db.execute("SELECT count(*) FROM pergams WHERE id = %s", (pergam_id,)).fetchone()[0]


def test_untouched_id_reads_as_default_metadata(client: Client) -> None:
    pid = client.publish()["id"]

    resp = client.get(f"/{pid}/meta")

    assert resp.status == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json() == {"id": pid, "tags": [], "archived": False, "pinned": False}


def test_patch_updates_metadata_without_creating_a_version(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish(tags=["draft"])["id"]
    client.publish(id=pid)

    resp = patch(client, pid, {"tags": ["Q3 Review", "ops"], "archived": True, "pinned": True})

    assert resp.status == 200
    assert resp.json() == {"id": pid, "tags": ["ops", "q3-review"], "archived": True, "pinned": True}
    assert client.get(f"/{pid}/meta").json() == resp.json()
    assert version_count(db, pid) == 2


def test_patch_changes_only_the_fields_it_sends(client: Client) -> None:
    pid = client.publish(tags=["ops"])["id"]

    assert patch(client, pid, {"pinned": True}).json() == {"id": pid, "tags": ["ops"], "archived": False, "pinned": True}
    assert patch(client, pid, {"archived": True}).json() == {"id": pid, "tags": ["ops"], "archived": True, "pinned": True}
    assert patch(client, pid, {"tags": []}).json() == {"id": pid, "tags": [], "archived": True, "pinned": True}
    assert patch(client, pid, {"pinned": False, "archived": False}).json()["pinned"] is False


def test_patch_tags_replace_and_later_posts_merge(client: Client) -> None:
    pid = client.publish(tags=["old"])["id"]

    patch(client, pid, {"tags": ["new"]})

    assert client.publish(id=pid, tags=["more"])["tags"] == ["more", "new"]


def test_patch_on_id_without_metadata_row_creates_it(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish()["id"]

    assert patch(client, pid, {"archived": True}).json() == {"id": pid, "tags": [], "archived": True, "pinned": False}
    assert db.execute("SELECT tags, archived, pinned FROM pergam_meta WHERE id = %s", (pid,)).fetchone() == (
        [],
        True,
        False,
    )


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_unknown_id_is_not_found(client: Client, db: psycopg.Connection, method: str) -> None:
    resp = patch(client, "abcdef12", {"pinned": True}) if method == "PATCH" else client.get("/abcdef12/meta")

    assert resp.status == 404
    assert db.execute("SELECT count(*) FROM pergam_meta").fetchone() == (0,)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        "tags",
        {"tags": "ops"},
        {"tags": None},
        {"tags": [1]},
        {"tags": ["x" * 41]},
        {"tags": [f"t{i}" for i in range(21)]},
        {"archived": "yes"},
        {"pinned": 1},
        {"pinned": None},
        {"title": "renamed"},
    ],
    ids=[
        "empty",
        "array",
        "string",
        "tags-string",
        "tags-null",
        "tags-number",
        "tag-too-long",
        "too-many-tags",
        "archived-string",
        "pinned-number",
        "pinned-null",
        "unknown-field",
    ],
)
def test_malformed_patch_is_rejected(client: Client, db: psycopg.Connection, payload: object) -> None:
    pid = client.publish(tags=["keep"])["id"]

    resp = patch(client, pid, payload)

    assert resp.status == 400
    assert client.get(f"/{pid}/meta").json() == {"id": pid, "tags": ["keep"], "archived": False, "pinned": False}


def test_patch_rejects_invalid_json_and_wrong_content_type(client: Client) -> None:
    pid = client.publish()["id"]

    assert client.request("PATCH", f"/{pid}/meta", b"{nope", {"Content-Type": "application/json"}).status == 400
    assert client.request("PATCH", f"/{pid}/meta", b"", {"Content-Type": "application/json"}).status == 400
    assert client.request("PATCH", f"/{pid}/meta", b'{"pinned":true}', {"Content-Type": "text/plain"}).status == 415


@pytest.mark.parametrize("path", ["/abcdef12", "/abcdef12/view", "/NOT-HEX/meta", "/abcdef12/v1/meta"])
def test_patch_on_other_paths_is_not_found(client: Client, path: str) -> None:
    resp = client.request("PATCH", path, b'{"pinned":true}', {"Content-Type": "application/json"})

    assert resp.status == 404


def test_post_body_must_be_an_object(client: Client) -> None:
    resp = client.request("POST", "/pergam", b"[1, 2]", {"Content-Type": "application/json"})

    assert resp.status == 400


def test_new_version_keeps_archived_and_pinned(client: Client) -> None:
    pid = client.publish()["id"]
    patch(client, pid, {"archived": True, "pinned": True})

    client.publish(id=pid, html="<p>v2</p>", tags=["more"])

    assert client.get(f"/{pid}/meta").json() == {"id": pid, "tags": ["more"], "archived": True, "pinned": True}
    assert pid not in listed_order(client.get("/").text)
