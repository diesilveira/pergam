"""The status filter: `state=active` (default), `archived` or `all`, on the index, the API and the feed."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import psycopg
import pytest

from pergam.states import StateError, parse_state
from support import Client, as_listed, crawl, listed_order, patch_meta, seed

ATOM = "{http://www.w3.org/2005/Atom}"


@pytest.mark.parametrize(("raw", "state"), [(None, "active"), ("", "active"), (" ARCHIVED ", "archived"), ("all", "all")])
def test_parse_state(raw: str | None, state: str) -> None:
    assert parse_state(raw) == state


@pytest.mark.parametrize("path", ["/?state=gone", "/api/pergams?state=gone", "/feed.atom?state=gone"])
def test_an_unknown_state_is_400(client: Client, path: str) -> None:
    resp = client.get(path)

    assert resp.status == 400
    assert "state must be one of ['active', 'archived', 'all']" in resp.text


def test_parse_state_rejects_other_values() -> None:
    with pytest.raises(StateError):
        parse_state("archive")


@pytest.fixture
def shelf(client: Client) -> dict[str, str]:
    ids = {
        "active": client.publish(title="Active plan", type="plan", html="<p>needle active</p>", tags=["ops"])["id"],
        "other": client.publish(title="Other report", type="reporte", html="<p>hay</p>")["id"],
        "old": client.publish(title="Old plan", type="plan", html="<p>needle old</p>", tags=["ops"])["id"],
    }
    patch_meta(client, ids["old"], {"archived": True})
    return ids


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", {"active", "other"}),
        ("?state=active", {"active", "other"}),
        ("?state=archived", {"old"}),
        ("?state=all", {"active", "other", "old"}),
        ("?state=archived&q=needle", {"old"}),
        ("?state=archived&type=plan&tag=ops", {"old"}),
        ("?state=archived&type=reporte", set()),
        ("?state=all&q=needle", {"active", "old"}),
    ],
)
def test_each_state_lists_its_pergams_in_the_page_and_the_api(
    client: Client, shelf: dict[str, str], query: str, expected: set[str]
) -> None:
    wanted = {shelf[name] for name in expected}

    assert set(listed_order(client.get(f"/{query}").text)) == wanted
    assert {row["id"] for row in client.get(f"/api/pergams{query}").json()["pergams"]} == wanted


def test_archived_never_returns_an_active_pergam(client: Client, db: psycopg.Connection) -> None:
    flags = seed(db)

    for query in ("?state=archived", "?state=archived&sort=title", "?state=archived&tag=seeded"):
        ids = crawl(client, f"/{query}")
        assert ids and all(flags[pid][1] for pid in ids)


def test_the_archived_view_is_titled_and_carries_no_badge(client: Client, shelf: dict[str, str]) -> None:
    archived, everything = client.get("/?state=archived").text, client.get("/?state=all").text

    assert "<h1>Archived pergams</h1>" in archived
    assert 'class="flag flag--archived"' not in archived
    assert '<span class="flag flag--archived">Archived</span>' in everything
    assert everything.count('class="flag flag--archived"') == 1


@pytest.mark.parametrize(
    ("path", "location"),
    [
        ("/?archived=1", "/?state=all"),
        ("/?archived=true&tag=ops&sort=title", "/?tag=ops&sort=title&state=all"),
        ("/?type=plan&archived=1&q=needle", "/?type=plan&q=needle&state=all"),
        ("/?archived=1&state=archived", "/?state=archived"),
        ("/?archived=0", "/"),
        ("/?archived=", "/"),
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_old_archived_links_redirect_permanently(client: Client, path: str, location: str, method: str) -> None:
    resp = client.request(method, path)

    assert resp.status == 301
    assert resp.headers["location"] == location
    assert resp.body == b""


def test_old_archived_links_land_on_the_same_set(client: Client, shelf: dict[str, str]) -> None:
    location = client.get("/?archived=1&tag=ops").headers["location"]

    assert set(listed_order(client.get(location).text)) == {shelf["active"], shelf["old"]}


def test_the_api_reads_archived_1_as_state_all(client: Client, shelf: dict[str, str]) -> None:
    alias, explicit = client.get("/api/pergams?archived=1").json(), client.get("/api/pergams?state=all").json()

    assert alias == explicit
    assert alias["state"] == "all"
    assert client.get("/api/pergams?archived=1&state=archived").json()["state"] == "archived"


def feed_ids(client: Client, query: str = "") -> tuple[set[str], str]:
    root = ET.fromstring(client.get(f"/feed.atom{query}").body)
    ids = {entry.find(f"{ATOM}id").text.rsplit("/", 3)[-3] for entry in root.iter(f"{ATOM}entry")}
    return ids, root.find(f"{ATOM}link[@rel='self']").get("href")


def test_the_feed_takes_the_same_state(client: Client, shelf: dict[str, str]) -> None:
    active, active_self = feed_ids(client)
    archived, archived_self = feed_ids(client, "?state=archived&tag=ops")
    everything, _ = feed_ids(client, "?state=all")

    assert active == {shelf["active"], shelf["other"]}
    assert archived == {shelf["old"]}
    assert everything == set(shelf.values())
    assert active_self.endswith("/feed.atom")
    assert archived_self.endswith("/feed.atom?tag=ops&state=archived")


@pytest.mark.parametrize("state", ["active", "archived", "all"])
@pytest.mark.parametrize("sort", ["recent", "title"])
def test_paging_visits_every_pergam_once_in_each_state(client: Client, db: psycopg.Connection, state: str, sort: str) -> None:
    flags = seed(db)
    order = (
        "lower(l.title), l.id"
        if sort == "title"
        else "COALESCE(m.pinned AND NOT m.archived, false) DESC, l.created_at DESC, l.id DESC"
    )
    rows = [pid for (pid,) in db.execute(f"SELECT l.id FROM pergams_latest l LEFT JOIN pergam_meta m USING (id) ORDER BY {order}")]
    keep = {"active": lambda pid: not flags[pid][1], "archived": lambda pid: flags[pid][1], "all": lambda pid: True}[state]
    expected = [pid for pid in rows if keep(pid)]
    by_date = [pid for (pid,) in db.execute("SELECT id FROM pergams_latest ORDER BY created_at DESC, id DESC")]
    query = f"?state={state}&sort={sort}"

    visited = crawl(client, f"/{query}")
    api: list[str] = []
    path = f"/api/pergams{query}"
    while path:
        body = client.get(path).json()
        api.extend(row["id"] for row in body["pergams"])
        path = f"/api/pergams{query}&cursor={body['next_cursor']}" if body["next_cursor"] else ""

    assert api == expected
    assert visited == as_listed(expected, flags, by_date)
    assert len(visited) == len(set(visited)) == len(expected)
