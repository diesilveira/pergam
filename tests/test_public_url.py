from __future__ import annotations

from pathlib import Path

import pytest

from pergam.public_url import resolve
from support import Client, ServerProcess


@pytest.mark.parametrize(
    ("env", "view_url", "display"),
    [
        ({"PUBLIC_URL": "https://docs.example.com"}, "https://docs.example.com/abc123/view", "docs.example.com"),
        ({"PUBLIC_URL": "https://docs.example.com/"}, "https://docs.example.com/abc123/view", "docs.example.com"),
        ({"PUBLIC_URL": "http://docs.example.com:8080/pergam/"}, "http://docs.example.com:8080/pergam/abc123/view", "docs.example.com:8080/pergam"),
        ({"PUBLIC_URL": "https://docs.example.com", "PUBLIC_HOST": "other.example.com", "PUBLIC_PORT": "443"}, "https://docs.example.com/abc123/view", "docs.example.com"),
        ({"PUBLIC_URL": "  ", "PUBLIC_HOST": "docs.example.com"}, "http://docs.example.com:1111/abc123/view", "docs.example.com:1111"),
        ({}, "http://localhost:1111/abc123/view", "localhost:1111"),
        ({"PUBLIC_HOST": "docs.example.com", "PUBLIC_PORT": "443"}, "https://docs.example.com/abc123/view", "docs.example.com"),
        ({"PUBLIC_HOST": "docs.example.com", "PUBLIC_PORT": "80"}, "http://docs.example.com/abc123/view", "docs.example.com"),
        ({"PUBLIC_HOST": "docs.example.com", "PUBLIC_PORT": "8080"}, "http://docs.example.com:8080/abc123/view", "docs.example.com:8080"),
    ],
)
def test_resolve(env: dict[str, str], view_url: str, display: str) -> None:
    address = resolve(env, 1111)

    assert address.view_url("abc123") == view_url
    assert address.display == display


@pytest.mark.parametrize(
    "env",
    [
        {"PUBLIC_URL": "docs.example.com"},
        {"PUBLIC_URL": "ftp://docs.example.com"},
        {"PUBLIC_URL": "https://docs.example.com/?x=1"},
        {"PUBLIC_URL": "https://"},
        {"PUBLIC_URL": "https://docs.example.com?"},
        {"PUBLIC_URL": "https://docs.example.com/#"},
        {"PUBLIC_URL": "https://docs.example.com:https"},
        {"PUBLIC_URL": "https://docs.example.com:99999"},
        {"PUBLIC_URL": "https://docs.example.com:"},
        {"PUBLIC_URL": "https://user:secret@docs.example.com"},
        {"PUBLIC_URL": "https://user@docs.example.com"},
        {"PUBLIC_URL": "user:secret@docs.example.com"},
        {"PUBLIC_PORT": "https"},
    ],
)
def test_resolve_rejects_invalid_configuration(env: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="PUBLIC_"):
        resolve(env, 1111)


@pytest.mark.parametrize("raw", ["https://user:secret@docs.example.com", "user:secret@docs.example.com"])
def test_rejection_does_not_echo_credentials(raw: str) -> None:
    with pytest.raises(ValueError) as info:
        resolve({"PUBLIC_URL": raw}, 1111)

    assert "secret" not in str(info.value)


def test_server_uses_public_url(session_database: str, client: Client, start_server) -> None:
    public = start_server(session_database, PUBLIC_URL="https://docs.example.com", PUBLIC_HOST="ignored.example.com")

    body = public.publish()

    assert body["view_url"] == f"https://docs.example.com/{body['id']}/view"
    assert "</span>docs.example.com</span>" in public.get("/").text


def test_server_falls_back_to_public_host_and_port(session_database: str, client: Client, start_server) -> None:
    public = start_server(session_database, PUBLIC_HOST="docs.example.com", PUBLIC_PORT="443")

    body = public.publish()

    assert body["view_url"] == f"https://docs.example.com/{body['id']}/view"


def test_server_refuses_invalid_public_url(session_database: str, server_logs: Path) -> None:
    server = ServerProcess(session_database, server_logs, {"PUBLIC_URL": "docs.example.com"})

    server.launch()
    try:
        assert server.wait_exit() != 0
    finally:
        server.stop()

    assert "PUBLIC_URL" in server.log()
