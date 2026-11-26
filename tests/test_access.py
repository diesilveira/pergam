from __future__ import annotations

import html
import http.client
import json
from datetime import datetime, timezone

import psycopg
import pytest

from pergam.access import CollaboratorError, parse_collaborators
from support import Client, Response

OWNER = "ana@example.com"
COLLABORATOR = "bo@example.com"
STRANGER = "eve@example.com"


def send(client: Client, method: str, path: str, payload: object) -> Response:
    return client.request(method, path, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"})


def set_collaborators(client: Client, pergam_id: str, users: list[str]) -> Response:
    return send(client, "PUT", f"/{pergam_id}/collaborators", {"collaborators": users})


def version_count(db: psycopg.Connection, pergam_id: str) -> int:
    return db.execute("SELECT count(*) FROM pergams WHERE id = %s", (pergam_id,)).fetchone()[0]


def export_document(pergam_id: str, *authors: str) -> dict:
    return {
        "format_version": 1,
        "id": pergam_id,
        "server": "https://team.example.com",
        "versions": [
            {
                "version": n,
                "title": "Moved",
                "html": f"<p>v{n}</p>",
                "type": "otro",
                "author": author,
                "created_at": f"2025-01-0{n}T00:00:00+00:00",
            }
            for n, author in enumerate(authors, 1)
        ],
    }


@pytest.fixture
def owned(trusting: Client) -> str:
    pid = trusting.as_user(OWNER).publish()["id"]
    assert set_collaborators(trusting.as_user(OWNER), pid, [COLLABORATOR]).status == 200
    return pid


def test_collaborators_are_trimmed_deduplicated_and_sorted() -> None:
    assert parse_collaborators({"collaborators": [" bo@example.com", "ana@example.com", "bo@example.com "]}) == [
        "ana@example.com",
        "bo@example.com",
    ]


@pytest.mark.parametrize(
    "payload",
    [{}, {"collaborators": "bo"}, {"collaborators": [1]}, {"collaborators": [" "]}, {"collaborators": ["a\nb"]},
     {"collaborators": ["x" * 321]}, {"collaborators": [], "owner": "eve"}, {"collaborators": [f"u{i}" for i in range(101)]}],
    ids=["missing", "not-a-list", "not-strings", "blank", "control", "too-long", "unknown-field", "too-many"],
)
def test_malformed_collaborators_are_refused(payload: dict) -> None:
    with pytest.raises(CollaboratorError):
        parse_collaborators(payload)


def test_owner_is_the_author_of_v1(trusting: Client, owned: str) -> None:
    trusting.as_user(COLLABORATOR).publish(id=owned)

    resp = trusting.get(f"/{owned}/collaborators")

    assert resp.status == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json() == {"id": owned, "owner": OWNER, "collaborators": [COLLABORATOR]}


def test_versioning_someone_elses_pergam_is_403_and_writes_nothing(
    trusting: Client, owned: str, db: psycopg.Connection
) -> None:
    resp = trusting.as_user(STRANGER).post_json({"id": owned, "html": "<p>mine now</p>", "type": "otro"})

    assert resp.status == 403
    assert OWNER in resp.text and STRANGER in resp.text
    assert version_count(db, owned) == 1


def test_patching_someone_elses_pergam_is_403(trusting: Client, owned: str) -> None:
    resp = send(trusting.as_user(STRANGER), "PATCH", f"/{owned}/meta", {"archived": True})

    assert resp.status == 403
    assert trusting.get(f"/{owned}/meta").json()["archived"] is False


def test_collaborator_publishes_and_patches(trusting: Client, owned: str) -> None:
    collaborator = trusting.as_user(COLLABORATOR)

    assert collaborator.publish(id=owned)["version"] == 2
    assert send(collaborator, "PATCH", f"/{owned}/meta", {"pinned": True}).status == 200
    assert trusting.as_user(OWNER).publish(id=owned)["version"] == 3


def test_only_the_owner_edits_the_collaborators(trusting: Client, owned: str) -> None:
    assert set_collaborators(trusting.as_user(COLLABORATOR), owned, [COLLABORATOR, STRANGER]).status == 403
    assert set_collaborators(trusting.as_user(STRANGER), owned, [STRANGER]).status == 403

    resp = set_collaborators(trusting.as_user(OWNER), owned, [])

    assert resp.json() == {"id": owned, "owner": OWNER, "collaborators": []}
    assert trusting.as_user(COLLABORATOR).post_json({"id": owned, "html": "<p>x</p>", "type": "otro"}).status == 403


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/pergam", {"html": "<p>x</p>", "type": "otro", "author": OWNER}),
        ("PATCH", "/{id}/meta", {"pinned": True}),
        ("PUT", "/{id}/collaborators", {"collaborators": []}),
        ("POST", "/import", export_document("0123abcd", OWNER)),
    ],
    ids=["publish", "patch", "collaborators", "import"],
)
def test_trusted_writes_without_a_user_are_401(
    trusting: Client, owned: str, db: psycopg.Connection, method: str, path: str, payload: dict
) -> None:
    resp = send(trusting, method, path.format(id=owned), payload)

    assert resp.status == 401
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (1,)
    assert trusting.get(f"/{owned}/collaborators").json()["collaborators"] == [COLLABORATOR]


