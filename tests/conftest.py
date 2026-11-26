"""Fixtures: one throwaway Postgres per run (or `TEST_DATABASE_URL`), isolated databases per run/test."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import psycopg
import pytest

from support import TARGET_PUBLIC_URL, Client, DatabaseFactory, ServerProcess, throwaway_postgres


@pytest.fixture(scope="session")
def admin_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    with throwaway_postgres() as url:
        yield url


@pytest.fixture(scope="session")
def databases(admin_url: str) -> Iterator[DatabaseFactory]:
    factory = DatabaseFactory(admin_url)
    yield factory
    factory.close()


@pytest.fixture(scope="session")
def server_logs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("server-logs")


StartServer = Callable[..., Client]


@pytest.fixture(scope="session")
def start_server(server_logs: Path) -> Iterator[StartServer]:
    """Start an extra server process (custom env) against a given database."""
    with ExitStack() as stack:

        def start(database_url: str, **env: str) -> Client:
            server = ServerProcess(database_url, server_logs, env)
            stack.callback(server.stop)
            return server.start()

        yield start


@pytest.fixture(scope="session")
def session_database(databases: DatabaseFactory) -> str:
    return databases.create()


@pytest.fixture(scope="session")
def session_server(session_database: str, start_server: StartServer) -> Client:
    return start_server(session_database)


TABLES = "pergams, pergam_meta, pergam_text, pergam_note"


@pytest.fixture
def db(session_database: str, session_server: Client) -> Iterator[psycopg.Connection]:
    with psycopg.connect(session_database, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {TABLES}")
        yield conn


@pytest.fixture
def client(session_server: Client, db: psycopg.Connection) -> Client:
    return session_server


@pytest.fixture(scope="session")
def target_database(databases: DatabaseFactory) -> str:
    return databases.create()


@pytest.fixture(scope="session")
def target_server(target_database: str, start_server: StartServer) -> Client:
    """A second server with its own database, the other end of an export and import."""
    return start_server(target_database, PUBLIC_URL=TARGET_PUBLIC_URL)


@pytest.fixture
def target_db(target_database: str, target_server: Client) -> Iterator[psycopg.Connection]:
    with psycopg.connect(target_database, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {TABLES}")
        yield conn


@pytest.fixture
def target(target_server: Client, target_db: psycopg.Connection) -> Client:
    return target_server
