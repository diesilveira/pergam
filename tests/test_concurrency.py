from __future__ import annotations

import threading

import psycopg

from support import Client, Response

WRITERS = 16


def post_concurrently(client: Client, payload: dict, tags: list[list[str]] | None = None) -> list[Response]:
    barrier = threading.Barrier(WRITERS)
    responses: list[Response] = []
    lock = threading.Lock()

    def post(index: int) -> None:
        barrier.wait()
        resp = client.post_json(payload if tags is None else {**payload, "tags": tags[index]})
        with lock:
            responses.append(resp)

    threads = [threading.Thread(target=post, args=(i,)) for i in range(WRITERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return responses


def test_concurrent_posts_to_one_id_get_consecutive_versions(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish()["id"]
    payload = {"id": pid, "title": "t", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"}

    responses = post_concurrently(client, payload)

    assert [r.status for r in responses] == [201] * WRITERS
    assert sorted(r.json()["version"] for r in responses) == list(range(2, WRITERS + 2))
    assert db.execute("SELECT array_agg(version ORDER BY version) FROM pergams WHERE id = %s", (pid,)).fetchone() == (
        list(range(1, WRITERS + 2)),
    )


def test_concurrent_posts_to_a_new_explicit_id_start_at_one(client: Client) -> None:
    payload = {"id": "c0ffee01", "title": "t", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"}

    responses = post_concurrently(client, payload)

    assert [r.status for r in responses] == [201] * WRITERS
    assert sorted(r.json()["version"] for r in responses) == list(range(1, WRITERS + 1))


def test_concurrent_new_pergams_get_distinct_ids(client: Client) -> None:
    payload = {"title": "t", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"}

    responses = post_concurrently(client, payload)

    assert [r.status for r in responses] == [201] * WRITERS
    assert len({r.json()["id"] for r in responses}) == WRITERS
    assert {r.json()["version"] for r in responses} == {1}


def test_concurrent_tag_merges_keep_every_tag(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish()["id"]
    payload = {"id": pid, "title": "t", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"}

    responses = post_concurrently(client, payload, tags=[[f"tag-{i:02d}"] for i in range(WRITERS)])

    assert [r.status for r in responses] == [201] * WRITERS
    assert db.execute("SELECT tags FROM pergam_meta WHERE id = %s", (pid,)).fetchone() == (
        [f"tag-{i:02d}" for i in range(WRITERS)],
    )


def test_concurrent_posts_are_dated_in_version_order(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish()["id"]
    payload = {"id": pid, "title": "t", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"}

    post_concurrently(client, payload)

    dates = [row[0] for row in db.execute("SELECT created_at FROM pergams WHERE id = %s ORDER BY version", (pid,))]
    assert len(dates) == WRITERS + 1
    assert dates == sorted(dates)


def test_a_new_version_is_never_dated_before_the_previous_one(client: Client, db: psycopg.Connection) -> None:
    pid = client.publish()["id"]
    db.execute("UPDATE pergams SET created_at = now() + interval '1 hour' WHERE id = %s", (pid,))

    client.publish(id=pid)

    first, second = (row[0] for row in db.execute("SELECT created_at FROM pergams WHERE id = %s ORDER BY version", (pid,)))
    assert second == first


def test_a_concurrently_published_id_exports_and_imports_cleanly(client: Client, target: Client) -> None:
    pid = client.publish()["id"]
    post_concurrently(client, {"id": pid, "title": "t", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"})
    exported = client.get(f"/{pid}/export").json()

    resp = target.post_json(exported, "/import")

    assert resp.status == 201
    assert resp.json()["imported"] == list(range(1, WRITERS + 2))
    assert target.get(f"/{pid}/export").json()["versions"] == exported["versions"]
