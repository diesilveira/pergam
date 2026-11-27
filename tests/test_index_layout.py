"""The index's sort orders, pinned cards, date groups, summaries, filter column and top-bar search."""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone

from collections.abc import Iterator
from urllib.parse import parse_qsl, urlencode

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from pergam.cursor import IndexCursor, TitleCursor
from pergam.index_page import date_group
from pergam.search import HEADLINE_SQL, MATCHES_SQL, search_params
from pergam.store import SEARCHED, IndexFilters, PergamStore, page_query, save_text
from pergam.summary import SUMMARY_CHARS, SOURCE_CHARS, summarise
from support import Client, as_listed, crawl, listed_order, patch_meta, seed


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


@pytest.mark.parametrize("query", ["?sort=title", "?sort=title&state=all", "?sort=title&tag=seeded&type=plan"])
def test_title_order_pages_every_pergam_once(client: Client, db: psycopg.Connection, query: str) -> None:
    flags = seed(db)
    archived = "state=all" in query
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

    by_date = [pid for (pid,) in db.execute("SELECT id FROM pergams_latest ORDER BY created_at DESC, id DESC").fetchall()]
    assert from_api == expected
    assert from_page == as_listed(expected, flags, by_date)


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
        ("Is it done? Yes.", "T", "Is it done? Yes."),
        ("Dr. Smith reviewed the plan. Then more.", "T", "Dr. Smith reviewed the plan."),
        ("Version 2. ships with the new cutover window. More.", "T", "Version 2. ships with the new cutover window."),
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


def matching(client: Client, params: dict[str, str]) -> int:
    return client.get(f"/api/pergams?{urlencode(params)}").json()["matching"]


FACETS = (("types", "type"), ("authors", "author"), ("tags", "tag"))


@pytest.mark.parametrize(
    "query",
    ["", "q=needle", "q=needle&type=plan", "q=needle&state=all", "q=needle&tag=ops", "type=plan", "type=plan&author=bo@example.com&tag=ops", "author=ana@example.com&state=all"],
)
def test_each_facet_counts_what_choosing_it_would_give(client: Client, results: dict[str, str], query: str) -> None:
    """Standard faceting: a facet applies `q` and every other filter, never its own."""
    params = dict(parse_qsl(query))
    body = client.get(f"/api/pergams?{query}").json()

    for key, name in FACETS:
        others = {k: v for k, v in params.items() if k != name}
        for item in body["counts"][key]:
            assert item["count"] == matching(client, {**others, name: item[name]}), (key, item)
        if key != "tags":
            assert sum(item["count"] for item in body["counts"][key]) == matching(client, others)
    assert body["archived_count"] == matching(client, {**params, "state": "archived"})
    assert body["state"] == params.get("state", "active")


def test_other_values_of_a_facet_stay_listed_within_a_search(client: Client, results: dict[str, str]) -> None:
    body = client.get("/api/pergams?q=needle&type=plan").json()

    assert body["counts"]["types"] == [{"type": "plan", "count": 2}, {"type": "reporte", "count": 1}]
    assert body["matching"] == 2


def test_search_counts_cover_the_results_and_archived_matches(client: Client, results: dict[str, str]) -> None:
    body = client.get("/api/pergams?q=needle").json()
    page = client.get("/?q=needle").text

    assert body["counts"]["types"] == [{"type": "plan", "count": 2}, {"type": "reporte", "count": 1}]
    assert body["counts"]["tags"] == [{"tag": "ops", "count": 2}, {"tag": "q3", "count": 1}]
    assert body["archived_count"] == 1
    assert "<h1>3 results</h1>" in page
    assert "for “needle” · the filters count these results only" in page


def test_facets_apply_the_other_filters_without_a_search(client: Client, results: dict[str, str]) -> None:
    body = client.get("/api/pergams?type=reporte").json()
    page = client.get("/?type=reporte").text

    assert body["counts"]["types"] == [{"type": "plan", "count": 2}, {"type": "informe", "count": 1}, {"type": "reporte", "count": 1}]
    assert body["counts"]["authors"] == [{"author": "bo@example.com", "count": 1}]
    assert body["archived_count"] == 0
    assert "showing 1 of 4" in page


def test_an_empty_search_says_what_was_searched_and_offers_to_widen_it(client: Client, results: dict[str, str]) -> None:
    page = client.get("/?q=%3Cnothing%3E&type=plan").text

    assert "Nothing matches “&lt;nothing&gt;” with type plan (archived pergams not included)." in page
    assert '<a class="btn btn--primary" href="/?state=all&amp;q=%3Cnothing%3E">Search every pergam</a>' in page
    assert '<a class="btn" href="/">Clear search</a>' in page


