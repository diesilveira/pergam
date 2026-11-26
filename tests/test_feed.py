from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from support import Client, patch_meta

A = "{http://www.w3.org/2005/Atom}"
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def fetch(client: Client, query: str = "") -> ET.Element:
    resp = client.get(f"/feed.atom{query}")
    assert resp.status == 200
    assert resp.headers["content-type"] == "application/atom+xml; charset=utf-8"
    assert resp.body.startswith(b"<?xml version='1.0' encoding='utf-8'?>")
    feed = ET.fromstring(resp.body)
    assert_valid_atom(feed)
    return feed


def one(element: ET.Element, name: str) -> ET.Element:
    found = element.findall(A + name)
    assert len(found) == 1, (name, len(found))
    return found[0]


def assert_valid_atom(feed: ET.Element) -> None:
    """The RFC 4287 rules this feed relies on (section 4.1.1 and 4.1.2)."""
    assert feed.tag == A + "feed"
    for name in ("id", "title", "updated"):
        assert one(feed, name).text
    assert RFC3339.match(one(feed, "updated").text or "")
    self_links = [link for link in feed.findall(A + "link") if link.get("rel") == "self"]
    assert len(self_links) == 1 and self_links[0].get("href") == one(feed, "id").text
    entry_ids = []
    for entry in feed.findall(A + "entry"):
        entry_ids.append(one(entry, "id").text)
        assert one(entry, "title").text
        assert RFC3339.match(one(entry, "updated").text or "")
        assert RFC3339.match(one(entry, "published").text or "")
        assert one(one(entry, "author"), "name").text
        alternates = [(link.get("type"), link.get("hreflang")) for link in entry.findall(A + "link") if link.get("rel") == "alternate"]
        assert ("text/html", None) in alternates
        assert len(alternates) == len(set(alternates))
        for category in entry.findall(A + "category"):
            assert category.get("term")
    assert len(entry_ids) == len(set(entry_ids))


def entry_links(feed: ET.Element) -> list[str]:
    return [one(entry, "id").text or "" for entry in feed.findall(A + "entry")]


def test_every_publication_is_an_entry_newest_first(client: Client) -> None:
    first = client.publish(title="Plan", html="<h1>Plan</h1><p>first   draft</p>")
    other = client.publish(title="Notes")
    client.publish(id=first["id"], title="Plan")
    base = f"http://localhost:{client.port}"

    feed = fetch(client)

    assert entry_links(feed) == [
        f"{base}/{first['id']}/v2/view",
        f"{base}/{other['id']}/v1/view",
        f"{base}/{first['id']}/v1/view",
    ]
    newest, _, oldest = feed.findall(A + "entry")
    assert one(newest, "title").text == "Plan (v2)"
    assert one(oldest, "title").text == "Plan"
    assert one(oldest, "summary").text == "Plan first draft"
    assert one(feed, "updated").text == one(newest, "updated").text
    assert len({one(entry, "updated").text for entry in feed.findall(A + "entry")}) == 3
    assert one(feed, "id").text == f"{base}/feed.atom"


def test_entry_carries_author_type_tags_and_text_link(client: Client) -> None:
    pid = client.publish(type="plan", author="ana@example.com", tags=["ops", "q3"], html="<script>x()</script>")["id"]

    (entry,) = fetch(client).findall(A + "entry")

    assert one(one(entry, "author"), "name").text == "ana@example.com"
    assert one(entry, "summary").text == "plan by ana@example.com"
    assert [c.get("term") for c in entry.findall(A + "category")] == ["plan", "ops", "q3"]
    links = {link.get("type"): link.get("href") for link in entry.findall(A + "link")}
    assert links["text/markdown"] == f"http://localhost:{client.port}/{pid}/v1/text"


@pytest.fixture
def authored(client: Client) -> dict[str, str]:
    ids = {
        "ana_ops": client.publish(author="ana@example.com", tags=["ops"])["id"],
        "bo_ops": client.publish(author="bo@example.com", tags=["Ops"])["id"],
        "bo_plain": client.publish(author="bo@example.com")["id"],
        "archived": client.publish(author="bo@example.com", tags=["ops"])["id"],
    }
    patch_meta(client, ids["archived"], {"archived": True})
    return ids


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", {"ana_ops", "bo_ops", "bo_plain"}),
        ("?tag=ops", {"ana_ops", "bo_ops"}),
        ("?tag=OPS", {"ana_ops", "bo_ops"}),
        ("?author=bo@example.com", {"bo_ops", "bo_plain"}),
        ("?tag=ops&author=bo@example.com", {"bo_ops"}),
        ("?tag=nope", set()),
    ],
)
def test_tag_and_author_filter_the_feed(
    client: Client, authored: dict[str, str], query: str, expected: set[str]
) -> None:
    listed = {link.split("/")[3] for link in entry_links(fetch(client, query))}

    assert listed == {authored[key] for key in expected}


def test_filtered_feed_identifies_itself_with_its_filters(client: Client) -> None:
    feed = fetch(client, "?author=bo@example.com&tag=Q3")

    assert one(feed, "id").text == f"http://localhost:{client.port}/feed.atom?tag=q3&author=bo%40example.com"
    assert "tag q3" in (one(feed, "title").text or "")


def test_feed_escapes_markup_and_drops_characters_xml_cannot_carry(client: Client) -> None:
    client.publish(title="<b>R&D</b> \x07bell", html="<p>a &lt; b &amp;&amp; c</p>")

    (entry,) = fetch(client).findall(A + "entry")

    assert one(entry, "title").text == "<b>R&D</b> bell"
    assert one(entry, "summary").text == "a < b && c"


def test_index_advertises_the_feed(client: Client) -> None:
    assert '<link rel="alternate" type="application/atom+xml"' in client.get("/").text


def test_empty_feed_is_still_valid(client: Client) -> None:
    assert fetch(client).findall(A + "entry") == []
