from __future__ import annotations

from support import Client


def comparable(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k != "date"}


def test_head_mirrors_get_on_every_route(client: Client) -> None:
    first = client.publish(html="<p>one</p>")
    client.publish(id=first["id"], html="<p>two</p>")
    pid = first["id"]
    paths = [
        "/",
        "/?type=otro&q=doc",
        "/healthz",
        f"/{pid}/view",
        f"/{pid}/raw",
        f"/{pid}/v1/view",
        f"/{pid}/v1/raw",
        f"/{pid}/text",
        f"/{pid}/v1/text",
        f"/{pid}/v9/text",
        "/api/pergams",
        "/api/pergams?q=doc",
        f"/api/pergams/{pid}",
        "/api/pergams/abcdef12",
        f"/{pid}/versions",
        f"/{pid}/meta",
        "/abcdef12/meta",
        f"/{pid}/v9/view",
        "/abcdef12/versions",
        "/nope",
    ]

    for path in paths:
        get, head = client.get(path), client.request("HEAD", path)

        assert head.status == get.status, path
        assert comparable(head.headers) == comparable(get.headers), path
        assert head.body == b"", path
