"""Fixtures: one throwaway Postgres per run (or `TEST_DATABASE_URL`), isolated databases per run/test."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import psycopg
import pytest

from support import REPO_ROOT, Client, DatabaseFactory, ServerProcess, throwaway_postgres

LEGACY_SCHEMA = REPO_ROOT / "db" / "schema.sql"


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


def apply_schema(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(LEGACY_SCHEMA.read_text())


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
    url = databases.create()
    apply_schema(url)
    return url


@pytest.fixture(scope="session")
def session_server(session_database: str, start_server: StartServer) -> Client:
    return start_server(session_database)


@pytest.fixture
def db(session_database: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(session_database, autocommit=True) as conn:
        conn.execute("TRUNCATE pergams")
        yield conn


@pytest.fixture
def client(session_server: Client, db: psycopg.Connection) -> Client:
    return session_server
