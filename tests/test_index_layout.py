"""The index's sort orders, pinned cards, date groups, summaries and filter column."""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

import psycopg
import pytest

from pergam.cursor import IndexCursor, TitleCursor
from pergam.index_page import TAGS_SHOWN, date_group
from pergam.summary import SUMMARY_CHARS, summarise
from support import Client, crawl, listed_order, patch_meta, seed


def api_crawl(client: Client, query: str) -> list[str]:
    visited: list[str] = []
    path = f"/api/pergams{query}"
    for _ in range(10):
        body = client.get(path).json()
        visited.extend(row["id"] for row in body["pergams"])
        if not body["next_cursor"]:
            return visited
        path = f"/api/pergams{query}&cursor={body['next_cursor']}"
    raise AssertionError("too many pages")


@pytest.mark.parametrize("query", ["?sort=title", "?sort=title&archived=1", "?sort=title&tag=seeded&type=plan"])
def test_title_order_pages_every_pergam_once(client: Client, db: psycopg.Connection, query: str) -> None:
    flags = seed(db)
    archived = "archived=1" in query
    rows = db.execute(
        "SELECT l.id, l.type FROM pergams_latest l LEFT JOIN pergam_meta m USING (id) ORDER BY lower(l.title), l.id"
    ).fetchall()
    expected = [
        pid
        for pid, kind in rows
        if (archived or not flags[pid][1])
        and ("tag=seeded" not in query or (flags[pid][2] and kind == "plan"))
    ]

    from_api = api_crawl(client, query)
    from_page = crawl(client, f"/{query}")

    assert from_api == expected
    pinned = [pid for pid in expected if flags[pid][0]]
    assert from_page == pinned + [pid for pid in expected if pid not in pinned]


def test_title_order_is_case_insensitive_then_by_id(client: Client) -> None:
    ids = [client.publish(title=title)["id"] for title in ("banana", "Apple", "cherry", "apple")]

    order = [row["id"] for row in client.get("/api/pergams?sort=title").json()["pergams"]]

    assert order[:2] == sorted([ids[1], ids[3]])
    assert order[2:] == [ids[0], ids[2]]


@pytest.mark.parametrize("path", ["/", "/api/pergams"])
def test_a_cursor_from_one_order_is_rejected_in_the_other(client: Client, path: str) -> None:
    recent = IndexCursor(False, datetime(2025, 1, 1, tzinfo=timezone.utc), "abcdef12").encode()
    title = TitleCursor("doc", "abcdef12").encode()

    assert client.get(f"{path}?cursor={recent}").status == 200
    assert client.get(f"{path}?sort=title&cursor={title}").status == 200
    assert client.get(f"{path}?sort=title&cursor={recent}").status == 400
    assert client.get(f"{path}?cursor={title}").status == 400


@pytest.mark.parametrize("path", ["/", "/api/pergams"])
def test_an_unknown_sort_is_rejected(client: Client, path: str) -> None:
    resp = client.get(f"{path}?sort=size")

    assert resp.status == 400
    assert "sort must be one of" in resp.text


def test_api_names_its_sort(client: Client) -> None:
    assert client.get("/api/pergams").json()["sort"] == "recent"
    assert client.get("/api/pergams?sort=TITLE").json()["sort"] == "title"


def test_pinned_cards_show_on_the_first_page_only_and_never_repeat(client: Client, db: psycopg.Connection) -> None:
    flags = seed(db)
    pinned = {pid for pid, (is_pinned, archived, _) in flags.items() if is_pinned and not archived}

    first = client.get("/?sort=title").text
    cards = set(re.findall(r'<a class="pin__title" href="/([a-f0-9]+)/view"', first))
    rows = set(re.findall(r'<a class="row__title" href="/([a-f0-9]+)/view"', first))
    second_path = html_lib.unescape(re.search(r'<a class="pager__next" href="([^"]+)"', first).group(1))
    second = client.get(second_path).text

    assert cards == pinned
    assert not cards & rows
    assert 'class="pin__title"' not in second
    assert not pinned & set(listed_order(second))


def test_pinned_cards_follow_the_filters(client: Client) -> None:
    plan = client.publish(title="Pinned plan", type="plan")["id"]
    other = client.publish(title="Pinned other", type="otro")["id"]
    for pid in (plan, other):
        patch_meta(client, pid, {"pinned": True})

    page = client.get("/?type=plan").text

    assert f'<a class="pin__title" href="/{plan}/view">' in page
    assert other not in listed_order(page)


def test_rows_are_grouped_by_the_date_of_their_latest_version(client: Client, db: psycopg.Connection) -> None:
    ids = [client.publish(title=f"Doc {n}")["id"] for n in range(3)]
    db.execute("UPDATE pergams SET created_at = '2001-02-03T04:05:06Z' WHERE id = %s", (ids[0],))

    page = client.get("/").text

    assert re.findall(r'data-group="(\w+)"><h2 class="group__title">([^<]+)</h2>', page) == [
        ("today", "Today"),
        ("earlier", "Earlier"),
    ]
    assert page.index(ids[2]) < page.index(ids[1]) < page.index(">Earlier<") < page.index(ids[0])
    assert 'data-group="title"' in client.get("/?sort=title").text


@pytest.mark.parametrize(
    ("moment", "group"),
    [
        ("2025-06-18T23:59:00+00:00", "today"),
        ("2025-06-18T00:30:00-03:00", "today"),
        ("2025-06-17T23:59:00+00:00", "week"),
        ("2025-06-16T00:00:00+00:00", "week"),
        ("2025-06-15T23:59:00+00:00", "month"),
        ("2025-06-01T00:00:00+00:00", "month"),
        ("2025-05-31T23:59:00+00:00", "earlier"),
        ("2025-06-19T01:00:00+00:00", "today"),
    ],
)
def test_date_groups_are_utc_calendar_days_weeks_and_months(moment: str, group: str) -> None:
    now = datetime(2025, 6, 18, 12, tzinfo=timezone.utc)

    assert date_group(datetime.fromisoformat(moment), now) == group


