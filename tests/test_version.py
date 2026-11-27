from __future__ import annotations

import re

from pergam import __version__
from support import Client


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_server_header_names_the_package_version(client: Client) -> None:
    for path in ("/healthz", "/"):
        assert client.get(path).headers["server"].startswith(f"pergam/{__version__} ")


def test_healthz_body_is_unchanged(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status == 200
    assert response.body == b'{"ok":true}'
