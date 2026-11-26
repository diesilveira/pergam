from __future__ import annotations

import psycopg
import pytest

from pergam.notes import MAX_NOTE_LENGTH, NoteError, parse_note
from support import Client

DOC = {"title": "Doc", "html": "<p>x</p>", "type": "otro", "author": "you@example.com"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("   ", None), ("  adds the\n costs  section ", "adds the costs section")],
)
def test_parse_note(value: object, expected: str | None) -> None:
    assert parse_note(value) == expected


@pytest.mark.parametrize("value", [3, ["a"], {"a": 1}, True, "x" * (MAX_NOTE_LENGTH + 1), "a\x00b", "a\x07b", "\ud800"])
def test_parse_note_rejects_bad_values(value: object) -> None:
    with pytest.raises(NoteError):
        parse_note(value)


def test_posting_without_note_is_unchanged(client: Client, db: psycopg.Connection) -> None:
    body = client.publish()

    assert "note" not in body
    assert client.get(f"/{body['id']}/versions").json()[0]["note"] is None
    assert db.execute("SELECT count(*) FROM pergam_note").fetchone() == (0,)


def test_note_is_returned_by_post_versions_and_api(client: Client) -> None:
    first = client.publish(note="first draft")
    second = client.publish(id=first["id"], note="adds the costs section")
    pid = first["id"]

    assert (first["note"], second["note"]) == ("first draft", "adds the costs section")
    assert [v["note"] for v in client.get(f"/{pid}/versions").json()] == ["adds the costs section", "first draft"]
    api = client.get(f"/api/pergams/{pid}").json()
    assert api["note"] == "adds the costs section"
    assert [v["note"] for v in api["versions"]] == ["adds the costs section", "first draft"]


def test_versions_without_a_note_read_as_null_next_to_ones_with_it(client: Client) -> None:
    pid = client.publish()["id"]
    client.publish(id=pid, note="second")
    client.publish(id=pid, note=" ")

    assert [v["note"] for v in client.get(f"/{pid}/versions").json()] == [None, "second", None]


@pytest.mark.parametrize("note", [42, ["x"], "x" * (MAX_NOTE_LENGTH + 1), "nul\x00here"])
def test_invalid_note_is_rejected_and_publishes_nothing(client: Client, db: psycopg.Connection, note: object) -> None:
    resp = client.post_json({**DOC, "note": note})

    assert resp.status == 400
    assert db.execute("SELECT count(*) FROM pergams").fetchone() == (0,)


def test_note_rolls_back_with_its_version_when_tags_fail(client: Client, db: psycopg.Connection) -> None:
    resp = client.post_json({**DOC, "note": "n", "tags": [f"t{i}" for i in range(30)]})

    assert resp.status == 400
    assert db.execute("SELECT count(*) FROM pergam_note").fetchone() == (0,)


def test_version_selector_shows_each_note(client: Client) -> None:
    pid = client.publish(note="first <draft>")["id"]
    client.publish(id=pid)

    page = client.get(f"/{pid}/view").text

    assert '<span class="versions__note">first &lt;draft&gt;</span>' in page
    assert page.count('class="versions__note"') == 1
