"""Who is writing: the reverse proxy's `X-Forwarded-User` when `TRUST_PROXY_USER` is on.

With the flag on, the header names the author of every write and the body's
`author` is ignored, so nobody can publish under someone else's name. The flag
is only safe behind a proxy that authenticates every request, sets the header
itself and drops any copy a client sends, with pergam reachable through that
proxy alone. With the flag off the header is never read and `author` comes from
the body, as before.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

PROXY_USER_HEADER = "X-Forwarded-User"
MAX_USER_LENGTH = 320
_ON = frozenset({"1", "true", "yes", "on"})
_OFF = frozenset({"", "0", "false", "no", "off"})


class IdentityError(ValueError):
    """The request cannot be attributed: `status` is 401 without a user, 400 for a malformed one."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def trusts_proxy_user(env: Mapping[str, str]) -> bool:
    """`TRUST_PROXY_USER` as a boolean; a value that is neither on nor off raises `ValueError`.

    A typo must stop the server rather than silently leave identity untrusted.
    """
    raw = env.get("TRUST_PROXY_USER", "").strip().lower()
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    raise ValueError(f"TRUST_PROXY_USER must be one of 1/true/yes/on or 0/false/no/off, got {raw!r}")


def _is_control(char: str) -> bool:
    return unicodedata.category(char) in ("Cc", "Cs")


def parse_author(value: object) -> str:
    """A body `author`, trimmed, `""` when absent; raises `ValueError` when it is not a string or holds a control character.

    An author ends up in HTML, JSON, feeds and error pages, where a line break or
    other control character has no business.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("author must be a string")
    if any(_is_control(char) for char in value.strip()):
        raise ValueError("author must not contain control characters")
    return value.strip()


def parse_user(text: str) -> str:
    """A user name trimmed; raises `IdentityError` when it is blank, too long or has control characters."""
    user = text.strip()
    if not user:
        raise IdentityError(401, f"Missing {PROXY_USER_HEADER}")
    if len(user) > MAX_USER_LENGTH or any(_is_control(char) for char in user):
        raise IdentityError(400, f"Invalid {PROXY_USER_HEADER}")
    return user


def proxy_user(value: str | None) -> str:
    """The user the header names, by `parse_user`; a missing header is a 401 `IdentityError`.

    `http.server` decodes header bytes as Latin-1; they are read back as UTF-8, what proxies send.
    """
    try:
        return parse_user((value or "").encode("latin-1").decode("utf-8"))
    except UnicodeError:
        raise IdentityError(400, f"Invalid {PROXY_USER_HEADER}") from None


def single_proxy_user(values: list[str]) -> str:
    """The user of a request's `X-Forwarded-User` headers, which must be at most one.

    A proxy that appends its header after a client's copy instead of replacing it
    would leave the client's first; refusing duplicates keeps that copy from counting.
    """
    if len(values) > 1:
        raise IdentityError(400, f"{PROXY_USER_HEADER} sent more than once")
    return proxy_user(values[0] if values else None)


@dataclass(frozen=True)
class Writer:
    """Who sends a write: `user` is the trusted proxy user, None when identity is not trusted."""

    user: str | None = None
