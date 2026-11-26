from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from pergam.meta import Meta, Origin
from pergam.transfer import FORMAT_VERSION, TransferError, parse_import, parse_server_url
from pergam.store import VersionInfo
from pergam.view_page import render_view
from support import TARGET_PUBLIC_URL, Client, patch_meta

TYPES = ("plan", "otro")
STATE = """
    SELECT
      (SELECT count(*) || md5(COALESCE(string_agg(p::text, '|' ORDER BY id, version), '')) FROM pergams p),
      (SELECT count(*) || md5(COALESCE(string_agg(m::text, '|' ORDER BY id), '')) FROM pergam_meta m),
      (SELECT count(*) || md5(COALESCE(string_agg(n::text, '|' ORDER BY id, version), '')) FROM pergam_note n),
      (SELECT count(*) FROM pergam_text)
"""


def history(client: Client) -> str:
    """A three-version pergam, tagged, pinned and with notes on v1 and v3."""
    pid = client.publish(title="Plan", html="<h1>plan</h1><p>first draft</p>", type="plan", tags=["Q3"], note="draft")[
        "id"
    ]
    client.publish(id=pid, title="Plan", html="<h1>plan</h1><p>second</p>", type="plan")
    client.publish(id=pid, title="Plan v3", html="<h1>plan</h1><p>costs added</p>", type="plan", note="adds costs")
    patch_meta(client, pid, {"pinned": True})
    return pid


def export(client: Client, pid: str) -> dict[str, Any]:
    resp = client.get(f"/{pid}/export")
    assert resp.status == 200, resp.body
    return resp.json()


def document(pid: str = "abcd1234", versions: int = 2, **fields: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "id": pid,
        "server": "http://localhost:1111",
        "meta": {"tags": ["q3"], "archived": False, "pinned": False},
        "versions": [
            {
                "version": n,
                "title": f"Doc v{n}",
                "html": f"<p>version {n}</p>",
                "type": "otro",
                "author": "you@example.com",
                "note": None,
                "bytes": 16,
                "created_at": f"2025-01-0{n}T10:00:00.123456+00:00",
            }
            for n in range(1, versions + 1)
        ],
    }
    doc.update(fields)
    return doc


def test_export_returns_every_version_with_html_and_meta(client: Client) -> None:
    pid = history(client)

    doc = export(client, pid)

    assert doc["format_version"] == FORMAT_VERSION
    assert (doc["id"], doc["server"]) == (pid, f"http://localhost:{client.port}")
    assert doc["meta"] == {"tags": ["q3"], "archived": False, "pinned": True}
    assert [v["version"] for v in doc["versions"]] == [1, 2, 3]
    assert [v["note"] for v in doc["versions"]] == ["draft", None, "adds costs"]
    assert doc["versions"][2]["html"] == "<h1>plan</h1><p>costs added</p>"
    assert doc["versions"][2]["bytes"] == len(b"<h1>plan</h1><p>costs added</p>")
    versions = client.get(f"/{pid}/versions").json()[::-1]
    assert [v["created_at"] for v in doc["versions"]] == [v["created_at"] for v in versions]
    assert set(doc["versions"][0]) == {"version", "title", "html", "type", "author", "note", "bytes", "created_at"}


def test_export_of_an_unknown_id_is_404(client: Client) -> None:
    assert client.get("/abcdef12/export").status == 404


def test_export_then_import_reproduces_the_pergam(client: Client, target: Client) -> None:
    pid = history(client)
    doc = export(client, pid)

    resp = target.post_json(doc, "/import")

    assert resp.status == 201
    assert resp.json() == {
        "id": pid,
        "imported": [1, 2, 3],
        "skipped": [],
        "origin": {"url": f"http://localhost:{client.port}", "id": pid},
        "view_url": f"{TARGET_PUBLIC_URL}/{pid}/view",
    }
    source_api, target_api = client.get(f"/api/pergams/{pid}").json(), target.get(f"/api/pergams/{pid}").json()
    fields = ("version", "title", "type", "author", "bytes", "created_at", "note")
    assert [{k: v[k] for k in fields} for v in target_api["versions"]] == [
        {k: v[k] for k in fields} for v in source_api["versions"]
    ]
    assert (target_api["tags"], target_api["pinned"], target_api["archived"]) == (["q3"], True, False)
    assert target_api["origin"] == {"url": f"http://localhost:{client.port}", "id": pid}
    assert source_api["origin"] is None
    for n in (1, 2, 3):
        assert target.get(f"/{pid}/v{n}/raw").body == client.get(f"/{pid}/v{n}/raw").body
    assert export(target, pid)["versions"] == doc["versions"]


