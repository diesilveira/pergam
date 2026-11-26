from __future__ import annotations

import pytest

from support import Client


def listed_ids(html: str, *ids: str) -> set[str]:
    return {pid for pid in ids if f'data-href="/{pid}/view"' in html}


@pytest.fixture
def catalogue(client: Client) -> dict[str, str]:
    plan = client.publish(title="Deploy plan", type="plan", author="ana@example.com")["id"]
    report = client.publish(title="Weekly report", type="reporte", author="bo@example.com")["id"]
    notes = client.publish(title="Release notes", type="plan", author="bo@example.com")["id"]
    client.publish(id=notes, title="Release notes v2", type="plan", author="bo@example.com")
    return {"plan": plan, "report": report, "notes": notes}


def test_index_lists_latest_version_of_every_pergam(client: Client, catalogue: dict[str, str]) -> None:
    resp = client.get("/")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert listed_ids(resp.text, *catalogue.values()) == set(catalogue.values())
    assert "Release notes v2" in resp.text
    assert f'href="/{catalogue["notes"]}/v1/view"' in resp.text
    assert "3 pergams · 2 authors · 4 versions" in resp.text


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?type=plan", {"plan", "notes"}),
        ("?type=PLAN", {"plan", "notes"}),
        ("?author=bo@example.com", {"report", "notes"}),
        ("?q=REPORT", {"report"}),
        ("?q=notes%20v2", {"notes"}),
        ("?type=plan&author=bo@example.com", {"notes"}),
        ("?type=informe", set()),
    ],
)
def test_index_filters(client: Client, catalogue: dict[str, str], query: str, expected: set[str]) -> None:
    resp = client.get(f"/{query}")

    assert resp.status == 200
    assert listed_ids(resp.text, *catalogue.values()) == {catalogue[k] for k in expected}


def test_index_filtered_view_reports_partial_count(client: Client, catalogue: dict[str, str]) -> None:
    resp = client.get("/?type=reporte")

    assert "showing 1 of 3" in resp.text
    assert 'href="/">clear all</a>' in resp.text


def test_empty_index_shows_hint(client: Client) -> None:
    resp = client.get("/")

    assert resp.status == 200
    assert "No pergams match." in resp.text


def test_index_escapes_user_content(client: Client) -> None:
    client.publish(title="<script>alert(1)</script>", author="x<y>@example.com")

    resp = client.get("/")

    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text
