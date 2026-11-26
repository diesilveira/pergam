from __future__ import annotations

import time
from urllib.parse import quote

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from pergam.backfill import backfill_text
from pergam.migrate import apply_migrations
from support import (
    FINGERPRINT,
    LEGACY_ROWS,
    MIGRATIONS_DIR,
    Client,
    DatabaseFactory,
    apply_legacy_schema,
    listed_ids,
    seed_legacy_rows,
)

BODY = (
    "<html><head><style>.hidden-style-word{}</style></head><body>"
    "<h1>Quarterly numbers</h1><p>The migration finished without downtime across every region.</p>"
    "<script>const scriptOnlyWord = 1;</script></body></html>"
)


@pytest.fixture
def searchable(client: Client) -> dict[str, str]:
    return {
        "body": client.publish(title="Ops review", html=BODY)["id"],
        "title": client.publish(title="Downtime budget", html="<p>nothing else here</p>")["id"],
        "other": client.publish(title="Lunch menu", html="<p>soup and bread</p>")["id"],
    }


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("finished without downtime", {"body"}),
        ('"without downtime"', {"body"}),
        ("downtime", {"body", "title"}),
        ("DOWNTIME", {"body", "title"}),
        ("budg", {"title"}),
        ("regions", set()),
        ("scriptOnlyWord", set()),
        ("hidden-style-word", set()),
    ],
)
def test_q_matches_title_or_body_text_only(
    client: Client, searchable: dict[str, str], q: str, expected: set[str]
) -> None:
    resp = client.get(f"/?q={quote(q)}")

    assert resp.status == 200
    assert listed_ids(resp.text, *searchable.values()) == {searchable[k] for k in expected}


def test_only_the_latest_version_is_searched(client: Client) -> None:
    first = client.publish(html="<p>alpha keyword</p>")
    client.publish(id=first["id"], html="<p>beta keyword</p>")

    assert listed_ids(client.get("/?q=beta").text, first["id"]) == {first["id"]}
    assert listed_ids(client.get("/?q=alpha").text, first["id"]) == set()


def test_body_match_shows_a_highlighted_snippet(client: Client, searchable: dict[str, str]) -> None:
    page = client.get("/?q=downtime").text

    assert '<p class="row__snippet">' in page
    assert "<mark>downtime</mark>" in page


def test_title_only_match_has_no_snippet(client: Client, searchable: dict[str, str]) -> None:
    assert '<p class="row__snippet">' not in client.get("/?q=budget").text


def test_snippet_escapes_body_text_except_the_highlight(client: Client) -> None:
    client.publish(html="<p>if a &lt; b then &lt;img src=x onerror=alert(1)&gt; needle  here</p>")

    page = client.get("/?q=needle").text

    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<img src=x" not in page
    assert "<mark>needle</mark>" in page
    assert "" not in page


def test_matching_count_includes_body_matches(client: Client, searchable: dict[str, str]) -> None:
    assert "showing 2 of 3" in client.get("/?q=downtime").text


def wait_for_text_rows(url: str, expected: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    with psycopg.connect(url, autocommit=True) as conn:
        while conn.execute("SELECT count(*) FROM pergam_text").fetchone()[0] < expected:
            assert time.monotonic() < deadline, "backfill did not finish"
            time.sleep(0.1)


def test_existing_rows_are_searchable_after_upgrading(databases: DatabaseFactory, start_server) -> None:
    url = databases.create()
    apply_legacy_schema(url)
    with psycopg.connect(url, autocommit=True) as conn:
        seed_legacy_rows(conn)
        fingerprint = conn.execute(FINGERPRINT).fetchone()

    client = start_server(url)
    wait_for_text_rows(url, len(LEGACY_ROWS))

    assert listed_ids(client.get("/?q=v2").text, "aaaa0001", "bbbb0002") == {"aaaa0001"}
    assert listed_ids(client.get("/?q=v1").text, "aaaa0001", "bbbb0002") == set()
    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute(FINGERPRINT).fetchone() == fingerprint


@pytest.fixture
def legacy_pool(databases: DatabaseFactory):
    url = databases.create()
    apply_legacy_schema(url)
    with psycopg.connect(url, autocommit=True) as conn:
        seed_legacy_rows(conn)
        apply_migrations(conn, MIGRATIONS_DIR)
    with ConnectionPool(url, min_size=1, max_size=2, kwargs={"autocommit": True}) as pool:
        yield pool


def text_rows(pool: ConnectionPool) -> list[tuple[str, int, str]]:
    with pool.connection() as conn:
        return conn.execute("SELECT id, version, body FROM pergam_text ORDER BY 1, 2").fetchall()


def test_backfill_fills_every_version_in_batches_and_is_idempotent(legacy_pool: ConnectionPool) -> None:
    with legacy_pool.connection() as conn:
        fingerprint = conn.execute(FINGERPRINT).fetchone()

    assert backfill_text(legacy_pool, batch_size=2) == len(LEGACY_ROWS)
    filled = text_rows(legacy_pool)
    assert backfill_text(legacy_pool, batch_size=2) == 0

    assert filled == [
        ("aaaa0001", 1, "# plan v1\n"),
        ("aaaa0001", 2, "# plan v2\n"),
        ("bbbb0002", 1, "ñ\n"),
    ]
    assert text_rows(legacy_pool) == filled
    with legacy_pool.connection() as conn:
        assert conn.execute(FINGERPRINT).fetchone() == fingerprint


def test_backfill_resumes_with_what_is_missing(legacy_pool: ConnectionPool) -> None:
    with legacy_pool.connection() as conn:
        conn.execute(
            "INSERT INTO pergam_text (id, version, body, document) VALUES ('aaaa0001', 2, 'kept', ''::tsvector)"
        )

    assert backfill_text(legacy_pool, batch_size=1) == len(LEGACY_ROWS) - 1
    assert ("aaaa0001", 2, "kept") in text_rows(legacy_pool)
