from __future__ import annotations

import http.server
import json
import shutil
import ssl
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

import pergam_promote
from pergam_profiles import Server
from pergam_promote import PromoteError, missing_versions
from support import REPO_ROOT, Client
from test_transfer import STATE

SCRIPT = REPO_ROOT / "skills" / "post-pergam" / "pergam_promote.py"


@pytest.fixture
def profiles(tmp_path: Path, client: Client, target: Client) -> dict[str, str]:
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "default": "local",
                "servers": {
                    "local": {"url": f"http://127.0.0.1:{client.port}"},
                    "team": {"url": f"http://127.0.0.1:{target.port}"},
                },
            }
        )
    )
    return {"PERGAM_CONFIG": str(path)}


def promote(profiles: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), **profiles}
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def five_versions(client: Client) -> str:
    pid = client.publish(title="Plan", html="<p>v1</p>", tags=["ops"])["id"]
    for n in range(2, 6):
        client.publish(id=pid, title="Plan", html=f"<p>v{n}</p>", note=f"step {n}")
    return pid


def test_five_versions_reach_the_target_in_one_command(client: Client, target: Client, profiles: dict[str, str]) -> None:
    pid = five_versions(client)

    result = promote(profiles, pid, "--from", "local", "--to", "team")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"promoted {pid} from local to team: uploaded v1, v2, v3, v4, v5",
        f"https://team.example.com/{pid}/view",
    ]
    source, copied = client.get(f"/api/pergams/{pid}").json(), target.get(f"/api/pergams/{pid}").json()
    fields = ("version", "title", "created_at", "note", "bytes")
    assert [[v[k] for k in fields] for v in copied["versions"]] == [[v[k] for k in fields] for v in source["versions"]]
    assert copied["tags"] == ["ops"]
    assert copied["origin"] == {"url": f"http://localhost:{client.port}", "id": pid}


def test_running_it_twice_is_a_no_op(
    client: Client, target: Client, target_db: psycopg.Connection, profiles: dict[str, str]
) -> None:
    pid = five_versions(client)
    promote(profiles, pid, "--from", "local", "--to", "team")
    state = target_db.execute(STATE).fetchone()

    result = promote(profiles, pid, "--from", "local", "--to", "team")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"team already has every version of {pid}; nothing to do",
        f"https://team.example.com/{pid}/view",
    ]
    assert target_db.execute(STATE).fetchone() == state


def test_only_missing_versions_are_uploaded(client: Client, target: Client, profiles: dict[str, str]) -> None:
    pid = client.publish(html="<p>v1</p>")["id"]
    client.publish(id=pid, html="<p>v2</p>")
    promote(profiles, pid, "--from", "local", "--to", "team")
    client.publish(id=pid, html="<p>v3</p>", note="third")

    result = promote(profiles, pid, "--from", "local", "--to", "team")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"promoted {pid} from local to team: uploaded v3 (v1, v2 already there)"
    assert [v["note"] for v in target.get(f"/{pid}/versions").json()] == ["third", None, None]


def test_a_diverging_target_stops_before_uploading(
    client: Client, target: Client, target_db: psycopg.Connection, profiles: dict[str, str]
) -> None:
    pid = client.publish(html="<p>ours</p>")["id"]
    client.publish(id=pid, html="<p>ours v2</p>")
    target.publish(id=pid, html="<p>theirs</p>")
    state = target_db.execute(STATE).fetchone()

    result = promote(profiles, pid, "--from", "local", "--to", "team")

    assert result.returncode == 1
    assert "v1 already exist on the target with different content" in result.stderr
    assert target_db.execute(STATE).fetchone() == state


def test_urls_work_in_place_of_names(client: Client, target: Client) -> None:
    pid = client.publish()["id"]

    result = promote({}, pid, "--from", f"http://127.0.0.1:{client.port}", "--to", f"http://127.0.0.1:{target.port}/")

    assert result.returncode == 0, result.stderr
    assert target.get(f"/{pid}/versions").status == 200


