"""Who gets the pin, archive and tag controls on the view page, and what their PATCH must look like."""

from __future__ import annotations

import re

import pytest

from support import Client, patch_meta

JSON = {"Content-Type": "application/json"}


def controls(page: str) -> dict[str, str | None]:
    """The rendered controls: the PATCH target, each flag's pressed state and the tag field's value."""
    url = re.search(r'<span class="meta-controls" data-meta-url="([^"]+)" hidden>', page)
    flags = dict(re.findall(r'data-meta-flag="(\w+)" aria-pressed="(true|false)"', page))
    tags = re.search(r'<input class="input" id="tag-input" name="tags"[^>]* value="([^"]*)"', page)
    return {
        "url": url.group(1) if url else None,
        "pinned": flags.get("pinned"),
        "archived": flags.get("archived"),
        "tags": tags.group(1) if tags else None,
    }


def test_without_trusted_identity_everyone_gets_the_controls_with_the_current_state(client: Client) -> None:
    pid = client.publish(tags=["q3", "ops"])["id"]
    patch_meta(client, pid, {"archived": True})

    page = client.get(f"/{pid}/view").text

    assert controls(page) == {"url": f"/{pid}/meta", "pinned": "false", "archived": "true", "tags": "ops, q3"}
    assert '<li data-meta-item hidden><button type="button" class="dropdown__item" data-tags-edit>Edit tags</button></li>' in page
    assert '<li data-meta-item hidden><button type="button" class="dropdown__item" data-meta-flag="archived" aria-pressed="true">Archive</button></li>' in page
    assert '<p class="meta-status" data-meta-status role="status"></p>' in page


def test_tag_field_escapes_the_tags(client: Client) -> None:
    pid = client.publish(tags=['"><script>x'])["id"]

    page = client.get(f"/{pid}/view").text

    assert controls(page)["tags"] == "&quot;&gt;&lt;script&gt;x"
    assert "<script>x" not in page


@pytest.mark.parametrize(
    ("viewer", "offered"),
    [("ana@example.com", True), ("bo@example.com", True), ("eve@example.com", False), (None, True)],
    ids=["owner", "collaborator", "stranger", "unknown"],
)
def test_with_trusted_identity_only_editors_and_unknown_users_get_the_controls(
    trusting: Client, viewer: str | None, offered: bool
) -> None:
    owner = trusting.as_user("ana@example.com")
    pid = owner.publish(tags=["q3"])["id"]
    assert owner.request("PUT", f"/{pid}/collaborators", b'{"collaborators":["bo@example.com"]}', JSON).status == 200

    page = (trusting.as_user(viewer) if viewer else trusting).get(f"/{pid}/view").text

    assert (controls(page)["url"] is not None) is offered
    assert ("data-tags-edit" in page) is offered
    assert ("data-meta-status" in page) is offered
    assert '<a class="tag" href="/?tag=q3">q3</a>' in page


def test_a_malformed_proxy_user_still_sees_the_page(trusting: Client) -> None:
    pid = trusting.as_user("ana@example.com").publish()["id"]

    resp = trusting.request("GET", f"/{pid}/view", headers={"X-Forwarded-User": "a\x7fb"})

    assert resp.status == 200
    assert controls(resp.text)["url"] == f"/{pid}/meta"


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", None]
)
def test_meta_accepts_only_a_json_body_so_a_form_cannot_change_it(client: Client, content_type: str | None) -> None:
    pid = client.publish()["id"]
    headers = {"Content-Type": content_type} if content_type else {}

    for method in ("PATCH", "POST"):
        assert client.request(method, f"/{pid}/meta", b'{"pinned":true}', headers).status in (404, 415)

    assert client.get(f"/{pid}/meta").json()["pinned"] is False


def test_meta_answers_no_cross_origin_preflight(client: Client) -> None:
    pid = client.publish()["id"]

    resp = client.request(
        "OPTIONS", f"/{pid}/meta", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "PATCH"}
    )

    assert resp.status >= 400
    assert not any(name.startswith("access-control-") for name in resp.headers)


def test_an_archived_pergam_offers_pin_as_unavailable_and_says_why(client: Client) -> None:
    archived, live = client.publish()["id"], client.publish()["id"]
    patch_meta(client, archived, {"archived": True})

    blocked, open_ = client.get(f"/{archived}/view").text, client.get(f"/{live}/view").text

    assert '<button type="button" class="btn btn--pin" data-meta-flag="pinned" aria-pressed="false" aria-disabled="true" aria-describedby="pin-note">' in blocked
    assert '<span class="sr-only" id="pin-note">Archived pergams cannot be pinned: unarchive it first.</span>' in blocked
    assert 'data-meta-flag="pinned" aria-pressed="false" aria-disabled="false">' in open_
    assert re.search(r'data-meta-flag="pinned"[^>]*><svg class="icon"[^>]*aria-hidden="true".*?</svg><span>Pin</span></button>', open_)
