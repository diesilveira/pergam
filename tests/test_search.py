from __future__ import annotations

import shutil
import time
from pathlib import Path
from urllib.parse import quote

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from pergam import store
from pergam.backfill import backfill_text
from pergam.migrate import apply_migrations
from pergam.search import EARLY_HEADLINE_CHARS, INDEXED_BODY_CHARS, MAX_QUERY_CHARS
from pergam.text import EXTRACTOR_VERSION
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

    assert '<span class="row__snippet">' in page
    assert "<mark>downtime</mark>" in page


def test_title_only_match_has_no_snippet(client: Client, searchable: dict[str, str]) -> None:
    assert '<span class="row__snippet">' not in client.get("/?q=budget").text


def test_snippet_escapes_body_text_except_the_highlight(client: Client) -> None:
    client.publish(html="<p>if a &lt; b then &lt;img src=x onerror=alert(1)&gt; needle \ue000 here</p>")

    page = client.get("/?q=needle").text

    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<img src=x" not in page
    assert "<mark>needle</mark>" in page
    assert "\ue000" not in page


def test_matching_count_includes_body_matches(client: Client, searchable: dict[str, str]) -> None:
    assert ">2 of 3<" in client.get("/?q=downtime").text


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
        ("aaaa0001", 1, "plan v1"),
        ("aaaa0001", 2, "plan v2"),
        ("bbbb0002", 1, "ñ"),
    ]
    assert text_rows(legacy_pool) == filled
    with legacy_pool.connection() as conn:
        assert conn.execute(FINGERPRINT).fetchone() == fingerprint


def test_backfill_resumes_with_what_is_missing(legacy_pool: ConnectionPool) -> None:
    with legacy_pool.connection() as conn:
        conn.execute(
            "INSERT INTO pergam_text (id, version, body, document, extractor) VALUES ('aaaa0001', 2, 'kept', '', %s)",
            (EXTRACTOR_VERSION,),
        )

    assert backfill_text(legacy_pool, batch_size=1) == len(LEGACY_ROWS) - 1
    assert ("aaaa0001", 2, "kept") in text_rows(legacy_pool)


def test_backfill_re_extracts_rows_from_an_older_extractor(legacy_pool: ConnectionPool) -> None:
    with legacy_pool.connection() as conn:
        conn.execute(
            "INSERT INTO pergam_text (id, version, body, document, extractor) VALUES"
            " ('aaaa0001', 1, 'stale', '', 1), ('aaaa0001', 2, 'newer', '', %s)",
            (EXTRACTOR_VERSION + 1,),
        )

    assert backfill_text(legacy_pool) == len(LEGACY_ROWS) - 1
    assert backfill_text(legacy_pool) == 0

    assert text_rows(legacy_pool) == [("aaaa0001", 1, "plan v1"), ("aaaa0001", 2, "newer"), ("bbbb0002", 1, "ñ")]
    with legacy_pool.connection() as conn:
        assert conn.execute("SELECT DISTINCT extractor FROM pergam_text ORDER BY 1").fetchall() == [
            (EXTRACTOR_VERSION,),
            (EXTRACTOR_VERSION + 1,),
        ]


