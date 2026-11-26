"""`GET /feed.atom`: an Atom 1.0 (RFC 4287) feed of recent publications.

Every published version is one entry, linked to its immutable `/{id}/v{n}/view`
URL, which also serves as the entry's permanent id. The feed's id is its own
URL with the filters, so each filtered feed is a distinct feed to a reader.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

from pergam.public_url import PublicAddress
from pergam.store import FeedEntry

ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_TYPE = "application/atom+xml; charset=utf-8"

_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def _xml_text(value: str) -> str:
    """Drop the characters XML 1.0 cannot carry at all, even escaped."""
    return _NOT_XML.sub("", value)


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _add(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = _xml_text(text)
    return element


def feed_url(public: PublicAddress, tag: str | None, author: str | None) -> str:
    query = urlencode([(key, value) for key, value in (("tag", tag), ("author", author)) if value])
    return public.url("feed.atom") + (f"?{query}" if query else "")


def _entry(feed: ET.Element, entry: FeedEntry, public: PublicAddress) -> None:
    url = public.url(f"{entry.id}/v{entry.version}/view")
    element = _add(feed, "entry")
    _add(element, "id", url)
    _add(element, "title", entry.title if entry.version == 1 else f"{entry.title} (v{entry.version})")
    _add(element, "updated", _timestamp(entry.created_at))
    _add(element, "published", _timestamp(entry.created_at))
    _add(_add(element, "author"), "name", entry.author)
    _add(element, "link", rel="alternate", type="text/html", href=url)
    _add(element, "link", rel="alternate", type="text/markdown", href=public.url(f"{entry.id}/v{entry.version}/text"))
    _add(element, "category", term=entry.type, label=f"type: {entry.type}")
    for tag in entry.tags:
        _add(element, "category", term=tag)
    summary = " ".join((entry.summary or "").split()) or f"{entry.type} by {entry.author}"
    _add(element, "summary", summary, type="text")


def render_feed(entries: list[FeedEntry], public: PublicAddress, tag: str | None, author: str | None) -> bytes:
    self_url = feed_url(public, tag, author)
    feed = ET.Element("feed", {"xmlns": ATOM_NS})
    _add(feed, "id", self_url)
    scope = " · ".join(f"{key} {value}" for key, value in (("tag", tag), ("author", author)) if value)
    _add(feed, "title", f"pergam · {public.display}" + (f" · {scope}" if scope else ""))
    _add(feed, "subtitle", "Recent publications")
    newest = entries[0].created_at if entries else datetime.now(timezone.utc)
    _add(feed, "updated", _timestamp(newest))
    _add(feed, "link", rel="self", type="application/atom+xml", href=self_url)
    _add(feed, "link", rel="alternate", type="text/html", href=public.url(""))
    _add(feed, "generator", "pergam", uri="https://github.com/diesilveira/pergam")
    for entry in entries:
        _entry(feed, entry, public)
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)