def test_empty_filters_offer_to_clear_them(client: Client, results: dict[str, str]) -> None:
    page = client.get("/?tag=nope&sort=title").text

    assert "No pergams with tag nope." in page
    assert '<a class="btn btn--primary" href="/?sort=title">Clear filters</a>' in page


def test_links_keep_the_sort_and_drop_the_cursor(client: Client, results: dict[str, str]) -> None:
    page = client.get("/?sort=title&type=plan").text

    assert 'href="/?type=plan&amp;tag=ops&amp;sort=title"' in page
    assert '<li><a href="/?type=plan">Recent</a></li>' in page
    assert '<li><a href="/?type=plan&amp;sort=title" aria-current="true">Title A–Z</a></li>' in page


def test_the_index_is_one_column_without_a_filter_column_or_stats_line(client: Client) -> None:
    pid = client.publish(html="<p>x</p>", tags=["ops"])["id"]
    client.publish(id=pid, html="<p>y</p>")

    page = client.get("/").text

    assert "<h1>All pergams</h1>" in page
    assert 'class="facets' not in page and "index-stats" not in page and "data-density-toggle" not in page
    assert '<main class="index">' in page


def test_feed_and_json_links_move_to_the_footer(client: Client) -> None:
    client.publish()

    page = client.get("/?tag=ops&sort=title").text
    footer = page[page.index('<footer class="index-foot">'):]
    top_bar = page[page.index('<header class="topbar">'):page.index("</header>")]

    assert '<a href="/feed.atom">Atom feed</a>' in footer
    assert '<a href="/api/pergams?tag=ops&amp;sort=title">This list as JSON</a>' in footer
    assert "feed.atom" not in top_bar and "/api/pergams" not in top_bar
    assert "data-theme-toggle" in top_bar and 'class="topbar__search' in top_bar


def test_the_sort_menu_links_each_order_and_keeps_the_filters(client: Client) -> None:
    client.publish()

    menu = re.search(r'<details class="dropdown sortmenu".*?</details>', client.get("/?tag=ops&sort=title").text).group(0)

    assert '<summary class="btn sortmenu__toggle" aria-label="Sort: Title A–Z">Title A–Z' in menu
    assert '<li><a href="/?tag=ops">Recent</a></li>' in menu
    assert '<li><a href="/?tag=ops&amp;sort=title" aria-current="true">Title A–Z</a></li>' in menu


def test_the_summaries_switch_is_hidden_until_the_script_runs(client: Client) -> None:
    page = client.get("/").text

    assert re.search(r'<li class="sortmenu__switch" data-summaries-item hidden><button type="button" role="switch" aria-checked="false" data-summaries-toggle>', page)
    assert re.search(r'<script src="/static/index.js\?v=[0-9a-f]{12}"></script>', page)


def test_rows_keep_their_details_in_the_markup_for_the_summaries_switch(client: Client) -> None:
    pid = client.publish(title="Plan", html="<p>The first sentence is long enough.</p>", tags=["ops"])["id"]

    row = re.search(rf'<li class="row" data-href="/{pid}/view">.*?</li>', client.get("/").text).group(0)

    assert '<span class="row__summary">The first sentence is long enough.</span>' in row
    assert '<a class="tag" href="/?tag=ops"' in row
    assert 'title="1 version">v1</span>' in row
    assert 'aria-label="Filter by author: you@example.com"' in row


@pytest.mark.parametrize("path", ["/static/shortcuts.js", "/static/index.js"])
def test_page_scripts_are_static_files(client: Client, path: str) -> None:
    resp = client.get(path)

    assert (resp.status, resp.headers["content-type"]) == (200, "text/javascript; charset=utf-8")
    assert resp.headers["cache-control"] == "public, max-age=300"


def search_form(page: str) -> str:
    return re.search(r'<form class="topbar__search[^"]*".*?</form>', page).group(0)


@pytest.mark.parametrize("path", ["/{id}/view", "/{id}/v1/view", "/{id}/diff/v1..v2", "/nope"])
def test_every_page_has_the_search_field_in_its_top_bar(client: Client, path: str) -> None:
    pid = client.publish(html="<p>one</p>")["id"]
    client.publish(id=pid, html="<p>two</p>")

    page = client.request("GET", path.format(id=pid), headers={"Accept": "text/html"}).text
    form = search_form(page)

    assert 'action="/" method="get" role="search"' in form
    assert '<input class="topbar__search-input" id="topbar-search" type="search" name="q" value=""' in form
    assert 'class="topbar__search topbar__search--compact' in form
    assert ("topbar__search--collapsed" in form) is (path != "/nope")
    assert 'aria-label="Search title and text"' in form and "data-search" in form
    assert 'type="hidden"' not in form
    assert re.search(r'<script src="/static/shortcuts.js\?v=[0-9a-f]{12}" defer></script>', page)


