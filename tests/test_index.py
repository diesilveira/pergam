from __future__ import annotations

import html as html_lib
import re

import psycopg
import pytest

from support import Client, as_listed, crawl, listed_ids, listed_order, patch_meta, seed


@pytest.fixture
def catalogue(client: Client) -> dict[str, str]:
    plan = client.publish(title="Deploy plan", type="plan", author="ana@example.com")["id"]
    report = client.publish(title="Weekly report", type="reporte", author="bo@example.com")["id"]
    notes = client.publish(title="Release notes", type="plan", author="bo@example.com")["id"]
    client.publish(id=notes, title="Release notes v2", type="plan", author="bo@example.com")
    return {"plan": plan, "report": report, "notes": notes}


def test_index_lists_latest_version_of_every_pergam(client: Client, catalogue: dict[str, str]) -> None:
    resp = client.get("/")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert listed_ids(resp.text, *catalogue.values()) == set(catalogue.values())
    assert "Release notes v2" in resp.text
    assert 'title="2 versions">v2</span>' in resp.text
    assert "3 pergams · 2 authors · 4 versions" in resp.text


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?type=plan", {"plan", "notes"}),
        ("?type=PLAN", {"plan", "notes"}),
        ("?author=bo@example.com", {"report", "notes"}),
        ("?q=REPORT", {"report"}),
        ("?q=notes%20v2", {"notes"}),
        ("?type=plan&author=bo@example.com", {"notes"}),
        ("?type=informe", set()),
    ],
)
def test_index_filters(client: Client, catalogue: dict[str, str], query: str, expected: set[str]) -> None:
    resp = client.get(f"/{query}")

    assert resp.status == 200
    assert listed_ids(resp.text, *catalogue.values()) == {catalogue[k] for k in expected}


def test_index_filtered_view_reports_partial_count(client: Client, catalogue: dict[str, str]) -> None:
    resp = client.get("/?type=reporte")

    assert "showing 1 of 3" in resp.text
    assert 'href="/">clear all</a>' in resp.text


def test_empty_index_shows_hint(client: Client) -> None:
    resp = client.get("/")

    assert resp.status == 200
    assert "No pergams yet." in resp.text


def test_index_escapes_user_content(client: Client) -> None:
    client.publish(title="<script>alert(1)</script>", author="x<y>@example.com")

    resp = client.get("/")

    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text


@pytest.fixture
def tagged(client: Client) -> dict[str, str]:
    ids = {
        "ops_plan": client.publish(title="Ops plan", type="plan", author="ana@example.com", tags=["ops", "q3"])["id"],
        "ops_report": client.publish(title="Ops report", type="reporte", author="bo@example.com", tags=["ops"])["id"],
        "q3_plan": client.publish(title="Budget plan", type="plan", author="bo@example.com", tags=["q3"])["id"],
        "untagged": client.publish(title="Loose notes", type="otro", author="ana@example.com")["id"],
        "archived": client.publish(title="Old ops plan", type="plan", author="ana@example.com", tags=["ops"])["id"],
    }
    patch_meta(client, ids["archived"], {"archived": True})
    return ids


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?tag=ops", {"ops_plan", "ops_report"}),
        ("?tag=OPS", {"ops_plan", "ops_report"}),
        ("?tag=q3&type=plan", {"ops_plan", "q3_plan"}),
        ("?tag=ops&author=bo@example.com", {"ops_report"}),
        ("?tag=ops&q=plan", {"ops_plan"}),
        ("?tag=ops&type=plan&author=ana@example.com&q=ops", {"ops_plan"}),
        ("?tag=nope", set()),
        ("?archived=1", {"ops_plan", "ops_report", "q3_plan", "untagged", "archived"}),
        ("?archived=1&tag=ops&type=plan", {"ops_plan", "archived"}),
        ("", {"ops_plan", "ops_report", "q3_plan", "untagged"}),
    ],
)
def test_tag_filter_composes_and_archived_stay_hidden(
    client: Client, tagged: dict[str, str], query: str, expected: set[str]
) -> None:
    resp = client.get(f"/{query}")

    assert resp.status == 200
    assert listed_ids(resp.text, *tagged.values()) == {tagged[k] for k in expected}


def test_tag_pills_count_visible_pergams(client: Client, tagged: dict[str, str]) -> None:
    hidden, shown = client.get("/").text, client.get("/?archived=1").text

    assert re.search(r'href="/\?tag=ops"><span class="facet__hash" aria-hidden="true">#</span>ops<span class="facet__count">2</span>', hidden)
    assert re.search(r'href="/\?tag=q3"><span[^>]*>#</span>q3<span class="facet__count">2</span>', hidden)
    assert re.search(r'href="/\?tag=ops&amp;archived=1"><span[^>]*>#</span>ops<span class="facet__count">3</span>', shown)
    assert '<a class="facet" href="/?archived=1">Archived<span class="facet__count">1</span>' in hidden
    assert '<a class="facet is-on" href="/" aria-current="true">Archived' in shown