def test_collaborators_of_an_unknown_id_are_404(trusting: Client) -> None:
    assert trusting.get("/0123abcd/collaborators").status == 404
    assert set_collaborators(trusting.as_user(OWNER), "0123abcd", []).status == 404


def test_malformed_collaborators_body_is_400(trusting: Client, owned: str) -> None:
    resp = send(trusting.as_user(OWNER), "PUT", f"/{owned}/collaborators", {"collaborators": ["a\x00b"]})

    assert resp.status == 400


def test_import_creating_an_id_writes_only_versions_by_the_user(trusting: Client, db: psycopg.Connection) -> None:
    refused = trusting.as_user(STRANGER).post_json(export_document("0123abcd", OWNER), "/import")
    mixed = trusting.as_user(OWNER).post_json(export_document("0123abcd", OWNER, COLLABORATOR), "/import")

    assert refused.status == 403
    assert f"v1 of 0123abcd is by {OWNER}: an import writes only versions by the signed-in user ({STRANGER})" in refused.text
    assert mixed.status == 403
    assert f"v2 of 0123abcd is by {COLLABORATOR}" in mixed.text
    assert version_count(db, "0123abcd") == 0
    assert trusting.as_user(OWNER).post_json(export_document("0123abcd", OWNER, OWNER), "/import").status == 201
    assert trusting.get("/0123abcd/collaborators").json()["owner"] == OWNER


def with_version(trusting: Client, pergam_id: str, author: str) -> dict:
    present = trusting.get(f"/{pergam_id}/export").json()
    added = {**present["versions"][0], "version": 2, "html": "<p>v2</p>", "author": author,
             "created_at": datetime.now(timezone.utc).isoformat()}
    return {**present, "versions": [*present["versions"], added]}


def test_import_into_an_existing_id_needs_an_editor(trusting: Client, owned: str) -> None:
    assert trusting.as_user(STRANGER).post_json(with_version(trusting, owned, STRANGER), "/import").status == 403
    assert trusting.as_user(COLLABORATOR).post_json(with_version(trusting, owned, COLLABORATOR), "/import").json()["imported"] == [2]


@pytest.mark.parametrize(("author", "status"), [(COLLABORATOR, 201), (OWNER, 201), (STRANGER, 403)])
def test_import_into_an_existing_id_writes_only_versions_by_its_editors(
    trusting: Client, owned: str, author: str, status: int
) -> None:
    resp = trusting.as_user(OWNER).post_json(with_version(trusting, owned, author), "/import")

    assert resp.status == status
    if status == 403:
        assert f"v2 of {owned} is by {STRANGER}: an import writes only versions by the signed-in user, the owner or a collaborator" in resp.text


def test_without_trusted_identity_nothing_is_enforced(client: Client) -> None:
    pid = client.publish(author=OWNER)["id"]

    assert client.publish(id=pid, author=STRANGER)["version"] == 2
    assert send(client, "PATCH", f"/{pid}/meta", {"archived": True}).status == 200
    assert client.post_json(export_document("0123abcd", OWNER), "/import").status == 201


def test_without_trusted_identity_collaborators_cannot_be_seeded(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish(author=OWNER)["id"]

    resp = set_collaborators(client.as_user(OWNER), pid, [STRANGER])

    assert resp.status == 409
    assert "TRUST_PROXY_USER" in resp.text
    assert client.get(f"/{pid}/collaborators").json() == {"id": pid, "owner": OWNER, "collaborators": []}
    assert db.execute("SELECT count(*) FROM pergam_meta WHERE collaborators IS NOT NULL").fetchone() == (0,)


@pytest.mark.parametrize("owner", ["ana@example.com\r\nSet-Cookie: injected=1", "日本@example.com"], ids=["crlf", "non-latin-1"])
def test_refusals_keep_stored_names_out_of_the_status_line(
    trusting: Client, db: psycopg.Connection, owner: str
) -> None:
    db.execute(
        "INSERT INTO pergams (id, version, title, html, type, author, bytes) VALUES ('0123abcd', 1, 'x', '<p>x</p>', 'otro', %s, 8)",
        (owner,),
    )
    conn = http.client.HTTPConnection("127.0.0.1", trusting.port, timeout=10)
    try:
        conn.request("PATCH", "/0123abcd/meta", b'{"pinned": true}', {"Content-Type": "application/json", "X-Forwarded-User": STRANGER})
        resp = conn.getresponse()
        body = resp.read().decode()
    finally:
        conn.close()

    assert (resp.status, resp.reason) == (403, "Forbidden")
    assert resp.getheader("Set-Cookie") is None
    assert html.escape(owner, quote=False) in body
