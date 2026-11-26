"""How the server names itself in returned `view_url`s and on the index page."""

from __future__ import annotations

import os


def _public_host() -> str:
    return os.environ.get("PUBLIC_HOST", "localhost")


def _public_port(server_port: int) -> str:
    return os.environ.get("PUBLIC_PORT", str(server_port))


def _authority(host: str, port: str) -> str:
    return host if port in ("80", "443") else f"{host}:{port}"


def host_display(server_port: int) -> str:
    return _authority(_public_host(), _public_port(server_port))


def view_url(pergam_id: str, server_port: int) -> str:
    port = str(int(_public_port(server_port)))
    scheme = "https" if port == "443" else "http"
    return f"{scheme}://{_authority(_public_host(), port)}/{pergam_id}/view"
