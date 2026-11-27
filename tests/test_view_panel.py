"""The view page's bar actions, outdated notice and history panel, as served."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from pergam.meta import Meta, Origin
from pergam.store import VersionInfo
from pergam.view_page import render_view
from pergam.view_panel import ago
from support import Client

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


def version(n: int, author: str = "ana@example.com", note: str | None = None, age: timedelta = timedelta()) -> VersionInfo:
    return VersionInfo(n, "Doc", "plan", author, 10, NOW - age, note)


@pytest.mark.parametrize(
    ("age", "words"),
    [
        (timedelta(seconds=59), "just now"),
        (timedelta(minutes=1), "1 minute ago"),
        (timedelta(minutes=12), "12 minutes ago"),
        (timedelta(hours=5), "5 hours ago"),
        (timedelta(days=6), "6 days ago"),
        (timedelta(days=65), "2 months ago"),
        (timedelta(days=800), "2 years ago"),
    ],
)
def test_ages_read_as_words(age: timedelta, words: str) -> None:
    assert ago(NOW - age, NOW) == words


def test_panel_lists_every_version_with_note_author_date_and_a_compare_with_current() -> None:
    versions = [
        version(3, note="Adds the rollback plan", age=timedelta(minutes=12)),
        version(2, author="bo@example.com", note="Moves the window", age=timedelta(days=2)),
        version(1, age=timedelta(days=6)),
    ]

    page = render_view("abcdef12", versions, Meta([], False, False), 1, now=NOW)

    items = re.findall(r'<li class="history__item[^"]*">(.*?)</li>', page)
    assert len(items) == 3
    assert '<a class="history__link" href="/abcdef12/v3/view">' in items[0]
    assert '<span class="history__latest">current</span>' in items[0]
    assert "Adds the rollback plan" in items[0] and "12 minutes ago" in items[0]
    assert "history__compare" not in items[0]
    assert "bo@example.com" in items[1] and "2 days ago" in items[1]
    assert '<a class="history__compare" href="/abcdef12/diff/v2..v3">' in items[1]
    assert '<a class="history__link" href="/abcdef12/v1/view" aria-current="page">' in items[2]
    assert re.findall(r'<li class="(history__item[^"]*)">', page) == [
        "history__item", "history__item", "history__item history__item--shown",
    ]
    assert f'<time datetime="{(NOW - timedelta(days=6)).isoformat()}" title="2026-03-04 12:00 UTC">6 days ago</time>' in items[2]


def test_outdated_notice_says_when_the_version_was_published() -> None:
    versions = [version(2), version(1, age=timedelta(days=6))]

    page = render_view("abcdef12", versions, Meta([], False, False), 1, now=NOW)

    notice = re.search(r'<p class="outdated" role="status">(.*?)</p>', page).group(1)
    assert re.sub(r"<[^>]+>", "", notice) == (
        "You are viewing v1, published 6 days ago. Go to the current version (v2) See what changed since"
    )
    assert '<a href="/abcdef12/diff/v1..v2">See what changed since</a>' in notice


def test_details_without_access_name_the_first_author_and_no_collaborators() -> None:
    versions = [version(2, author="bo@example.com"), version(1)]

    page = render_view("abcdef12", versions, Meta([], False, False), 2, now=NOW)

    facts = dict(re.findall(r"<div><dt>(.*?)</dt><dd>(.*?)</dd></div>", page))
    assert facts == {
        "Type": '<span class="badge" style="--type-rgb:240,210,138">plan</span>',
        "Tags": '<span class="facts__tags" data-tags></span>',
        "Author": "ana@example.com",
        "Collaborators": '<span class="muted">none</span>',
        "Origin": "published here",
        "Id": "<code>abcdef12</code>",
    }


def test_details_name_the_owner_collaborators_and_origin(trusting: Client) -> None:
    owner = trusting.as_user("ana@example.com")
    pid = owner.publish(title="Doc")["id"]
    put = owner.request("PUT", f"/{pid}/collaborators", b'{"collaborators":["bo@example.com","<c>@example.com"]}',
                        {"Content-Type": "application/json"})
    assert put.status == 200
    trusting.as_user("bo@example.com").publish(id=pid)

    page = trusting.as_user("bo@example.com").get(f"/{pid}/view").text

    facts = dict(re.findall(r"<div><dt>(.*?)</dt><dd>(.*?)</dd></div>", page))
    assert facts["Author"] == "ana@example.com"
    assert facts["Collaborators"] == "&lt;c&gt;@example.com, bo@example.com"


def test_details_link_the_origin_of_an_imported_pergam() -> None:
    meta = Meta([], False, False, Origin("https://pergam.example.com", "0123abcd"))

    page = render_view("0123abcd", [version(1)], meta, 1, now=NOW)

    assert (
        '<dd><span class="bar__origin">imported from <a href="https://pergam.example.com/0123abcd/view" '
        'rel="noreferrer" title="https://pergam.example.com/0123abcd/view">pergam.example.com/0123abcd</a></span></dd>'
    ) in page


def test_panel_escapes_notes_and_authors() -> None:
    versions = [version(2, author="<i>x</i>@example.com", note="<script>alert(1)</script>"), version(1)]

    page = render_view("abcdef12", versions, Meta([], False, False), 2, now=NOW)

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;i&gt;x&lt;/i&gt;@example.com" in page


def test_bar_has_a_breadcrumb_buttons_and_a_menu_for_the_rest(client: Client) -> None:
    pid = client.publish(title="Doc", html="<h1>one</h1>")["id"]
    client.publish(id=pid, title="Doc", html="<h1>two</h1>")

    page = client.get(f"/{pid}/view").text

    assert '<nav aria-label="Breadcrumb"><a href="/">Index</a></nav>' in page
    header = re.search(r'<header class="topbar">(.*?)</header>', page).group(1)
    bar = re.sub(r'<details class="dropdown more">.*?</details>', "", header)
    for gone in ('class="badge"', 'class="tag"', "Compare", ">Text<", ">Archive<", "Edit tags", "bar__print"):
        assert gone not in bar
    menu = re.search(r'<details class="dropdown more">(.*?)</details>', page).group(1)
    assert '<summary class="icon-btn dropdown__toggle" aria-label="More actions"' in menu
    assert re.findall(r'<li><(?:a href="([^"]+)"[^>]*|button type="button" class="dropdown__item[^"]*"[^>]*)>([^<]+)<', menu) == [
        (f"/{pid}/diff/v1..v2", "Compare with v1"),
        (f"/{pid}/v2/text", "Text"),
        ("", "Print"),
        (f"/{pid}/v2/raw", "Raw HTML"),
        (f"/{pid}/export", "Export (JSON)"),
        (f"/{pid}/v2/content", "Open document"),
    ]
    assert (
        '<button type="button" class="btn" data-panel-toggle aria-controls="panel" aria-expanded="false" hidden>'
        "History</button>"
    ) in page
    assert '<aside class="panel" id="panel" aria-labelledby="panel-title">' in page


def test_view_page_loads_its_enhancements_in_the_head(client: Client) -> None:
    pid = client.publish()["id"]

    page = client.get(f"/{pid}/view").text
    src = re.search(r'<script src="(/static/pages\.js\?v=[0-9a-f]{12})"></script>', page)
    assert src and src.start() < page.index("<body")
    script = client.get(src.group(1))

    assert script.status == 200
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert not re.search(rb"['\"`]message['\"`]|onmessage", script.body)
