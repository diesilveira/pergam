from __future__ import annotations

import re

import pytest

from pergam.diff import diff_sections
from support import Client

V1 = (
    "<p>Lead paragraph.</p><h1>Plan</h1><p>Intro stays.</p>"
    "<h2>Costs</h2><p>We spend 10 dollars a month.</p>"
    "<h2>Gone</h2><p>Removed later.</p>"
    "<h2>Risks</h2><p>None known.</p>"
    "<pre># not a heading</pre>"
)
V2 = (
    "<h1>Plan</h1><p>Intro stays.</p>"
    "<h2>Budget</h2><p>We spend 12 dollars a month.</p>"
    "<h2>Risks</h2><p>None known.</p><p>A new risk.</p>"
    "<pre># not a heading</pre>"
)


def summary(old: str, new: str) -> list[tuple[str | None, int, int]]:
    return [(section.heading, section.added, section.removed) for section in diff_sections(old, new)]


def test_sections_group_additions_and_removals_by_heading() -> None:
    assert summary(V1, V2) == [
        (None, 0, 1),
        ("Plan", 0, 0),
        ("Budget", 2, 2),
        ("Gone", 0, 2),
        ("Risks", 1, 0),
    ]


def test_replaced_lines_pair_up_and_mark_the_changed_words() -> None:
    budget = diff_sections(V1, V2)[2]

    paragraph = [line for line in budget.lines if "dollars" in line.text]
    assert [(line.kind, [s.text for s in line.segments if s.changed]) for line in paragraph] == [
        ("removed", ["10"]),
        ("added", ["12"]),
    ]


def test_heading_lines_inside_code_fences_do_not_open_sections() -> None:
    assert [heading for heading, _, _ in summary(V1, V2)].count("not a heading") == 0


def test_identical_versions_have_no_changes() -> None:
    assert all(added == removed == 0 for _, added, removed in summary(V1, V1))


def test_markup_only_changes_do_not_show() -> None:
    assert summary("<p>Same <b>text</b></p>", '<div class="x"><p>Same text</p></div>') == [(None, 0, 0)]


def publish_pair(client: Client) -> str:
    pid = client.publish(title="Plan", html=V1, note="first draft")["id"]
    client.publish(id=pid, title="Plan", html="<h1>Plan</h1><p>middle</p>")
    client.publish(id=pid, title="Plan", html=V2, note="renames costs to budget")
    return pid


def test_diff_page_highlights_changes_per_section(client: Client) -> None:
    pid = publish_pair(client)

    resp = client.get(f"/{pid}/diff/v1..v3")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert resp.headers["cache-control"] == "public, max-age=300"
    page = resp.text
    assert "<title>Plan · v1 → v3 · pergam</title>" in page
    assert f'href="/{pid}/v1/view"><span class="ver__n">v1</span><span class="note">first draft</span>' in page
    assert f'href="/{pid}/v3/view"' in page
    assert '<span class="plus">+3</span> <span class="minus">−5</span> lines · 4 of 5 sections changed' in page
    assert "<del>10</del>" in page
    assert "<ins>12</ins>" in page
    assert re.search(r'<div class="line line--added">.*?<ins>A new risk\.</ins>', page)
    assert '<details class="sec sec--same"><summary><span class="sec__name">Plan</span>' in page


def test_diff_escapes_document_text(client: Client) -> None:
    pid = client.publish(html="<p>a</p>")["id"]
    client.publish(id=pid, html="<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")

    page = client.get(f"/{pid}/diff/v1..v2").text

    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_diff_of_a_version_with_itself_reports_no_differences(client: Client) -> None:
    pid = publish_pair(client)

    assert "No differences in the text." in client.get(f"/{pid}/diff/v2..v2").text


def test_diff_cuts_long_unchanged_runs_to_context(client: Client) -> None:
    body = "".join(f"<p>line {i}</p>" for i in range(20))
    pid = client.publish(html=body)["id"]
    client.publish(id=pid, html=body.replace("<p>line 10</p>", "<p>line ten</p>"))

    page = client.get(f"/{pid}/diff/v1..v2").text

    assert page.count('class="gap"') == 2
    assert "… 8 unchanged lines" in page
    assert "… 7 unchanged lines" in page


@pytest.mark.parametrize("path", ["/{pid}/diff/v1..v9", "/{pid}/diff/v9..v1", "/abcdef12/diff/v1..v2"])
def test_unknown_versions_are_not_found(client: Client, path: str) -> None:
    pid = publish_pair(client)

    assert client.get(path.format(pid=pid)).status == 404


@pytest.mark.parametrize(
    "version_range", ["v1-v2", "1..2", "v1..", "v0..v2", "v01..v2", "v1...v2", "vx..v2", "v1..v2..v3", "latest"]
)
def test_malformed_ranges_are_bad_requests(client: Client, version_range: str) -> None:
    pid = publish_pair(client)

    assert client.get(f"/{pid}/diff/{version_range}").status == 400


def test_huge_version_numbers_are_not_found(client: Client) -> None:
    pid = publish_pair(client)

    assert client.get(f"/{pid}/diff/v1..v{'9' * 30}").status == 404


def test_view_links_to_the_changes_and_to_what_changed_since(client: Client) -> None:
    pid = publish_pair(client)

    first, middle = client.get(f"/{pid}/v1/view").text, client.get(f"/{pid}/v2/view").text

    assert ">changes</a>" not in first
    assert f'<a href="/{pid}/diff/v1..v3">what changed since</a>' in first
    assert f'<a href="/{pid}/diff/v1..v2" title="diff with v1">changes</a>' in middle
