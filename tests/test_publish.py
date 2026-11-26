from __future__ import annotations

import json
import re

import psycopg
import pytest

from support import Client

ID_RE = re.compile(r"^[a-f0-9]{8}$")


def test_create_returns_new_id_at_version_one(client: Client) -> None:
    resp = client.post_json(
        {"title": "Hello", "html": "<h1>hi</h1>", "type": "plan", "author": "you@example.com"}
    )

    assert resp.status == 201
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert ID_RE.match(body["id"])
    assert body == {
        "id": body["id"],
        "version": 1,
        "view_url": f"http://localhost:{client.port}/{body['id']}/view",
        "title": "Hello",
        "type": "plan",
        "author": "you@example.com",
    }


def test_create_persists_row(client: Client, db: psycopg.Connection) -> None:
    body = client.publish(title="Stored", html="<p>é</p>", type="informe")

    row = db.execute(
        "SELECT version, title, html, type, author, bytes FROM pergams WHERE id = %s", (body["id"],)
    ).fetchone()

    assert row == (1, "Stored", "<p>é</p>", "informe", "you@example.com", len("<p>é</p>".encode()))


def test_posting_existing_id_bumps_version(client: Client) -> None:
    first = client.publish(html="<p>v1</p>")
    second = client.publish(id=first["id"], html="<p>v2</p>")
    third = client.publish(id=first["id"], html="<p>v3</p>")

    assert (second["id"], second["version"]) == (first["id"], 2)
    assert (third["id"], third["version"]) == (first["id"], 3)


def test_posting_unknown_id_creates_it_at_version_one(client: Client) -> None:
    body = client.publish(id="abcdef12")

    assert (body["id"], body["version"]) == ("abcdef12", 1)


def test_title_defaults_and_type_is_normalised(client: Client) -> None:
    body = client.publish(title=None, type="  PLAN ")

    assert body["title"] == "Pergam"
    assert body["type"] == "plan"


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "x", "html": "<p>x</p>", "type": "nope", "author": "you@example.com"},
        {"title": "x", "html": "<p>x</p>", "author": "you@example.com"},
        {"title": "x", "html": "<p>x</p>", "type": "plan"},
        {"title": "x", "html": "<p>x</p>", "type": "plan", "author": "   "},
        {"title": "x", "type": "plan", "author": "you@example.com"},
        {"title": "x", "html": "   ", "type": "plan", "author": "you@example.com"},
        {"title": "x", "html": "<p>x</p>", "type": "plan", "author": "you@example.com", "id": "NOT-HEX"},
    ],
    ids=["invalid-type", "missing-type", "missing-author", "blank-author", "missing-html", "blank-html", "bad-id"],
)
def test_invalid_payload_is_rejected(client: Client, db: psycopg.Connection, payload: dict) -> None:
    resp = client.post_json(payload)

    assert resp.status == 400
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_invalid_json_is_rejected(client: Client) -> None:
    resp = client.request("POST", "/pergam", b"{not json", {"Content-Type": "application/json"})

    assert resp.status == 400


def test_empty_body_is_rejected(client: Client) -> None:
    resp = client.request("POST", "/pergam", b"", {"Content-Type": "application/json"})

    assert resp.status == 400


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_wrong_content_type_is_unsupported(client: Client, content_type: str) -> None:
    payload = json.dumps({"title": "x", "html": "<p>x</p>", "type": "plan", "author": "you@example.com"})

    resp = client.request("POST", "/pergam", payload.encode(), {"Content-Type": content_type})

    assert resp.status == 415


def test_content_type_parameters_are_accepted(client: Client) -> None:
    payload = json.dumps({"title": "x", "html": "<p>x</p>", "type": "plan", "author": "you@example.com"})

    resp = client.request(
        "POST", "/pergam", payload.encode(), {"Content-Type": "Application/JSON; charset=utf-8"}
    )

    assert resp.status == 201


def test_post_to_other_path_is_not_found(client: Client) -> None:
    resp = client.post_json({"html": "<p>x</p>"}, path="/other")

    assert resp.status == 404


@pytest.mark.parametrize("path", ["/abcdef12", "/pergam", "/abcdef12/view"])
def test_delete_is_not_allowed(client: Client, path: str) -> None:
    assert client.request("DELETE", path).status == 405


def test_post_without_tags_writes_no_metadata(client: Client, db: psycopg.Connection) -> None:
    body = client.publish()

    assert "tags" not in body
    assert db.execute("SELECT count(*) FROM pergam_meta").fetchone() == (0,)


def test_post_with_tags_returns_them_normalised(client: Client, db: psycopg.Connection) -> None:
    body = client.publish(tags=["Q3 Review", "ops", "OPS", " "])

    assert body["tags"] == ["ops", "q3-review"]
    assert db.execute("SELECT tags FROM pergam_meta WHERE id = %s", (body["id"],)).fetchone() == (["ops", "q3-review"],)


def test_tags_from_two_versions_are_merged(client: Client) -> None:
    first = client.publish(tags=["ops", "draft"])
    second = client.publish(id=first["id"], tags=["release"])
    third = client.publish(id=first["id"])

    assert second["tags"] == ["draft", "ops", "release"]
    assert "tags" not in third
    assert client.publish(id=first["id"], tags=[])["tags"] == ["draft", "ops", "release"]


def test_null_tags_are_ignored(client: Client, db: psycopg.Connection) -> None:
    body = client.publish(tags=None)

    assert "tags" not in body
    assert db.execute("SELECT count(*) FROM pergam_meta").fetchone() == (0,)


@pytest.mark.parametrize("tags", ["ops", [1, 2], {"ops": True}, ["x" * 41]], ids=["string", "numbers", "object", "too-long"])
def test_invalid_tags_are_rejected(client: Client, db: psycopg.Connection, tags: object) -> None:
    resp = client.post_json({"title": "x", "html": "<p>x</p>", "type": "plan", "author": "you@example.com", "tags": tags})

    assert resp.status == 400
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_tag_limit_overflow_rolls_back_the_version(client: Client, db: psycopg.Connection) -> None:
    first = client.publish(tags=[f"t{i:02d}" for i in range(20)])

    resp = client.post_json(
        {"id": first["id"], "title": "x", "html": "<p>x</p>", "type": "plan", "author": "you@example.com", "tags": ["extra"]}
    )

    assert resp.status == 400
    assert db.execute("SELECT max(version) FROM pergams WHERE id = %s", (first["id"],)).fetchone() == (1,)
