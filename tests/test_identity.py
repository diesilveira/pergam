from __future__ import annotations

import http.client
import json
from pathlib import Path

import psycopg
import pytest

from pergam.identity import IdentityError, parse_author, proxy_user, single_proxy_user, trusts_proxy_user
from support import Client, ServerProcess


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, False), ("", False), ("0", False), ("off", False), ("1", True), (" TRUE ", True), ("yes", True), ("on", True)],
)
def test_trust_flag_values(raw: str | None, expected: bool) -> None:
    env = {} if raw is None else {"TRUST_PROXY_USER": raw}

    assert trusts_proxy_user(env) is expected


def test_trust_flag_typo_is_refused() -> None:
    with pytest.raises(ValueError, match="TRUST_PROXY_USER"):
        trusts_proxy_user({"TRUST_PROXY_USER": "ture"})


@pytest.mark.parametrize(("raw", "status"), [(None, 401), ("", 401), ("   ", 401), ("a\x07b", 400), ("x" * 321, 400)])
def test_unusable_proxy_user(raw: str | None, status: int) -> None:
    with pytest.raises(IdentityError) as info:
        proxy_user(raw)

    assert info.value.status == status


def test_proxy_user_is_trimmed_and_read_as_utf8() -> None:
    assert proxy_user(" ana@example.com ") == "ana@example.com"
    assert proxy_user("ñandú".encode().decode("latin-1")) == "ñandú"


def test_trusted_author_comes_from_the_header_not_the_body(trusting: Client, db: psycopg.Connection) -> None:
    body = trusting.as_user("ana@example.com").publish(author="someone-else@example.com")

    assert body["author"] == "ana@example.com"
    assert db.execute("SELECT author FROM pergams WHERE id = %s", (body["id"],)).fetchone() == ("ana@example.com",)


def test_trusted_author_needs_no_body_author(trusting: Client) -> None:
    resp = trusting.as_user("ana@example.com").post_json({"title": "x", "html": "<p>x</p>", "type": "otro"})

    assert resp.status == 201
    assert resp.json()["author"] == "ana@example.com"


def test_trusted_publish_without_the_header_is_401_and_writes_nothing(
    trusting: Client, db: psycopg.Connection
) -> None:
    resp = trusting.post_json({"title": "x", "html": "<p>x</p>", "type": "otro", "author": "ana@example.com"})

    assert resp.status == 401
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_untrusted_server_ignores_the_header(client: Client) -> None:
    body = client.as_user("ana@example.com").publish(author="bo@example.com")

    assert body["author"] == "bo@example.com"


def test_server_refuses_an_unknown_trust_flag(session_database: str, server_logs: Path) -> None:
    server = ServerProcess(session_database, server_logs, {"TRUST_PROXY_USER": "maybe"})

    server.launch()
    try:
        assert server.wait_exit() != 0
    finally:
        server.stop()

    assert "TRUST_PROXY_USER" in server.log()


@pytest.mark.parametrize(("value", "expected"), [(None, ""), ("  ana@example.com ", "ana@example.com"), ("Ñandú 日本", "Ñandú 日本")])
def test_body_author_is_trimmed(value: object, expected: str) -> None:
    assert parse_author(value) == expected


@pytest.mark.parametrize("value", ["ana\r\nSet-Cookie: x=1", "a\tb", "a\x00b", 42, ["ana"]])
def test_body_author_with_control_characters_or_not_a_string_is_refused(value: object) -> None:
    with pytest.raises(ValueError):
        parse_author(value)


@pytest.mark.parametrize("trusted", [False, True], ids=["untrusted", "trusted"])
def test_publish_refuses_an_author_with_control_characters(
    client: Client, trusting: Client, db: psycopg.Connection, trusted: bool
) -> None:
    server = trusting.as_user("ana@example.com") if trusted else client

    resp = server.post_json({"title": "x", "html": "<p>x</p>", "type": "otro", "author": "ana\r\nX-Injected: 1"})

    assert resp.status == 400
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_import_refuses_an_author_with_control_characters(client: Client) -> None:
    version = {"version": 1, "title": "t", "html": "<p>t</p>", "type": "otro", "author": "ana\nbo",
               "created_at": "2025-01-01T00:00:00+00:00"}

    resp = client.post_json({"format_version": 1, "id": "0123abcd", "versions": [version]}, "/import")

    assert resp.status == 400
    assert "control characters" in resp.text


def test_a_repeated_proxy_user_header_is_refused() -> None:
    assert single_proxy_user(["ana@example.com"]) == "ana@example.com"
    with pytest.raises(IdentityError) as info:
        single_proxy_user(["eve@example.com", "ana@example.com"])

    assert info.value.status == 400


def test_trusted_publish_with_two_user_headers_is_400_and_writes_nothing(
    trusting: Client, db: psycopg.Connection
) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", trusting.port, timeout=10)
    try:
        conn.putrequest("POST", "/pergam")
        body = json.dumps({"title": "x", "html": "<p>x</p>", "type": "otro"}).encode()
        for name, value in [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                            ("X-Forwarded-User", "eve@example.com"), ("X-Forwarded-User", "ana@example.com")]:
            conn.putheader(name, value)
        conn.endheaders(body)
        status = conn.getresponse().status
    finally:
        conn.close()

    assert status == 400
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_import_compares_but_does_not_refuse_a_stored_author_that_predates_the_rule(
    client: Client, db: psycopg.Connection
) -> None:
    legacy = "ana\r\nexample"
    db.execute(
        "INSERT INTO pergams (id, version, title, html, type, author, bytes, created_at)"
        " VALUES ('0123abcd', 1, 't', '<p>t</p>', 'otro', %s, 8, '2025-01-01T00:00:00+00:00')",
        (legacy,),
    )
    exported = client.get("/0123abcd/export").json()
    added = {**exported["versions"][0], "version": 2, "html": "<p>u</p>", "author": "bo@example.com",
             "created_at": "2025-01-02T00:00:00+00:00"}
    bad = {**added, "version": 3, "author": "eve\nexample", "created_at": "2025-01-03T00:00:00+00:00"}

    accepted = client.post_json({**exported, "versions": [*exported["versions"], added]}, "/import")
    refused = client.post_json({**exported, "versions": [*exported["versions"], added, bad]}, "/import")

    assert accepted.status == 201
    assert accepted.json()["imported"] == [2] and accepted.json()["skipped"] == [1]
    assert refused.status == 400
    assert "v3: author must not contain control characters" in refused.text
    assert db.execute("SELECT count(*) FROM pergams WHERE id = '0123abcd'").fetchone() == (2,)
