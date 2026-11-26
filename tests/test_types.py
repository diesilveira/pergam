from __future__ import annotations

import re

import psycopg
import pytest

from pergam.taxonomy import DEFAULT_TYPES, parse_types, type_rgb
from support import Client, DatabaseFactory

DEFAULT_ERROR = "type must be one of ['informe', 'investigacion', 'otro', 'plan', 'reporte']"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_TYPES),
        ("", DEFAULT_TYPES),
        (" , ,", DEFAULT_TYPES),
        ("plan,runbook", ("plan", "runbook")),
        (" Plan , RUNBOOK,plan,, adr ", ("plan", "runbook", "adr")),
    ],
)
def test_parse_types(raw: str | None, expected: tuple[str, ...]) -> None:
    assert parse_types(raw) == expected


def test_default_types_keep_their_colours() -> None:
    assert {name: type_rgb(name) for name in DEFAULT_TYPES} == {
        "plan": (240, 210, 138),
        "investigacion": (182, 146, 255),
        "informe": (86, 211, 100),
        "reporte": (94, 231, 255),
        "otro": (139, 150, 167),
    }


def test_other_types_get_a_stable_colour_from_their_name() -> None:
    colours = {name: type_rgb(name) for name in ("runbook", "adr", "retro", "spec")}

    assert colours == {name: type_rgb(name) for name in colours}
    assert len(set(colours.values())) == len(colours)
    assert all(0 <= channel <= 255 for colour in colours.values() for channel in colour)


def test_unset_types_keep_the_default_rule(client: Client) -> None:
    resp = client.post_json({"title": "x", "html": "<p>x</p>", "type": "runbook", "author": "you@example.com"})

    assert resp.status == 400
    assert DEFAULT_ERROR in resp.text
    assert all(client.publish(type=name)["type"] == name for name in DEFAULT_TYPES)


@pytest.fixture
def custom(databases: DatabaseFactory, start_server) -> tuple[Client, str]:
    url = databases.create()
    return start_server(url, PERGAM_TYPES="plan, Runbook"), url


def test_custom_type_is_accepted_filterable_and_coloured(custom: tuple[Client, str]) -> None:
    client, _ = custom
    pid = client.publish(type="RUNBOOK")["id"]
    other = client.publish(type="plan")["id"]

    page = client.get("/?type=runbook").text

    assert f'data-href="/{pid}/view"' in page
    assert f'data-href="/{other}/view"' not in page
    rgb = ",".join(map(str, type_rgb("runbook")))
    assert re.search(rf'class="type" style="--type-rgb:{rgb}"[^>]*>runbook</a>', page)


def test_custom_list_rejects_types_it_does_not_name(custom: tuple[Client, str]) -> None:
    client, _ = custom

    resp = client.post_json({"title": "x", "html": "<p>x</p>", "type": "informe", "author": "you@example.com"})

    assert resp.status == 400
    assert "type must be one of ['plan', 'runbook']" in resp.text


def test_stored_types_no_longer_listed_still_render_and_filter(custom: tuple[Client, str]) -> None:
    client, url = custom
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO pergams (id, version, title, html, type, author, bytes)"
            " VALUES ('0dd00001', 1, 'Old report', '<p>old</p>', 'informe', 'you@example.com', 10)"
        )

    page = client.get("/?type=informe").text

    assert client.get("/0dd00001/v1/content").text == "<p>old</p>"
    assert 'data-href="/0dd00001/view"' in page
    assert re.search(r'class="type" style="--type-rgb:86,211,100"[^>]*>informe</a>', page)
    assert re.search(r'>informe <span class="cnt">1</span>', page)