def test_active_tag_pill_links_back_to_unfiltered(client: Client, tagged: dict[str, str]) -> None:
    resp = client.get("/?tag=ops&type=plan")

    assert re.search(r'<a class="facet is-on" href="/\?type=plan" aria-current="true"><span[^>]*>#</span>ops<', resp.text)
    assert "showing 1 of 4" in resp.text


@pytest.mark.parametrize(
    ("query", "note"),
    [
        ("/", "showing 4 of 5 · 1 archived hidden"),
        ("/?archived=1", ""),
        ("/?tag=ops", "showing 2 of 4"),
        ("/?tag=ops&archived=1", "showing 3 of 5"),
    ],
)
def test_header_counts_follow_the_archived_filter(
    client: Client, tagged: dict[str, str], query: str, note: str
) -> None:
    page = client.get(query).text

    assert f'<p class="index-head__note">{note}</p>' in page
    assert '<p class="index-stats">5 pergams · 2 authors · 5 versions · ' in page


@pytest.mark.parametrize(("query", "clear"), [("/", False), ("/?archived=1", True), ("/?tag=ops", True)])
def test_clear_all_resets_the_archived_filter_too(
    client: Client, tagged: dict[str, str], query: str, clear: bool
) -> None:
    assert ('href="/">clear all</a>' in client.get(query).text) is clear


def test_rows_show_tags_and_flags(client: Client, tagged: dict[str, str]) -> None:
    patch_meta(client, tagged["q3_plan"], {"pinned": True})

    resp = client.get("/?archived=1")

    assert '<a class="tag" href="/?tag=q3&amp;archived=1"' in resp.text
    assert f'<li class="pin" data-href="/{tagged["q3_plan"]}/view"><a class="pin__title" href="/{tagged["q3_plan"]}/view">' in resp.text
    assert re.search(
        rf'<li class="row row--archived" data-href="/{tagged["archived"]}/view">.*?<span class="flag flag--archived">archived</span>'
        rf'<a class="row__title" href="/{tagged["archived"]}/view">',
        resp.text,
    )


def test_pinned_pergams_sort_first(client: Client, tagged: dict[str, str]) -> None:
    patch_meta(client, tagged["ops_plan"], {"pinned": True})

    assert listed_order(client.get("/").text)[0] == tagged["ops_plan"]
    assert listed_order(client.get("/").text)[1] == tagged["untagged"]


def test_invalid_cursor_is_rejected(client: Client) -> None:
    assert client.get("/?cursor=not-a-cursor").status == 400
    assert client.get("/?cursor=WzIsIngiLCJ5Il0").status == 400


@pytest.mark.parametrize(
    ("query", "keep"),
    [
        ("/", lambda pinned, archived, tagged: not archived),
        ("/?archived=1", lambda pinned, archived, tagged: True),
        ("/?tag=seeded", lambda pinned, archived, tagged: tagged and not archived),
        ("/?tag=seeded&archived=1&type=plan", lambda pinned, archived, tagged: tagged),
    ],
)
def test_paging_visits_every_pergam_once_in_order(client: Client, db: psycopg.Connection, query: str, keep) -> None:
    flags = seed(db)
    rows = db.execute(
        "SELECT l.id, l.type FROM pergams_latest l LEFT JOIN pergam_meta m USING (id)"
        " ORDER BY COALESCE(m.pinned AND NOT m.archived, false) DESC, l.created_at DESC, l.id DESC"
    ).fetchall()
    assert db.execute("SELECT count(*) FROM pergams_latest WHERE id NOT IN (SELECT id FROM pergam_meta)").fetchone()[0] > 0
    type_filter = "plan" if "type=plan" in query else None
    expected = [pid for pid, kind in rows if keep(*flags[pid]) and (type_filter is None or kind == type_filter)]

    visited = crawl(client, query)

    assert visited == as_listed(expected, flags)
    assert len(visited) == len(set(visited))


def test_first_page_has_no_back_link_and_later_pages_do(client: Client, db: psycopg.Connection) -> None:
    seed(db)

    first = client.get("/?archived=1").text
    second_path = html_lib.unescape(re.search(r'<a class="pager__next" href="([^"]+)"', first).group(1))
    second = client.get(second_path).text

    assert "first page" not in first
    assert "archived=1" in second_path
    assert '<a class="pager__first" href="/?archived=1">' in second