def test_the_index_search_field_shows_the_query_and_keeps_the_filters(client: Client) -> None:
    form = search_form(client.get('/?q=%22a%22%3Cb&tag=ops&state=all&sort=title').text)

    assert 'value="&quot;a&quot;&lt;b"' in form
    assert re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)">', form) == [
        ("tag", "ops"),
        ("state", "all"),
        ("sort", "title"),
    ]


def test_rows_and_cards_name_the_pergam_they_open(client: Client) -> None:
    listed = client.publish(title="Listed")["id"]
    pinned = client.publish(title="Pinned")["id"]
    patch_meta(client, pinned, {"pinned": True})

    page = client.get("/").text

    assert f'<li class="row" data-href="/{listed}/view">' in page
    assert f'<li class="pin" data-href="/{pinned}/view">' in page


def insert(db: psycopg.Connection, rows: list[tuple[str, str, bool]]) -> None:
    with db.cursor() as cur:
        for pid, title, pinned in rows:
            cur.execute(
                "INSERT INTO pergams (id, version, title, html, type, author, bytes) VALUES (%s, 1, %s, '<p>x</p>', 'otro', 'you@example.com', 8)",
                (pid, title),
            )
            if pinned:
                cur.execute("INSERT INTO pergam_meta (id, pinned) VALUES (%s, true)", (pid,))


def test_a_later_page_of_carded_rows_shows_no_empty_state(client: Client, db: psycopg.Connection) -> None:
    insert(db, [(f"a{n:07x}", f"a {n:03d}", False) for n in range(100)])
    insert(db, [(f"b{n:07x}", f"b {n:03d}", True) for n in range(100)])
    insert(db, [("c0000000", "c", False)])

    first = client.get("/?sort=title").text
    second_path = html_lib.unescape(re.search(r'<a class="pager__next" href="([^"]+)"', first).group(1))
    second = client.get(second_path).text

    assert listed_order(second) == []
    assert 'class="empty"' not in second
    assert 'class="pager__next"' in second
    assert crawl(client, "/?sort=title")[-1] == "c0000000"


@pytest.mark.parametrize("query", ["?sort=title", "?sort=title&state=all"])
def test_title_ties_across_page_boundaries_are_broken_by_id(client: Client, db: psycopg.Connection, query: str) -> None:
    titles = ["Same", "same", "SAME", "Other"]
    insert(db, [(f"{n:08x}", titles[n % 4], n % 9 == 0) for n in range(230)])
    expected = [pid for (pid,) in db.execute("SELECT id FROM pergams_latest ORDER BY lower(title), id").fetchall()]

    from_api = api_crawl(client, query)
    from_page = crawl(client, f"/{query}")

    assert from_api == expected
    assert sorted(from_page) == sorted(expected) and len(from_page) == len(set(from_page))


@pytest.fixture
def store(session_database: str, db: psycopg.Connection) -> Iterator[PergamStore]:
    with ConnectionPool(session_database, min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        yield PergamStore(pool)


def test_pinned_cards_are_fetched_only_for_the_page_and_only_as_ids_later(store: PergamStore, db: psycopg.Connection) -> None:
    flags = seed(db)
    pinned = {pid for pid, (is_pinned, archived, _) in flags.items() if is_pinned and not archived}

    api = store.index(IndexFilters())
    first = store.index(IndexFilters(), cards=True)
    later = store.index(IndexFilters(cursor=first.next_cursor), cards=True)

    assert (api.pinned, api.pinned_ids) == ([], frozenset())
    assert {row.id for row in first.pinned} == first.pinned_ids == pinned
    assert (later.pinned, later.pinned_ids) == ([], pinned)


def plan_outputs(node: dict) -> list[str]:
    return [*node.get("Output", []), *(text for child in node.get("Plans", []) for text in plan_outputs(child))]


def find_limit(node: dict) -> dict | None:
    if node["Node Type"] == "Limit":
        return node
    return next((found for child in node.get("Plans", []) if (found := find_limit(child))), None)


@pytest.mark.parametrize("sort", ["recent", "title"])
def test_text_and_headline_are_computed_for_the_page_rows_only(db: psycopg.Connection, sort: str) -> None:
    flags = seed(db)
    save_text(db, [(pid, 1, "<p>a needle here</p>") for pid in flags])
    sql = page_query(SEARCHED, f"WHERE {MATCHES_SQL}", sort, HEADLINE_SQL, "%(limit)s")
    params = {**search_params("needle"), "source_chars": SOURCE_CHARS, "limit": 101}

    (plan,) = db.execute(f"EXPLAIN (VERBOSE, FORMAT JSON) {sql}", params).fetchone()[0]
    below_limit = " ".join(plan_outputs(find_limit(plan["Plan"])))
    above_limit = " ".join(plan["Plan"]["Output"])

    assert '"left"(t.body' not in below_limit and "@@" not in below_limit
    assert '"left"(t.body' in above_limit and "@@" in above_limit
