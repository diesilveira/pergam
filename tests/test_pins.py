"""The cap on pinned pergams (`PERGAM_MAX_PINNED`), pin order and how archiving interacts with pins."""

from __future__ import annotations

import http.client
import json
import re
import threading
from datetime import datetime
from pathlib import Path

import psycopg
import pytest

from pergam.pins import DEFAULT_MAX_PINNED, max_pinned
from support import Client, Response, ServerProcess, patch_meta


def patch(client: Client, pid: str, payload: dict) -> Response:
    return client.request("PATCH", f"/{pid}/meta", json.dumps(payload).encode(), {"Content-Type": "application/json"})


def pinned_ids(db: psycopg.Connection) -> set[str]:
    return {pid for (pid,) in db.execute("SELECT id FROM pergam_meta WHERE pinned AND NOT archived").fetchall()}


def card_order(client: Client, query: str = "") -> list[str]:
    return [row["id"] for row in pinned_rows(client, query)]


def pinned_rows(client: Client, query: str = "") -> list[dict]:
    """Pinned rows of the API, most recently pinned first, as the index's cards show them."""
    rows = [row for row in client.get(f"/api/pergams{query}").json()["pergams"] if row["pinned"] and not row["archived"]]
    dated = sorted((row for row in rows if row["pinned_at"]), key=lambda row: row["pinned_at"], reverse=True)
    return dated + [row for row in rows if not row["pinned_at"]]


def cards(page: str) -> list[str]:
    return re.findall(r'<a class="pin__title" href="/([0-9a-f]+)/view"', page)


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, DEFAULT_MAX_PINNED), ({"PERGAM_MAX_PINNED": ""}, 12), ({"PERGAM_MAX_PINNED": " 3 "}, 3), ({"PERGAM_MAX_PINNED": "0"}, 0)],
)
def test_max_pinned_reads_the_environment(env: dict[str, str], expected: int) -> None:
    assert max_pinned(env) == expected


@pytest.mark.parametrize("raw", ["twelve", "-1", "1.5", "+3", "３", "٣", "1_0", "0x3"])
def test_max_pinned_rejects_bad_values(raw: str) -> None:
    with pytest.raises(ValueError, match="PERGAM_MAX_PINNED"):
        max_pinned({"PERGAM_MAX_PINNED": raw})


def test_server_refuses_an_invalid_cap(session_database: str, server_logs: Path) -> None:
    server = ServerProcess(session_database, server_logs, {"PERGAM_MAX_PINNED": "lots"})

    server.launch()
    try:
        assert server.wait_exit() != 0
    finally:
        server.stop()

    assert "PERGAM_MAX_PINNED" in server.log()


@pytest.fixture
def capped(databases, start_server) -> tuple[Client, psycopg.Connection]:
    url = databases.create()
    client = start_server(url, PERGAM_MAX_PINNED="3")
    with psycopg.connect(url, autocommit=True) as conn:
        yield client, conn


