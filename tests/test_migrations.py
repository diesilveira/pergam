from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from pergam.migrate import MigrationError, apply_migrations, discover
from support import MIGRATIONS_DIR, DatabaseFactory, ServerProcess, apply_legacy_schema

LEGACY_ROWS = [
    ("aaaa0001", 1, "Plan", "<h1>plan v1</h1>", "plan", "ana@example.com", 16, "2025-01-02T03:04:05+00:00"),
    ("aaaa0001", 2, "Plan", "<h1>plan v2</h1>", "plan", "ana@example.com", 16, "2025-02-02T03:04:05+00:00"),
    ("bbbb0002", 1, "Ñandú — 日本", "<p>ñ</p>", "informe", "bo@example.com", 9, "2025-03-04T05:06:07+00:00"),
]

SCHEMA_QUERIES = {
    "columns": """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name IN ('pergams', 'pergams_latest')
        ORDER BY 1, 2
    """,
    "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'pergams' ORDER BY 1",
    "constraints": """
        SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'pergams'::regclass ORDER BY 1
    """,
    "view": "SELECT pg_get_viewdef('pergams_latest'::regclass)",
}

OIDS = """
    SELECT c.relname, c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname LIKE 'pergams%' ORDER BY 1
"""

FINGERPRINT = "SELECT count(*), md5(string_agg(p::text, '|' ORDER BY id, version)) FROM pergams p"


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True)


