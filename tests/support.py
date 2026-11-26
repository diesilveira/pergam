"""Test infrastructure: throwaway Postgres, real server processes and a tiny HTTP client."""

from __future__ import annotations

import http.client
import html as html_lib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = REPO_ROOT / "src" / "pergam" / "server.py"
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
LEGACY_SCHEMA = REPO_ROOT / "tests" / "fixtures" / "legacy_schema.sql"
POSTGRES_IMAGE = "postgres:15-alpine"
TARGET_PUBLIC_URL = "https://team.example.com"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def with_dbname(url: str, dbname: str) -> str:
    params = conninfo_to_dict(url)
    params["dbname"] = dbname
    return make_conninfo(**params)


def wait_for_postgres(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(url, connect_timeout=2):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


@contextmanager
def throwaway_postgres() -> Iterator[str]:
    """Run a disposable Postgres container on a random port, no named volume, tmpfs data dir."""
    name = f"pergam-test-{uuid.uuid4().hex[:10]}"
    password = uuid.uuid4().hex
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", f"POSTGRES_PASSWORD={password}",
            "-p", "127.0.0.1::5432",
            "--tmpfs", "/var/lib/postgresql/data:rw",
            POSTGRES_IMAGE, "-c", "fsync=off",
        ],
        check=True,
        capture_output=True,
    )
    try:
        mapping = subprocess.run(
            ["docker", "port", name, "5432/tcp"], check=True, capture_output=True, text=True
        ).stdout.split()[0]
        port = mapping.rsplit(":", 1)[1]
        url = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        wait_for_postgres(url)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-fv", name], capture_output=True)


def listed_ids(html: str, *ids: str) -> set[str]:
    """The ids among `ids` that the index page lists."""
    return {pid for pid in ids if f'data-href="/{pid}/view"' in html}


def listed_order(html: str) -> list[str]:
    """Ids the index page lists, in page order."""
    return re.findall(r'data-href="/([a-f0-9]+)/view"', html)


SEEDED = 230


def seed(db: psycopg.Connection) -> dict[str, tuple[bool, bool, bool]]:
    """Ids with shared timestamps (to exercise the id tie-break), some pinned, archived or tagged.

    Ids with none of those flags get no `pergam_meta` row on every other index, so pages mix
    rows with and without metadata.
    """
    flags: dict[str, tuple[bool, bool, bool]] = {}
    with db.cursor() as cur:
        for i in range(SEEDED):
            pid = f"{i:08x}"
            pinned, archived, tagged = i % 17 == 0, i % 5 == 0, i % 3 == 0
            flags[pid] = (pinned, archived, tagged)
            cur.execute(
                "INSERT INTO pergams (id, version, title, html, type, author, bytes, created_at)"
                " VALUES (%s, 1, %s, '<p>x</p>', %s, 'you@example.com', 8, '2025-01-01'::timestamptz + %s * interval '1 hour')",
                (pid, f"Doc {i}", "plan" if i % 2 else "otro", i // 4),
            )
            if pinned or archived or tagged or i % 2:
                cur.execute(
                    "INSERT INTO pergam_meta (id, tags, archived, pinned) VALUES (%s, %s, %s, %s)",
                    (pid, ["seeded"] if tagged else [], archived, pinned),
                )
    return flags


def crawl(client: Client, path: str) -> list[str]:
    visited: list[str] = []
    pages = 0
    while path:
        resp = client.get(path)
        assert resp.status == 200
        visited.extend(listed_order(resp.text))
        match = re.search(r'<a class="pager__next" href="([^"]+)"', resp.text)
        path = html_lib.unescape(match.group(1)) if match else ""
        pages += 1
        assert pages < 10
    return visited


def patch_meta(client: Client, pergam_id: str, payload: dict) -> None:
    resp = client.request(
        "PATCH", f"/{pergam_id}/meta", json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"}
    )
    assert resp.status == 200, resp.body


def apply_legacy_schema(database_url: str) -> None:
    """Initialise a database the pre-migrations way (the old `docker-entrypoint-initdb.d` script)."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(LEGACY_SCHEMA.read_text())


LEGACY_ROWS = [
    ("aaaa0001", 1, "Plan", "<h1>plan v1</h1>", "plan", "ana@example.com", 16, "2025-01-02T03:04:05+00:00"),
    ("aaaa0001", 2, "Plan", "<h1>plan v2</h1>", "plan", "ana@example.com", 16, "2025-02-02T03:04:05+00:00"),
    ("bbbb0002", 1, "Ñandú — 日本", "<p>ñ</p>", "informe", "bo@example.com", 9, "2025-03-04T05:06:07+00:00"),
]

FINGERPRINT = "SELECT count(*), md5(string_agg(p::text, '|' ORDER BY id, version)) FROM pergams p"


def seed_legacy_rows(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO pergams (id, version, title, html, type, author, bytes, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            LEGACY_ROWS,
        )


class DatabaseFactory:
    """Creates isolated databases on an admin connection and drops them on close."""

    def __init__(self, admin_url: str) -> None:
        self.admin_url = admin_url
        self._created: list[str] = []

    def create(self) -> str:
        dbname = f"pergam_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{dbname}"')
        self._created.append(dbname)
        return with_dbname(self.admin_url, dbname)

    def close(self) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            for dbname in self._created:
                conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        self._created.clear()


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=dict(headers or {}))
            resp = conn.getresponse()
            return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read())
        finally:
            conn.close()

    def get(self, path: str) -> Response:
        return self.request("GET", path)

    def post_json(self, payload: Any, path: str = "/pergam") -> Response:
        return self.request(
            "POST", path, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"}
        )

    def publish(self, **fields: Any) -> dict[str, Any]:
        payload = {"title": "Doc", "html": "<h1>doc</h1>", "type": "otro", "author": "you@example.com"}
        payload.update(fields)
        resp = self.post_json(payload)
        assert resp.status == 201, resp.body
        return resp.json()


class ServerProcess:
    """The real server, started as `python server.py` the way the Docker image runs it."""

    def __init__(self, database_url: str, log_dir: Path, env: Mapping[str, str] | None = None) -> None:
        self.port = free_port()
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("PUBLIC_", "PG"))},
            "DATABASE_URL": database_url,
            "HOST": "127.0.0.1",
            "PORT": str(self.port),
            **(env or {}),
        }
        self.log_path = log_dir / f"pergam-server-{self.port}.log"
        self.process: subprocess.Popen[bytes] | None = None

    def launch(self) -> None:
        with self.log_path.open("wb") as log:
            self.process = subprocess.Popen(
                [sys.executable, str(SERVER_SCRIPT)], env=self.env, stdout=log, stderr=subprocess.STDOUT
            )

    def start(self, timeout: float = 15.0) -> Client:
        self.launch()
        assert self.process is not None
        client = Client(self.port)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"server exited with {self.process.returncode}:\n{self.log()}")
            try:
                if client.get("/healthz").status == 200:
                    return client
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"server did not become healthy:\n{self.log()}")

    def wait_exit(self, timeout: float = 15.0) -> int:
        assert self.process is not None
        return self.process.wait(timeout=timeout)

    def log(self) -> str:
        return self.log_path.read_text(errors="replace") if self.log_path.exists() else ""

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
