from __future__ import annotations

import pytest

from pergam.routes import escape_log


@pytest.mark.parametrize(
    ("raw", "logged"),
    [
        ("GET /x HTTP/1.1", "GET /x HTTP/1.1"),
        ("a\r\nforged line", "a\\x0d\\x0aforged line"),
        ("\x1b[31mred", "\\x1b[31mred"),
        ("del\x7f c1\x85\x9f", "del\\x7f c1\\x85\\x9f"),
        ("already \\x0a escaped", "already \\\\x0a escaped"),
        ("ñandú \xa0 日本", "ñandú \xa0 日本"),
    ],
    ids=["plain", "crlf", "escape-sequence", "del-and-c1", "backslash", "printable-non-ascii"],
)
def test_log_lines_escape_control_characters_and_backslashes(raw: str, logged: str) -> None:
    assert escape_log(raw) == logged