def test_backfill_logs_and_skips_a_document_the_extractor_fails_on(
    legacy_pool: ConnectionPool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_extract = store.extract_text

    def failing_on_v1(html: str):
        if "plan v1" in html:
            raise RuntimeError("extractor bug")
        return real_extract(html)

    monkeypatch.setattr(store, "extract_text", failing_on_v1)

    assert backfill_text(legacy_pool, batch_size=1) == len(LEGACY_ROWS)

    assert text_rows(legacy_pool) == [("aaaa0001", 1, ""), ("aaaa0001", 2, "plan v2"), ("bbbb0002", 1, "ñ")]
    assert "text extraction failed for aaaa0001 v1: RuntimeError('extractor bug')" in capsys.readouterr().err


def test_upgraded_instance_re_extracts_text_from_the_first_extractor(
    databases: DatabaseFactory, start_server, tmp_path: Path
) -> None:
    url = databases.create()
    first_release = tmp_path / "migrations"
    first_release.mkdir()
    for migration in sorted(MIGRATIONS_DIR.glob("00[1-3]_*.sql")):
        shutil.copy(migration, first_release)
    with psycopg.connect(url, autocommit=True) as conn:
        apply_migrations(conn, first_release)
        conn.execute(
            "INSERT INTO pergams (id, version, title, html, type, author, bytes)"
            " VALUES ('cccc0003', 1, 'Headless', '<head><title>T</title><body><p>omitted head end tag</p>', 'otro',"
            " 'you@example.com', 60)"
        )
        conn.execute("INSERT INTO pergam_text (id, version, body, document) VALUES ('cccc0003', 1, '', '')")
        fingerprint = conn.execute(FINGERPRINT).fetchone()

    client = start_server(url)
    deadline = time.monotonic() + 10
    with psycopg.connect(url, autocommit=True) as conn:
        while conn.execute("SELECT extractor FROM pergam_text").fetchone() != (EXTRACTOR_VERSION,):
            assert time.monotonic() < deadline, "stale text was not re-extracted"
            time.sleep(0.1)
        assert conn.execute("SELECT body FROM pergam_text").fetchone() == ("omitted head end tag",)
        assert conn.execute(FINGERPRINT).fetchone() == fingerprint

    assert listed_ids(client.get("/?q=omitted").text, "cccc0003") == {"cccc0003"}
    assert client.get("/cccc0003/text").text == "omitted head end tag\n"


def test_snippet_finds_a_match_far_into_the_body(client: Client) -> None:
    filler = "<p>" + "lorem ipsum dolor " * (EARLY_HEADLINE_CHARS // 18 + 100) + "</p>"
    client.publish(html=f"{filler}<p>the faraway word</p>")

    assert "<mark>faraway</mark>" in client.get("/?q=faraway").text


def test_body_text_past_the_indexed_prefix_is_not_searchable(client: Client) -> None:
    filler = "<p>" + "lorem ipsum dolor " * (INDEXED_BODY_CHARS // 18 + 1) + "</p>"
    pid = client.publish(html=f"<p>opening word</p>{filler}<p>trailing word</p>")["id"]

    assert listed_ids(client.get("/?q=opening").text, pid) == {pid}
    assert listed_ids(client.get("/?q=trailing").text, pid) == set()


@pytest.mark.parametrize("path", ["/", "/api/pergams"])
def test_q_is_capped(client: Client, path: str) -> None:
    assert client.get(f"{path}?q={'a' * MAX_QUERY_CHARS}").status == 200
    resp = client.get(f"{path}?q={'a' * (MAX_QUERY_CHARS + 1)}")

    assert resp.status == 400
    assert f"q must be at most {MAX_QUERY_CHARS} characters" in resp.text


def test_hidden_text_is_not_searched_but_raw_and_export_keep_it(client: Client) -> None:
    html = '<p>shown words</p><div hidden>concealed</div><select><option>dropdownword</select>'
    pid = client.publish(html=html)["id"]

    assert listed_ids(client.get("/?q=concealed").text, pid) == set()
    assert listed_ids(client.get("/?q=dropdownword").text, pid) == set()
    assert listed_ids(client.get("/?q=shown").text, pid) == {pid}
    assert client.get(f"/{pid}/text").text == "shown words\n"
    assert client.get(f"/{pid}/raw").text == html
    assert client.get(f"/{pid}/export").json()["versions"][0]["html"] == html


def test_backfill_drops_hidden_text_stored_by_the_previous_extractor(legacy_pool: ConnectionPool) -> None:
    with legacy_pool.connection() as conn:
        conn.execute("UPDATE pergams SET html = '<p>plan v2</p><p hidden>stale secret</p>' WHERE id = 'aaaa0001' AND version = 2")
        conn.execute(
            "INSERT INTO pergam_text (id, version, body, document, extractor)"
            " VALUES ('aaaa0001', 2, 'plan v2\nstale secret', '', 2)"
        )

    backfill_text(legacy_pool)

    assert ("aaaa0001", 2, "plan v2") in text_rows(legacy_pool)
    assert EXTRACTOR_VERSION > 2
