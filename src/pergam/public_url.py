"""How the server names itself in returned `view_url`s and on the index page.

`PUBLIC_URL` (e.g. `https://docs.example.com`) is the base of every returned URL.
Without it the base is built from `PUBLIC_HOST` and `PUBLIC_PORT`, where port 443
means https and ports 80/443 are left out of the authority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

_CSP_ORIGIN = re.compile(r"^https?://[a-z0-9.-]+(:\d+)?$")


@dataclass(frozen=True)
class PublicAddress:
    base_url: str
    display: str

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def view_url(self, pergam_id: str) -> str:
        return self.url(f"{pergam_id}/view")

    @property
    def origin(self) -> str | None:
        """`scheme://host[:port]` of the base URL as a CSP source, None when its host cannot be named in one (IPv6)."""
        parsed = urlparse(self.base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        return origin if _CSP_ORIGIN.match(origin) else None


def from_public_url(raw: str) -> PublicAddress:
    """Validate `PUBLIC_URL`; credentials are rejected without echoing the value."""
    if "@" in raw:
        raise ValueError("PUBLIC_URL must not contain credentials or '@'")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"PUBLIC_URL must be an absolute http(s) URL, got {raw!r}")
    if "?" in raw or "#" in raw or parsed.params:
        raise ValueError(f"PUBLIC_URL must not contain a query, fragment or parameters, got {raw!r}")
    try:
        parsed.port
    except ValueError:
        raise ValueError(f"PUBLIC_URL has an invalid port, got {raw!r}") from None
    if parsed.netloc.endswith(":"):
        raise ValueError(f"PUBLIC_URL has an empty port, got {raw!r}")
    base = raw.rstrip("/")
    return PublicAddress(base_url=base, display=(parsed.netloc + parsed.path).rstrip("/"))


def from_host_port(host: str, port_text: str) -> PublicAddress:
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f"PUBLIC_PORT must be an integer, got {port_text!r}") from None
    scheme = "https" if port == 443 else "http"
    authority = host if port in (80, 443) else f"{host}:{port}"
    return PublicAddress(base_url=f"{scheme}://{authority}", display=authority)


def resolve(env: Mapping[str, str], server_port: int) -> PublicAddress:
    public_url = env.get("PUBLIC_URL", "").strip()
    if public_url:
        return from_public_url(public_url)
    return from_host_port(env.get("PUBLIC_HOST") or "localhost", env.get("PUBLIC_PORT") or str(server_port))
