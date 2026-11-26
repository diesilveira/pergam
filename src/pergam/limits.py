"""Largest request body the server reads, from `PERGAM_MAX_BODY_MB`.

A body is read whole into memory before it is parsed, so a declared
`Content-Length` above the limit is refused before reading. The default leaves
room for `POST /import`, which carries every version of a pergam in one body.
"""

from __future__ import annotations

from collections.abc import Mapping

MIB = 1024 * 1024
DEFAULT_MAX_BODY_MB = 256


def max_body_bytes(env: Mapping[str, str]) -> int:
    raw = env.get("PERGAM_MAX_BODY_MB", "").strip()
    if not raw:
        return DEFAULT_MAX_BODY_MB * MIB
    try:
        megabytes = int(raw)
    except ValueError:
        raise ValueError(f"PERGAM_MAX_BODY_MB must be a positive integer, got {raw!r}") from None
    if megabytes <= 0:
        raise ValueError(f"PERGAM_MAX_BODY_MB must be a positive integer, got {raw!r}")
    return megabytes * MIB