@pytest.mark.parametrize(
    ("body", "title", "summary"),
    [
        (None, "T", None),
        ("", "T", None),
        ("Plan\nPlan", "plan", None),
        ("Plan\nFirst sentence here. Second one.", "Plan", "First sentence here."),
        ("Overview\nNo full stop", "Plan", "Overview No full stop"),
        ("Is it done? Yes.", "T", "Is it done?"),
        ("Version 1.2 ships", "T", "Version 1.2 ships"),
        ("  spaced\t\tout  \n\n text.  ", "T", "spaced out text."),
    ],
)
def test_summary_is_the_first_sentence_after_the_title(body: str | None, title: str, summary: str | None) -> None:
    assert summarise(body, title) == summary


def test_long_summary_is_cut_at_a_word() -> None:
    body = "word " * 100

    summary = summarise(body, "T")

    assert summary.endswith("word…")
    assert len(summary) <= SUMMARY_CHARS + 1


def test_summary_comes_from_the_extracted_text_without_markup(client: Client) -> None:
    pid = client.publish(
        title="Plan", html="<h1>Plan</h1><p>Use <b>bold</b> &lt;script&gt; carefully. More.</p><script>hidden()</script>"
    )["id"]

    (row,) = client.get("/api/pergams").json()["pergams"]
    page = client.get("/").text

    assert row["summary"] == "Use bold <script> carefully."
    assert '<span class="row__summary">Use bold &lt;script&gt; carefully.</span>' in page
    assert pid in listed_order(page)


def test_summary_is_absent_until_the_text_is_extracted(client: Client, db: psycopg.Connection) -> None:
    client.publish(html="<p>Some text.</p>")
    db.execute("DELETE FROM pergam_text")

    (row,) = client.get("/api/pergams").json()["pergams"]

    assert "summary" not in row
    assert 'class="row__summary"' not in client.get("/").text


@pytest.fixture
def results(client: Client) -> dict[str, str]:
    ids = {
        "plan": client.publish(title="A", type="plan", author="ana@example.com", tags=["ops"], html="<p>needle one</p>")["id"],
        "report": client.publish(title="B", type="reporte", author="bo@example.com", tags=["ops", "q3"], html="<p>needle two</p>")["id"],
        "plan2": client.publish(title="C", type="plan", author="bo@example.com", html="<p>needle three</p>")["id"],
        "hay": client.publish(title="D", type="informe", author="cy@example.com", tags=["q3"], html="<p>only hay</p>")["id"],
        "archived": client.publish(title="E", type="plan", author="ana@example.com", html="<p>needle four</p>")["id"],
    }
    patch_meta(client, ids["archived"], {"archived": True})
    return ids


def test_empty_filters_offer_to_clear_them(client: Client, results: dict[str, str]) -> None:
    page = client.get("/?tag=nope&sort=title").text

    assert "No pergams with tag nope." in page
    assert '<a class="btn btn--primary" href="/?sort=title">Clear filters</a>' in page
    assert '<a class="facet is-on" href="/?sort=title" aria-current="true"><span' in page


def test_links_keep_the_sort_and_drop_the_cursor(client: Client, results: dict[str, str]) -> None:
    page = client.get("/?sort=title&type=plan").text

    assert 'href="/?type=plan&amp;tag=ops&amp;sort=title"' in page
    assert '<a class="chip" href="/?type=plan" title="Most recent first">Recent</a>' in page
    assert '<a class="chip is-on" href="/?type=plan&amp;sort=title" aria-current="true"' in page


def test_long_tag_lists_fold_but_keep_the_active_tag_in_view(client: Client) -> None:
    for n in range(TAGS_SHOWN + 4):
        client.publish(title=f"Doc {n}", tags=[f"tag{m:02d}" for m in range(n + 1)])
    rarest = f"tag{TAGS_SHOWN + 3:02d}"

    plain = client.get("/").text
    active = client.get(f"/?tag={rarest}").text

    assert f"Show all {TAGS_SHOWN + 4}</summary>" in plain
    shown = active.split('<details class="facets__more">')[0]
    assert f'<a class="facet is-on" href="/" aria-current="true"><span class="facet__hash" aria-hidden="true">#</span>{rarest}' in shown


def test_stats_are_one_line_under_the_heading(client: Client) -> None:
    pid = client.publish(html="<p>x</p>")["id"]
    client.publish(id=pid, html="<p>y</p>")

    page = client.get("/").text

    assert "<h1>All pergams</h1>" in page
    assert '<p class="index-stats">1 pergams · 1 authors · 2 versions · 1 new this week · 16 B</p>' in page
    assert "stats-grid" not in page


def test_the_density_toggle_is_hidden_until_the_script_runs(client: Client) -> None:
    page = client.get("/").text

    assert '<button type="button" class="chip" data-density-toggle aria-pressed="false" hidden>Compact</button>' in page
    assert re.search(r'<script src="/static/index.js\?v=[0-9a-f]{12}"></script>', page)


@pytest.mark.parametrize("path", ["/static/index.js"])
def test_page_scripts_are_static_files(client: Client, path: str) -> None:
    resp = client.get(path)

    assert (resp.status, resp.headers["content-type"]) == (200, "text/javascript; charset=utf-8")
    assert resp.headers["cache-control"] == "public, max-age=300"