def test_imported_versions_are_searchable_and_continue_numbering(client: Client, target: Client) -> None:
    pid = history(client)
    target.post_json(export(client, pid), "/import")

    found = target.get("/api/pergams?q=costs").json()["pergams"]

    assert [row["id"] for row in found] == [pid]
    assert target.publish(id=pid)["version"] == 4


def test_view_page_names_the_origin(client: Client, target: Client) -> None:
    pid = history(client)
    target.post_json(export(client, pid), "/import")

    page = target.get(f"/{pid}/view").text

    assert f'<a href="http://localhost:{client.port}/{pid}/view" rel="noreferrer"' in page
    assert f">localhost:{client.port}/{pid}</a>" in page
    assert 'class="bar__origin"' not in client.get(f"/{pid}/view").text


def test_reimporting_is_a_no_op(target: Client, target_db: psycopg.Connection) -> None:
    doc = document(versions=3)
    assert target.post_json(doc, "/import").status == 201
    state = target_db.execute(STATE).fetchone()

    resp = target.post_json(doc, "/import")

    assert resp.status == 200
    assert (resp.json()["imported"], resp.json()["skipped"]) == ([], [1, 2, 3])
    assert target_db.execute(STATE).fetchone() == state


def test_importing_over_a_diverging_id_is_409_and_writes_nothing(target: Client, target_db: psycopg.Connection) -> None:
    target.post_json(document(versions=2), "/import")
    state = target_db.execute(STATE).fetchone()
    diverging = document(versions=3)
    diverging["versions"][1]["html"] = "<p>someone else's v2</p>"

    resp = target.post_json(diverging, "/import")

    assert resp.status == 409
    assert b"v2" in resp.body
    assert target_db.execute(STATE).fetchone() == state


def test_importing_over_an_id_published_here_is_409(target: Client, target_db: psycopg.Connection) -> None:
    target.publish(id="abcd1234", title="Unrelated")
    state = target_db.execute(STATE).fetchone()

    assert target.post_json(document(), "/import").status == 409
    assert target_db.execute(STATE).fetchone() == state


def test_missing_versions_are_added_to_an_existing_id(target: Client, target_db: psycopg.Connection) -> None:
    full = document(versions=4)
    target.post_json({**full, "versions": full["versions"][:2]}, "/import")
    patch_meta(target, "abcd1234", {"tags": ["local"], "archived": True})

    resp = target.post_json({**full, "versions": full["versions"][2:], "meta": {"tags": ["ops"], "pinned": True}}, "/import")

    assert resp.status == 201
    assert (resp.json()["imported"], resp.json()["skipped"]) == ([3, 4], [])
    assert [v["version"] for v in target.get("/abcd1234/versions").json()] == [4, 3, 2, 1]
    assert target.get("/abcd1234/meta").json() == {"id": "abcd1234", "tags": ["local", "ops"], "archived": True, "pinned": False}


def test_origin_of_an_existing_id_is_kept(target: Client) -> None:
    target.publish(id="abcd1234", title="Doc v1", html="<p>version 1</p>")
    doc = document(versions=2)
    doc["versions"][0]["created_at"] = "2020-01-01T00:00:00+00:00"

    resp = target.post_json(doc, "/import")

    assert (resp.status, resp.json()["imported"], resp.json()["skipped"]) == (201, [2], [1])
    assert resp.json()["origin"] is None
    assert target.get("/api/pergams/abcd1234").json()["origin"] is None


@pytest.mark.parametrize("versions", [[2], [1, 3]], ids=["no-v1", "gap"])
def test_import_that_would_leave_a_version_missing_is_409(
    target: Client, target_db: psycopg.Connection, versions: list[int]
) -> None:
    full = document(versions=3)
    doc = {**full, "versions": [v for v in full["versions"] if v["version"] in versions]}

    resp = target.post_json(doc, "/import")

    assert resp.status == 409
    assert target_db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_import_without_server_records_no_origin(target: Client) -> None:
    doc = document()
    del doc["server"]

    assert target.post_json(doc, "/import").json()["origin"] is None
    assert target.get("/api/pergams/abcd1234").json()["origin"] is None


def _broken(change: Any) -> dict[str, Any]:
    doc = document()
    change(doc)
    return doc


