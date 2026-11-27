"""Repeated filter parameters (any of the values within a filter, all filters together) and `GET /facets`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlencode

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from pergam.routes import MAX_FILTER_VALUES
from pergam.store import IndexFilters, PergamStore
from support import Client, as_listed, crawl, listed_order, patch_meta, seed


@pytest.fixture
def library(client: Client) -> dict[str, str]:
    def publish(name: str, type_: str, author: str, tags: list[str], body: str = "hay") -> tuple[str, str]:
        return name, client.publish(title=name, type=type_, author=author, tags=tags, html=f"<p>{body}</p>")["id"]

    ids = dict(
        [
            publish("plan-ana-infra", "plan", "ana@example.com", ["infra"], "needle"),
            publish("plan-bo-ops", "plan", "bo@example.com", ["ops"]),
            publish("report-ana-ops", "reporte", "ana@example.com", ["ops", "infra"], "needle"),
            publish("research-cy", "investigacion", "cy@example.com", []),
            publish("other-bo-infra", "otro", "bo@example.com", ["infra"], "needle"),
            publish("old-plan", "plan", "ana@example.com", ["infra"], "needle"),
        ]
    )
    patch_meta(client, ids["old-plan"], {"archived": True})
    return ids


def listed(client: Client, pairs: list[tuple[str, str]]) -> set[str]:
    return set(listed_order(client.get("/?" + urlencode(pairs)).text))


def api(client: Client, pairs: list[tuple[str, str]], path: str = "/api/pergams") -> dict[str, Any]:
    resp = client.get(f"{path}?{urlencode(pairs)}")
    assert resp.status == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([("type", "plan")], {"plan-ana-infra", "plan-bo-ops"}),
        ([("type", "plan"), ("type", "reporte")], {"plan-ana-infra", "plan-bo-ops", "report-ana-ops"}),
        ([("type", "plan"), ("type", "reporte"), ("tag", "infra")], {"plan-ana-infra", "report-ana-ops"}),
        ([("tag", "ops"), ("tag", "infra")], {"plan-ana-infra", "plan-bo-ops", "report-ana-ops", "other-bo-infra"}),
        ([("author", "ana@example.com"), ("author", "cy@example.com")], {"plan-ana-infra", "report-ana-ops", "research-cy"}),
        ([("author", "bo@example.com"), ("tag", "infra"), ("tag", "ops")], {"plan-bo-ops", "other-bo-infra"}),
        ([("type", "plan"), ("type", "otro"), ("q", "needle")], {"plan-ana-infra", "other-bo-infra"}),
        ([("type", "plan"), ("state", "all"), ("tag", "infra")], {"plan-ana-infra", "old-plan"}),
        ([("type", "PLAN"), ("type", " plan "), ("tag", "INFRA")], {"plan-ana-infra"}),
    ],
)
def test_values_of_one_filter_are_a_union_and_filters_intersect(
    client: Client, library: dict[str, str], pairs: list[tuple[str, str]], expected: set[str]
) -> None:
    wanted = {library[name] for name in expected}

    assert listed(client, pairs) == wanted
    assert {row["id"] for row in api(client, pairs)["pergams"]} == wanted


def test_single_value_urls_keep_working(client: Client, library: dict[str, str]) -> None:
    assert listed(client, [("type", "reporte")]) == {library["report-ana-ops"]}
    assert listed(client, [("tag", "ops"), ("author", "bo@example.com")]) == {library["plan-bo-ops"]}


FACETS = (("type", "type"), ("author", "author"), ("tag", "tag"))


@pytest.mark.parametrize(
    "pairs",
    [
        [],
        [("type", "plan"), ("type", "reporte")],
        [("type", "plan"), ("tag", "infra"), ("tag", "ops")],
        [("author", "ana@example.com"), ("author", "bo@example.com"), ("q", "needle")],
        [("tag", "infra"), ("state", "all")],
        [("type", "otro"), ("state", "archived")],
    ],
)
def test_each_count_is_what_selecting_that_value_alone_would_list(
    client: Client, library: dict[str, str], pairs: list[tuple[str, str]]
) -> None:
    body = api(client, pairs, "/facets")

    for key, _ in FACETS:
        others = [(k, v) for k, v in pairs if k != key]
        for item in body["facets"][key]:
            assert item["count"] == api(client, [*others, (key, item["value"])])["matching"], (key, item)
    for state, count in body["facets"]["state"].items():
        assert count == api(client, [*[(k, v) for k, v in pairs if k != "state"], ("state", state)])["matching"]
    assert body["total"] == api(client, pairs)["matching"]


def test_facets_list_every_value_with_zeros_and_types_in_configured_order(client: Client, library: dict[str, str]) -> None:
    body = api(client, [("type", "plan"), ("tag", "nothing-has-this")], "/facets")

    assert [item["value"] for item in body["facets"]["type"]] == ["plan", "investigacion", "informe", "reporte", "otro"]
    assert {item["value"]: item["count"] for item in body["facets"]["type"]}["informe"] == 0
    assert {"value": "nothing-has-this", "count": 0} in body["facets"]["tag"]
    assert body["total"] == 0
    assert body["facets"]["state"] == {"active": 0, "archived": 0, "all": 0}


def test_facets_answer_json_and_are_not_cached(client: Client, library: dict[str, str]) -> None:
    ok = client.get("/facets?type=plan")
    bad = client.request("GET", "/facets?state=nope", headers={"Accept": "text/html"})

    assert ok.headers["content-type"] == "application/json"
    assert ok.headers["cache-control"] == "no-store"
    assert bad.status == 400
    assert "error__code" not in bad.text


@pytest.mark.parametrize("path", ["/", "/api/pergams", "/facets"])
def test_too_many_values_of_one_filter_is_400(client: Client, path: str) -> None:
    at_limit = urlencode([("tag", f"t{n}") for n in range(MAX_FILTER_VALUES)])
    over = urlencode([("tag", f"t{n}") for n in range(MAX_FILTER_VALUES + 1)])

    assert client.get(f"{path}?{at_limit}").status == 200
    resp = client.get(f"{path}?{over}")
    assert resp.status == 400
    assert f"at most {MAX_FILTER_VALUES} values of tag" in resp.text


@pytest.mark.parametrize("key", ["type", "author", "tag"])
def test_repeated_values_are_data_not_sql(client: Client, library: dict[str, str], db: psycopg.Connection, key: str) -> None:
    hostile = ["x') OR 1=1 --", "'; DROP TABLE pergams; --", "a\\b", '"quoted"']

    resp = client.get("/?" + urlencode([(key, value) for value in hostile]))

    assert resp.status == 200
    assert listed_order(resp.text) == []
    assert db.execute("SELECT count(*) FROM pergams").fetchone()[0] == 6


class CountingPool:
    """A pool whose connections count `execute` calls, to check the number of queries."""

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool
        self.calls = 0

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self.pool.connection() as conn:
            outer = self

            class Counting:
                def execute(self, *args: Any, **kwargs: Any) -> Any:
                    outer.calls += 1
                    return conn.execute(*args, **kwargs)

            yield Counting()


@pytest.fixture
def counting(session_database: str, db: psycopg.Connection) -> Iterator[tuple[PergamStore, CountingPool]]:
    with ConnectionPool(session_database, min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        counter = CountingPool(pool)
        yield PergamStore(counter), counter


def test_the_number_of_queries_does_not_grow_with_the_values(
    counting: tuple[PergamStore, CountingPool], library: dict[str, str]
) -> None:
    store, counter = counting

    def queries(filters: IndexFilters, call: str) -> int:
        counter.calls = 0
        getattr(store, call)(filters) if call == "facets" else store.index(filters, cards=True)
        return counter.calls

    one = IndexFilters(types=("plan",), tags=("infra",), authors=("ana@example.com",), q="needle")
    many = IndexFilters(
        types=tuple(f"t{n}" for n in range(40)),
        tags=tuple(f"g{n}" for n in range(40)),
        authors=tuple(f"a{n}@example.com" for n in range(40)),
        q="needle",
    )

    assert queries(one, "facets") == queries(many, "facets") == 5
    assert queries(one, "index") == queries(many, "index")


@pytest.mark.parametrize("sort", ["recent", "title"])
@pytest.mark.parametrize("state", ["active", "archived", "all"])
@pytest.mark.parametrize("q", [None, "Doc 1"])
def test_paging_visits_every_pergam_once_with_several_values(
    client: Client, db: psycopg.Connection, sort: str, state: str, q: str | None
) -> None:
    flags = seed(db)
    order = (
        "lower(l.title), l.id"
        if sort == "title"
        else "COALESCE(m.pinned AND NOT m.archived, false) DESC, l.created_at DESC, l.id DESC"
    )
    rows = db.execute(
        f"SELECT l.id, l.type, l.title FROM pergams_latest l LEFT JOIN pergam_meta m USING (id) ORDER BY {order}"
    ).fetchall()
    in_state = {"active": lambda pid: not flags[pid][1], "archived": lambda pid: flags[pid][1], "all": lambda pid: True}[state]
    expected = [
        pid
        for pid, kind, title in rows
        if kind in ("plan", "otro") and in_state(pid) and (q is None or q.lower() in title.lower())
    ]
    by_date = [pid for (pid,) in db.execute("SELECT id FROM pergams_latest ORDER BY created_at DESC, id DESC")]
    query = f"?type=plan&type=otro&state={state}&sort={sort}" + (f"&q={q.replace(' ', '+')}" if q else "")

    visited = crawl(client, f"/{query}")
    api_ids: list[str] = []
    path = f"/api/pergams{query}"
    while path:
        body = client.get(path).json()
        api_ids.extend(row["id"] for row in body["pergams"])
        path = f"/api/pergams{query}&cursor={body['next_cursor']}" if body["next_cursor"] else ""

    assert expected
    assert api_ids == expected
    assert visited == as_listed(expected, flags, by_date)