def schema_of(conn: psycopg.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {name: conn.execute(query).fetchall() for name, query in SCHEMA_QUERIES.items()}


def seed_legacy_rows(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO pergams (id, version, title, html, type, author, bytes, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            LEGACY_ROWS,
        )


def write_migrations(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir()
    for name, body in files.items():
        (directory / name).write_text(body)
    return directory


def test_repository_migrations_are_well_formed() -> None:
    migrations = discover(MIGRATIONS_DIR)

    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "001_initial_schema"


def test_fresh_database_gets_schema_and_ledger(databases: DatabaseFactory) -> None:
    with connect(databases.create()) as conn:
        applied = apply_migrations(conn, MIGRATIONS_DIR)

        assert [m.name for m in applied] == [m.name for m in discover(MIGRATIONS_DIR)]
        assert conn.execute("SELECT version, name FROM schema_migrations ORDER BY 1").fetchall() == [
            (m.version, m.name) for m in applied
        ]
        assert conn.execute("SELECT count(*) FROM pergams_latest").fetchone() == (0,)


def test_rerunning_is_a_no_op(databases: DatabaseFactory) -> None:
    with connect(databases.create()) as conn:
        apply_migrations(conn, MIGRATIONS_DIR)

        assert apply_migrations(conn, MIGRATIONS_DIR) == []


def test_legacy_database_keeps_every_row_and_object(databases: DatabaseFactory) -> None:
    url = databases.create()
    apply_legacy_schema(url)
    with connect(url) as conn:
        seed_legacy_rows(conn)
        fingerprint, schema, oids = conn.execute(FINGERPRINT).fetchone(), schema_of(conn), conn.execute(OIDS).fetchall()

        apply_migrations(conn, MIGRATIONS_DIR)

        assert conn.execute(FINGERPRINT).fetchone() == fingerprint
        assert fingerprint[0] == len(LEGACY_ROWS)
        assert schema_of(conn) == schema
        assert conn.execute(OIDS).fetchall() == oids
        assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]


def test_legacy_and_fresh_databases_converge(databases: DatabaseFactory) -> None:
    legacy_url, fresh_url = databases.create(), databases.create()
    apply_legacy_schema(legacy_url)
    with connect(legacy_url) as legacy, connect(fresh_url) as fresh:
        apply_migrations(legacy, MIGRATIONS_DIR)
        apply_migrations(fresh, MIGRATIONS_DIR)

        assert schema_of(legacy) == schema_of(fresh)


def test_server_serves_a_legacy_database(databases: DatabaseFactory, start_server) -> None:
    url = databases.create()
    apply_legacy_schema(url)
    with connect(url) as conn:
        seed_legacy_rows(conn)

    client = start_server(url)

    assert client.get("/aaaa0001/view").text == "<h1>plan v2</h1>"
    assert client.publish(id="aaaa0001")["version"] == 3


def test_failing_migration_rolls_back_and_raises(databases: DatabaseFactory, tmp_path: Path) -> None:
    directory = write_migrations(
        tmp_path / "migrations",
        {
            "001_ok.sql": "CREATE TABLE ok_table (x int);",
            "002_broken.sql": "CREATE TABLE half_done (x int); INSERT INTO ok_table VALUES (1); SELECT 1/0;",
        },
    )
    with connect(databases.create()) as conn:
        with pytest.raises(psycopg.errors.DivisionByZero):
            apply_migrations(conn, directory)

        assert conn.execute("SELECT to_regclass('half_done')").fetchone() == (None,)
        assert conn.execute("SELECT count(*) FROM ok_table").fetchone() == (0,)
        assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
        assert conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        ).fetchone() == (0,)


def test_concurrent_runs_are_serialized(databases: DatabaseFactory, tmp_path: Path) -> None:
    directory = write_migrations(
        tmp_path / "migrations",
        {"001_slow.sql": "CREATE TABLE slow (x int); SELECT pg_sleep(0.5);"},
    )
    url = databases.create()
    results: list[object] = []
    barrier = threading.Barrier(3)

    def run() -> None:
        with connect(url) as conn:
            barrier.wait()
            try:
                results.append(len(apply_migrations(conn, directory)))
            except Exception as exc:
                results.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == [0, 0, 1]


@pytest.mark.parametrize(
    "files",
    [{"1_short.sql": "SELECT 1;"}, {"001_a.sql": "SELECT 1;", "001_b.sql": "SELECT 1;"}],
    ids=["bad-name", "duplicate-version"],
)
def test_invalid_migration_sets_are_rejected(tmp_path: Path, files: dict[str, str]) -> None:
    with pytest.raises(MigrationError):
        discover(write_migrations(tmp_path / "migrations", files))


def test_server_refuses_to_start_when_a_migration_fails(
    databases: DatabaseFactory, tmp_path: Path, server_logs: Path
) -> None:
    directory = write_migrations(tmp_path / "migrations", {"001_broken.sql": "SELECT 1/0;"})
    server = ServerProcess(databases.create(), server_logs, {"MIGRATIONS_DIR": str(directory)})

    server.launch()
    try:
        assert server.wait_exit() != 0
    finally:
        server.stop()

    assert "migrations failed" in server.log()


@contextmanager
def app_role(admin_url: str, database_url: str, grants: list[str]) -> Iterator[str]:
    """A login role that does not own `pergams`, holding only `grants` in that database."""
    name, password = f"pergam_app_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    with connect(admin_url) as admin:
        admin.execute(f"CREATE ROLE {name} LOGIN PASSWORD '{password}'")
    try:
        with connect(database_url) as owner:
            for grant in grants:
                owner.execute(grant.format(role=name))
        params = conninfo_to_dict(database_url)
        params.update(user=name, password=password)
        yield make_conninfo(**params)
    finally:
        with connect(database_url) as owner:
            owner.execute(f"DROP OWNED BY {name}")
        with connect(admin_url) as admin:
            admin.execute(f"DROP ROLE {name}")


APP_GRANTS = [
    "GRANT SELECT, INSERT ON pergams TO {role}",
    "GRANT SELECT ON pergams_latest TO {role}",
]


@pytest.mark.parametrize(
    "ledger_grants",
    [
        ["GRANT CREATE ON SCHEMA public TO {role}"],
        [
            "CREATE TABLE schema_migrations (version int PRIMARY KEY, name text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())",
            "GRANT SELECT, INSERT ON schema_migrations TO {role}",
        ],
    ],
    ids=["role-creates-ledger", "ledger-precreated"],
)
def test_legacy_database_upgrades_with_a_non_owner_role(
    admin_url: str, databases: DatabaseFactory, start_server, ledger_grants: list[str]
) -> None:
    url = databases.create()
    apply_legacy_schema(url)
    with connect(url) as owner:
        seed_legacy_rows(owner)
        fingerprint, schema = owner.execute(FINGERPRINT).fetchone(), schema_of(owner)

    with app_role(admin_url, url, APP_GRANTS + ledger_grants) as role_url:
        with connect(role_url) as conn:
            assert [m.version for m in apply_migrations(conn, MIGRATIONS_DIR)] == [1]
        client = start_server(role_url)

        assert client.get("/aaaa0001/view").text == "<h1>plan v2</h1>"
        assert client.get("/").status == 200
        assert client.publish(id="aaaa0001")["version"] == 3

    with connect(url) as owner:
        assert owner.execute("SELECT count(*) FROM pergams").fetchone() == (len(LEGACY_ROWS) + 1,)
        owner.execute("DELETE FROM pergams WHERE version = 3")
        assert owner.execute(FINGERPRINT).fetchone() == fingerprint
        assert schema_of(owner) == schema