def test_pinning_past_the_cap_is_409_and_changes_nothing(capped: tuple[Client, psycopg.Connection]) -> None:
    client, db = capped
    ids = [client.publish(title=f"Doc {n}", tags=["old"])["id"] for n in range(4)]
    for pid in ids[:3]:
        patch_meta(client, pid, {"pinned": True})
    before = db.execute("SELECT * FROM pergam_meta ORDER BY id").fetchall()

    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    try:
        conn.request("PATCH", f"/{ids[3]}/meta", b'{"pinned": true, "tags": ["new"]}', {"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode()
    finally:
        conn.close()

    assert (resp.status, resp.reason) == (409, "Conflict")
    assert "3 pergams are already pinned, the most this server allows (3); unpin one first" in body
    assert db.execute("SELECT * FROM pergam_meta ORDER BY id").fetchall() == before


def test_re_pinning_and_every_other_change_work_at_the_cap(capped: tuple[Client, psycopg.Connection]) -> None:
    client, db = capped
    ids = [client.publish(title=f"Doc {n}")["id"] for n in range(4)]
    for pid in ids[:3]:
        patch_meta(client, pid, {"pinned": True})
    stamp = db.execute("SELECT pinned_at FROM pergam_meta WHERE id = %s", (ids[0],)).fetchone()

    assert patch(client, ids[0], {"pinned": True, "tags": ["x"]}).json()["pinned"] is True
    assert patch(client, ids[3], {"tags": ["y"]}).status == 200
    assert patch(client, ids[3], {"archived": True}).status == 200
    assert db.execute("SELECT pinned_at FROM pergam_meta WHERE id = %s", (ids[0],)).fetchone() == stamp
    assert patch(client, ids[1], {"pinned": False}).json()["pinned"] is False
    assert patch(client, ids[2], {"pinned": True}).status == 200


def test_concurrent_pins_never_pass_the_cap(client: Client, db: psycopg.Connection) -> None:
    ids = [client.publish(title=f"Doc {n}")["id"] for n in range(16)]
    barrier = threading.Barrier(len(ids))
    statuses: list[int] = []
    lock = threading.Lock()

    def pin(pid: str) -> None:
        barrier.wait()
        status = patch(client, pid, {"pinned": True}).status
        with lock:
            statuses.append(status)

    threads = [threading.Thread(target=pin, args=(pid,)) for pid in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(statuses) == [200] * 12 + [409] * 4
    assert len(pinned_ids(db)) == 12


def test_a_cap_of_zero_turns_pinning_off(databases, start_server) -> None:
    url = databases.create()
    client = start_server(url, PERGAM_MAX_PINNED="0")
    pid = client.publish()["id"]
    with psycopg.connect(url, autocommit=True) as db:
        db.execute("INSERT INTO pergam_meta (id, pinned) VALUES (%s, true)", (pid,))
        other = client.publish()["id"]

        resp = patch(client, other, {"pinned": True})

        assert resp.status == 409
        assert "Pinning is turned off on this server" in resp.text
        assert patch(client, pid, {"pinned": False}).json()["pinned"] is False


def test_an_instance_over_the_cap_keeps_its_pins_until_back_under(capped: tuple[Client, psycopg.Connection]) -> None:
    client, db = capped
    ids = [client.publish(title=f"Doc {n}")["id"] for n in range(6)]
    for pid in ids[:5]:
        db.execute("INSERT INTO pergam_meta (id, pinned) VALUES (%s, true)", (pid,))

    page = client.get("/").text

    assert len(cards(page)) == 5
    assert '<span class="pins__count">5 of 3 pinned</span>' in page
    assert patch(client, ids[5], {"pinned": True}).status == 409
    for pid in ids[:2]:
        assert patch(client, pid, {"pinned": False}).status == 200
    assert patch(client, ids[5], {"pinned": True}).status == 409
    assert patch(client, ids[2], {"pinned": False}).status == 200
    assert patch(client, ids[5], {"pinned": True}).status == 200


def test_stats_name_the_pinned_count_and_the_cap(capped: tuple[Client, psycopg.Connection]) -> None:
    client, _ = capped
    pid = client.publish()["id"]
    patch_meta(client, pid, {"pinned": True})

    stats = client.get("/api/pergams").json()["stats"]

    assert (stats["pinned"], stats["max_pinned"]) == (1, 3)
    assert '<span class="pins__count">1 of 3 pinned</span>' in client.get("/").text


def test_cards_are_ordered_by_when_they_were_pinned(client: Client, db: psycopg.Connection) -> None:
    a, b, c = (client.publish(title=title)["id"] for title in ("A", "B", "C"))
    db.execute("INSERT INTO pergam_meta (id, pinned) VALUES (%s, true)", (c,))

    patch_meta(client, a, {"pinned": True})
    patch_meta(client, b, {"pinned": True})
    assert cards(client.get("/").text) == [b, a, c]
    assert cards(client.get("/?sort=title").text) == [b, a, c]

    patch_meta(client, a, {"pinned": False})
    patch_meta(client, a, {"pinned": True})
    assert cards(client.get("/").text) == [a, b, c]

    patch_meta(client, b, {"pinned": True, "tags": ["again"]})
    client.publish(id=b, html="<p>newer</p>")
    assert cards(client.get("/").text) == [a, b, c]
    assert card_order(client) == [a, b, c]


def test_the_main_list_and_api_order_do_not_follow_pin_time(client: Client) -> None:
    a, b = (client.publish(title=title)["id"] for title in ("A", "B"))
    patch_meta(client, b, {"pinned": True})
    patch_meta(client, a, {"pinned": True})

    assert [row["id"] for row in client.get("/api/pergams").json()["pergams"]] == [b, a]


def test_pinned_at_is_exposed_and_cleared_on_unpin(client: Client) -> None:
    pid = client.publish()["id"]

    patch_meta(client, pid, {"pinned": True})
    (row,) = client.get("/api/pergams").json()["pergams"]
    one = client.get(f"/api/pergams/{pid}").json()

    assert datetime.fromisoformat(row["pinned_at"]).tzinfo is not None
    assert one["pinned_at"] == row["pinned_at"]
    patch_meta(client, pid, {"pinned": False})
    assert client.get(f"/api/pergams/{pid}").json()["pinned_at"] is None


def test_archiving_unpins_and_unarchiving_does_not_pin_again(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish(tags=["keep"])["id"]
    patch_meta(client, pid, {"pinned": True})

    archived = patch(client, pid, {"archived": True}).json()
    restored = patch(client, pid, {"archived": False}).json()

    assert archived == {"id": pid, "tags": ["keep"], "archived": True, "pinned": False}
    assert restored == {"id": pid, "tags": ["keep"], "archived": False, "pinned": False}
    assert db.execute("SELECT pinned_at FROM pergam_meta WHERE id = %s", (pid,)).fetchone() == (None,)


def test_pinning_an_archived_pergam_is_refused(client: Client) -> None:
    pid = client.publish()["id"]
    patch_meta(client, pid, {"archived": True})

    resp = patch(client, pid, {"pinned": True})

    assert resp.status == 409
    assert "An archived pergam cannot be pinned; unarchive it first" in resp.text
    assert client.get(f"/{pid}/meta").json()["pinned"] is False


def test_archiving_and_pinning_in_one_patch_is_400(client: Client) -> None:
    pid = client.publish()["id"]

    resp = patch(client, pid, {"archived": True, "pinned": True})

    assert resp.status == 400
    assert "archived and pinned cannot both be true" in resp.text
    assert client.get(f"/{pid}/meta").json() == {"id": pid, "tags": [], "archived": False, "pinned": False}


def test_rows_left_archived_and_pinned_are_no_cards_no_count_and_normalise_on_write(
    capped: tuple[Client, psycopg.Connection],
) -> None:
    client, db = capped
    old = client.publish(title="Old")["id"]
    ids = [client.publish(title=f"Doc {n}")["id"] for n in range(3)]
    db.execute("INSERT INTO pergam_meta (id, archived, pinned) VALUES (%s, true, true)", (old,))

    for pid in ids:
        assert patch(client, pid, {"pinned": True}).status == 200
    page = client.get("/?archived=1").text

    assert old not in cards(page)
    assert client.get("/api/pergams").json()["stats"]["pinned"] == 3
    assert patch(client, old, {"tags": ["touched"]}).json() == {"id": old, "tags": ["touched"], "archived": True, "pinned": False}


def export_doc(pid: str, pinned: bool, archived: bool = False, pinned_at: object = "absent") -> dict:
    meta: dict = {"tags": [], "archived": archived, "pinned": pinned}
    if pinned_at != "absent":
        meta["pinned_at"] = pinned_at
    return {
        "format_version": 1,
        "id": pid,
        "meta": meta,
        "versions": [
            {"version": 1, "title": "T", "html": "<p>x</p>", "type": "otro", "author": "you@example.com",
             "note": None, "created_at": "2025-01-02T03:04:05+00:00"}
        ],
    }


@pytest.mark.parametrize(
    ("pinned_at", "expected"),
    [("2025-02-03T04:05:06+00:00", "2025-02-03T04:05:06+00:00"), (None, "now"), ("absent", "now")],
)
def test_import_keeps_or_stamps_the_pin_time(client: Client, pinned_at: object, expected: str) -> None:
    resp = client.post_json(export_doc("abc123ef", True, pinned_at=pinned_at), "/import")

    assert resp.status == 201
    got = client.get("/api/pergams/abc123ef").json()["pinned_at"]
    if expected == "now":
        assert datetime.fromisoformat(got) > datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    else:
        assert datetime.fromisoformat(got) == datetime.fromisoformat(expected)
    assert client.get("/abc123ef/export").json()["meta"]["pinned_at"] == got


def test_import_rejects_a_malformed_pin_time(client: Client) -> None:
    resp = client.post_json(export_doc("abc123ef", True, pinned_at="yesterday"), "/import")

    assert resp.status == 400
    assert "meta.pinned_at must be an ISO 8601 timestamp" in resp.text


def test_import_over_the_cap_or_archived_arrives_unpinned(capped: tuple[Client, psycopg.Connection]) -> None:
    client, _ = capped
    for n in range(3):
        patch_meta(client, client.publish(title=f"Doc {n}")["id"], {"pinned": True})

    assert client.post_json(export_doc("abc123ef", True), "/import").status == 201
    assert client.post_json(export_doc("abc123ff", True, archived=True), "/import").status == 201

    assert client.get("/api/pergams/abc123ef").json()["pinned"] is False
    archived = client.get("/api/pergams/abc123ff").json()
    assert (archived["archived"], archived["pinned"], archived["pinned_at"]) == (True, False, None)
