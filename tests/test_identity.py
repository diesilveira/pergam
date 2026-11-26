from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from pergam.identity import IdentityError, proxy_user, trusts_proxy_user
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