@pytest.mark.parametrize(
    ("args", "status", "message"),
    [
        (["abcdef12", "--from", "local", "--to", "team"], 1, "abcdef12 not found on local"),
        (["abcdef12", "--from", "local", "--to", "hosted"], 1, "hosted backend keeps no versions"),
        (["abcdef12", "--from", "local", "--to", "local"], 1, "are the same server"),
        (["abcdef12", "--from", "local", "--to", "staging"], 2, "unknown server 'staging'"),
    ],
)
def test_refusals(profiles: dict[str, str], args: list[str], status: int, message: str) -> None:
    result = promote(profiles, *args)

    assert result.returncode == status
    assert message in result.stderr


def test_an_unreachable_server_is_reported() -> None:
    result = promote({}, "abcdef12", "--from", "http://127.0.0.1:9", "--to", "http://127.0.0.1:8")

    assert result.returncode == 1
    assert "cannot reach http://127.0.0.1:9" in result.stderr


def test_missing_versions_compares_content_not_dates() -> None:
    version = {"version": 1, "title": "T", "html": "<p>x</p>", "type": "otro", "author": "a@example.com", "note": None}
    source = {"versions": [{**version, "created_at": "2025-01-01T00:00:00+00:00"}, {**version, "version": 2}]}
    target = {"versions": [{**version, "created_at": "2026-01-01T00:00:00+00:00"}]}

    missing, present = missing_versions(source, target)

    assert ([v["version"] for v in missing], present) == ([2], [1])
    with pytest.raises(PromoteError, match="v1"):
        missing_versions(source, {"versions": [{**version, "note": "other"}]})


class ExportOnly(http.server.BaseHTTPRequestHandler):
    """Answers `GET /{id}/export` with a fixed one-version document, nothing else."""

    document: dict = {}

    def do_GET(self) -> None:
        body = json.dumps(self.document).encode("utf-8")
        self.send_response(200 if self.path == f"/{self.document['id']}/export" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def tls_source(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """An HTTPS source whose certificate only `ca.pem` vouches for."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert, key = tmp_path / "ca.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(key), "-out", str(cert),
        ],
        check=True,
        capture_output=True,
    )
    version = {"version": 1, "title": "Doc", "html": "<p>tls</p>", "type": "otro", "author": "you@example.com"}
    ExportOnly.document = {
        "format_version": 1,
        "id": "abcd1234",
        "server": "https://127.0.0.1",
        "versions": [{**version, "note": None, "bytes": 10, "created_at": "2025-01-01T00:00:00+00:00"}],
    }
    httpd = http.server.HTTPServer(("127.0.0.1", 0), ExportOnly)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{httpd.server_address[1]}", cert
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_private_ca_from_the_profile_verifies_tls(tmp_path: Path, target: Client, tls_source: tuple[str, Path]) -> None:
    url, ca = tls_source
    config = tmp_path / "servers.json"
    servers = {"secure": {"url": url, "ca": str(ca)}, "insecure": {"url": url}, "team": {"url": f"http://127.0.0.1:{target.port}"}}
    config.write_text(json.dumps({"servers": servers}))
    env = {"PERGAM_CONFIG": str(config)}

    refused = promote(env, "abcd1234", "--from", "insecure", "--to", "team")
    accepted = promote(env, "abcd1234", "--from", "secure", "--to", "team")

    assert refused.returncode == 1
    assert "CERTIFICATE_VERIFY_FAILED" in refused.stderr
    assert accepted.returncode == 0, accepted.stderr
    assert target.get("/abcd1234/raw").text == "<p>tls</p>"


def test_long_histories_are_uploaded_in_batches(client: Client, target: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = five_versions(client)
    monkeypatch.setattr(pergam_promote, "BATCH_SIZE", 2)
    source, team = Server("local", f"http://127.0.0.1:{client.port}"), Server("team", f"http://127.0.0.1:{target.port}")

    result = pergam_promote.promote(pid, source, team)

    assert result.uploaded == [1, 2, 3, 4, 5]
    assert [v["version"] for v in target.get(f"/{pid}/versions").json()] == [5, 4, 3, 2, 1]
