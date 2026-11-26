from __future__ import annotations

import pytest

from support import in_ci, unavailable


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"CI": ""}, False),
        ({"CI": "false"}, False),
        ({"CI": "0"}, False),
        ({"CI": "true"}, True),
        ({"CI": "1"}, True),
    ],
)
def test_in_ci(env: dict[str, str], expected: bool) -> None:
    assert in_ci(env) is expected


def test_missing_tool_skips_outside_ci() -> None:
    with pytest.raises(pytest.skip.Exception, match="no browser"):
        unavailable("no browser", {})


def test_missing_tool_fails_in_ci() -> None:
    with pytest.raises(pytest.fail.Exception, match="no browser; CI runs every test"):
        unavailable("no browser", {"CI": "true"})
