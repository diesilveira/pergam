from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from pergam.limits import DEFAULT_MAX_BODY_MB, MIB, max_body_bytes
from support import Client, ServerProcess


@pytest.mark.parametrize(("env", "expected"), [({}, DEFAULT_MAX_BODY_MB * MIB), ({"PERGAM_MAX_BODY_MB": " 3 "}, 3 * MIB)])
def test_max_body_bytes(env: dict[str, str], expected: int) -> None:
    assert max_body_bytes(env) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "lots"])
def test_max_body_bytes_rejects_bad_values(raw: str) -> None:
    with pytest.raises(ValueError, match="PERGAM_MAX_BODY_MB"):
        max_body_bytes({"PERGAM_MAX_BODY_MB": raw})


def test_body_above_the_limit_is_413_and_publishes_nothing(
    session_database: str, db: psycopg.Connection, start_server
) -> None:
    small = start_server(session_database, PERGAM_MAX_BODY_MB="1")
    body = json.dumps({"title": "Doc", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"}).encode()

    resp = small.request(
        "POST", "/pergam", body, {"Content-Type": "application/json", "Content-Length": str(MIB + 1)}
    )

    assert resp.status == 413
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_body_at_the_limit_is_read(session_database: str, db: psycopg.Connection, start_server) -> None:
    small = start_server(session_database, PERGAM_MAX_BODY_MB="1")
    payload = {"title": "Doc", "html": "", "type": "otro", "author": "you@example.com"}
    payload["html"] = "x" * (MIB - len(json.dumps(payload)))

    resp = small.post_json(payload)

    assert resp.status == 201


def test_malformed_content_length_is_400(client: Client) -> None:
    resp = client.request("POST", "/pergam", b"{}", {"Content-Type": "application/json", "Content-Length": "abc"})

    assert resp.status == 400


def test_server_refuses_an_invalid_limit(session_database: str, server_logs: Path) -> None:
    server = ServerProcess(session_database, server_logs, {"PERGAM_MAX_BODY_MB": "none"})

    server.launch()
    try:
        assert server.wait_exit() != 0
    finally:
        server.stop()

    assert "PERGAM_MAX_BODY_MB" in server.log()
