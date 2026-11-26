from __future__ import annotations

import pytest

from support import Client

DOC = """<!doctype html><html><head><title>t</title><style>body{}</style></head><body>
<h1>Rollout</h1><p>Two <em>regions</em> left.</p>
<h2>Steps</h2><ol><li>Drain</li><li>Deploy</li></ol>
<table><tr><th>Region</th><th>State</th></tr><tr><td>east</td><td>done</td></tr></table>
<script>ignored()</script></body></html>"""

DOC_TEXT = """# Rollout

Two regions left.

## Steps

1. Drain
2. Deploy

| Region | State |
| --- | --- |
| east | done |
"""


def test_text_serves_the_latest_version_as_markdown(client: Client) -> None:
    first = client.publish(html="<p>old</p>")
    client.publish(id=first["id"], html=DOC)

    resp = client.get(f"/{first['id']}/text")

    assert resp.status == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.text == DOC_TEXT


def test_pinned_version_text_is_cached_briefly(client: Client) -> None:
    first = client.publish(html="<h2>first</h2>")
    client.publish(id=first["id"], html=DOC)

    resp = client.get(f"/{first['id']}/v1/text")

    assert resp.status == 200
    assert resp.headers["cache-control"] == "public, max-age=300"
    assert resp.text == "## first\n"


@pytest.mark.parametrize("suffix", ["text", "v2/text"])
def test_text_of_unknown_pergam_or_version_is_404(client: Client, suffix: str) -> None:
    pid = client.publish()["id"]

    assert client.get(f"/abcdef12/{suffix}").status == 404
    assert client.get(f"/{pid}/v2/text").status == 404


def test_text_of_html_without_visible_text_is_empty(client: Client) -> None:
    pid = client.publish(html="<script>only()</script>")["id"]

    resp = client.get(f"/{pid}/text")

    assert resp.status == 200
    assert resp.body == b""