BROKEN = {
    "format": lambda d: d.update(format_version=2),
    "id": lambda d: d.update(id="../etc"),
    "no-versions": lambda d: d.update(versions=[]),
    "versions-not-list": lambda d: d.update(versions={"1": {}}),
    "duplicate": lambda d: d["versions"].append(copy.deepcopy(d["versions"][0])),
    "version-zero": lambda d: d["versions"][0].update(version=0),
    "version-bool": lambda d: d["versions"][0].update(version=True),
    "no-html": lambda d: d["versions"][0].pop("html"),
    "blank-author": lambda d: d["versions"][0].update(author="  "),
    "nul-title": lambda d: d["versions"][0].update(title="a\x00b"),
    "type": lambda d: d["versions"][1].update(type="memo"),
    "note": lambda d: d["versions"][1].update(note="x" * 281),
    "naive-date": lambda d: d["versions"][0].update(created_at="2025-01-01T10:00:00"),
    "bad-date": lambda d: d["versions"][0].update(created_at="yesterday"),
    "server-scheme": lambda d: d.update(server="javascript:alert(1)"),
    "server-credentials": lambda d: d.update(server="https://user:pw@example.com"),
    "server-type": lambda d: d.update(server=42),
    "meta-tags": lambda d: d["meta"].update(tags="q3"),
    "too-many-tags": lambda d: d["meta"].update(tags=[f"t{i}" for i in range(21)]),
    "meta-flag": lambda d: d["meta"].update(archived="yes"),
}


@pytest.mark.parametrize("change", BROKEN.values(), ids=BROKEN.keys())
def test_malformed_import_is_400_and_writes_nothing(target: Client, target_db: psycopg.Connection, change: Any) -> None:
    resp = target.post_json(_broken(change), "/import")

    assert resp.status == 400
    assert target_db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_tags_merged_past_the_limit_are_400_and_write_nothing(target: Client, target_db: psycopg.Connection) -> None:
    target.publish(id="abcd1234", title="Doc v1", html="<p>version 1</p>", tags=[f"a{i}" for i in range(15)])
    state = target_db.execute(STATE).fetchone()
    doc = document(meta={"tags": [f"b{i}" for i in range(10)]})

    assert target.post_json(doc, "/import").status == 400
    assert target_db.execute(STATE).fetchone() == state


def test_import_needs_json(target: Client) -> None:
    assert target.request("POST", "/import", b"{}", {"Content-Type": "text/plain"}).status == 415


def test_parse_import_normalises_and_recomputes_bytes() -> None:
    doc = document(server="https://docs.example.com/", meta=None)
    doc["versions"][0].update(type=" PLAN ", author=" you@example.com ", note="  two   words ", bytes=1)

    parsed = parse_import(doc, TYPES)

    first = parsed.versions[0]
    assert (first.type, first.author, first.note, first.bytes) == ("plan", "you@example.com", "two words", 16)
    assert first.created_at == datetime.fromisoformat("2025-01-01T10:00:00.123456+00:00")
    assert parsed.origin == Origin("https://docs.example.com", "abcd1234")
    assert (parsed.tags, parsed.archived, parsed.pinned) == ([], False, False)


@pytest.mark.parametrize(
    "raw",
    ["ftp://example.com", "https://", "https://example.com/?x=1", "https://example.com/#a", "https://exa mple.com", "x" * 3000],
)
def test_parse_server_url_rejects(raw: str) -> None:
    with pytest.raises(TransferError):
        parse_server_url(raw)


def test_render_view_escapes_the_origin() -> None:
    pid = "abcd1234"
    info = [VersionInfo(1, "Doc", "otro", "you@example.com", 12, datetime.fromisoformat("2025-01-01T00:00:00+00:00"))]

    page = render_view(pid, info, Meta([], False, False, Origin('https://example.com/"x', pid)), 1)

    assert 'href="https://example.com/&quot;x/' in page


@pytest.mark.parametrize(
    "created_at",
    ["9999-12-31T23:30:00-05:00", "1969-12-31T23:59:59+00:00", "2999-01-01T00:00:00+00:00"],
    ids=["past-year-9999-in-utc", "before-1970", "future"],
)
def test_out_of_range_created_at_is_400(target: Client, target_db: psycopg.Connection, created_at: str) -> None:
    doc = document()
    doc["versions"][0]["created_at"] = created_at

    resp = target.post_json(doc, "/import")

    assert resp.status == 400
    assert target_db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_created_at_is_stored_in_utc_and_may_lead_by_the_clock_skew(target: Client) -> None:
    soon = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    doc = document()
    doc["versions"][0]["created_at"] = "2025-01-01T05:00:00-05:00"
    doc["versions"][1]["created_at"] = soon

    assert target.post_json(doc, "/import").status == 201
    exported = export(target, "abcd1234")["versions"]
    assert exported[0]["created_at"] == "2025-01-01T10:00:00+00:00"
    assert datetime.fromisoformat(exported[1]["created_at"]) == datetime.fromisoformat(soon)


def test_parse_import_window_is_relative_to_now() -> None:
    doc = document(versions=1)
    doc["versions"][0]["created_at"] = "2025-01-01T00:06:00+00:00"
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(TransferError, match="between 1970-01-01 and now"):
        parse_import(doc, TYPES, now=now)
    assert parse_import(doc, TYPES, now=now + timedelta(minutes=1)).versions[0].created_at.tzinfo == timezone.utc
