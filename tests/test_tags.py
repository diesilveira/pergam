from __future__ import annotations

import pytest

from pergam.tags import MAX_TAG_LENGTH, MAX_TAGS, TagError, merge_tags, normalise_tag, parse_tags


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("release", "release"),
        ("  Q3 Review  ", "q3-review"),
        ("snake_case_tag", "snake-case-tag"),
        ("tabs\tand\nnewlines", "tabs-and-newlines"),
        ("--edge--", "edge"),
        ("a - _ b", "a-b"),
        ("ÑANDÚ", "ñandú"),
        ("   ", ""),
    ],
)
def test_normalise_tag(raw: str, expected: str) -> None:
    assert normalise_tag(raw) == expected


def test_parse_tags_drops_empties_dedupes_and_sorts() -> None:
    assert parse_tags(["Ops", "infra", "ops", " ", "INFRA", "on_call"]) == ["infra", "on-call", "ops"]


@pytest.mark.parametrize("value", ["ops", {"a": 1}, ["ok", 3], [None], 7])
def test_parse_tags_rejects_non_string_arrays(value: object) -> None:
    with pytest.raises(TagError):
        parse_tags(value)


def test_parse_tags_rejects_overlong_tags() -> None:
    assert parse_tags(["x" * MAX_TAG_LENGTH]) == ["x" * MAX_TAG_LENGTH]
    with pytest.raises(TagError):
        parse_tags(["x" * (MAX_TAG_LENGTH + 1)])


def test_merge_tags_is_a_sorted_union() -> None:
    assert merge_tags(["ops", "beta"], ["alpha", "ops"]) == ["alpha", "beta", "ops"]


def test_merge_tags_enforces_the_limit() -> None:
    full = [f"t{i:02d}" for i in range(MAX_TAGS)]

    assert merge_tags(full, ["t00"]) == full
    with pytest.raises(TagError):
        merge_tags(full, ["one-more"])


@pytest.mark.parametrize("raw", ["bell\x07", "nul\x00", "esc\x1b[0m", "del\x7f", "c1\x80x", "nonchar\uffff"])
def test_parse_tags_rejects_control_characters(raw: str) -> None:
    with pytest.raises(TagError, match="control characters"):
        parse_tags([raw])


def test_parse_tags_keeps_tabs_and_newlines_as_separators() -> None:
    assert parse_tags(["a\tb\nc"]) == ["a-b-c"]
