"""Pin, archive and tag editing from the view page, in a real browser, and what a published script cannot do.

Needs Playwright and a Chrome or Chromium it can launch. Without them the tests are
skipped, except when `CI` is set: there they fail, so a broken setup cannot pass unnoticed.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from support import Client, in_ci, listed_order, unavailable

try:
    from playwright import sync_api
except ImportError:
    if in_ci():
        raise
    pytest.skip("Playwright is not installed", allow_module_level=True)

JSON = {"Content-Type": "application/json"}
META = "SELECT tags, archived, pinned FROM pergam_meta WHERE id = %s"

INTRUDER = """<!doctype html><title>Intruder</title><body><script>
window.attempts = {};
const note = (name) => [() => { attempts[name] = 'reached'; }, () => { attempts[name] = 'blocked'; }];
const change = JSON.stringify({pinned: true, archived: true, tags: ['owned']});
const targets = {other: '{target}', own: location.pathname.split('/')[1]};
for (const [name, id] of Object.entries(targets)) {
  fetch(`/${id}/meta`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: change}).then(...note(`${name}-patch`));
  fetch(`/${id}/meta`, {method: 'PATCH', mode: 'no-cors', body: change}).then(...note(`${name}-blind-patch`));
}
for (const type of ['pin', 'archive', 'meta', 'tags', 'click', 'hash', 'title']) {
  parent.postMessage({pergam: 1, type, value: change}, '*');
}
parent.postMessage({pinned: true, archived: true}, '*');
if (parent === window) {
  attempts.dom = 'no-parent';
} else try {
  parent.document.querySelectorAll('[data-meta-flag]').forEach((button) => button.click());
  attempts.dom = 'reached';
} catch (error) {
  attempts.dom = 'blocked';
}
const form = document.createElement('form');
form.method = 'POST'; form.action = `/${targets.other}/meta`; form.enctype = 'text/plain';
form.innerHTML = '<input name=\\'{"pinned":true,"x":"\\' value=\\'"}\\'>';
document.body.append(form);
form.submit();
</script></body>"""


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        for options in ({"channel": "chrome"}, {}):
            try:
                launched = playwright.chromium.launch(**options)
                break
            except sync_api.Error:
                continue
        else:
            unavailable("no Chrome or Chromium that Playwright can launch")
        yield launched
        launched.close()


@pytest.fixture
def open_page(browser) -> Iterator:
    contexts = []

    def open_page(url: str, scheme: str = "light", **options):
        context = browser.new_context(color_scheme=scheme, **options)
        contexts.append(context)
        page = context.new_page()
        page.goto(url)
        return page

    yield open_page
    for context in contexts:
        context.close()


def base(client: Client) -> str:
    return f"http://127.0.0.1:{client.port}"


def status_text(page) -> str:
    page.wait_for_selector("[data-meta-status]:not([hidden])")
    return page.locator("[data-meta-status]").inner_text()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_pinning_from_the_page_puts_the_pergam_first_on_the_index(
    open_page, client: Client, db: psycopg.Connection, scheme: str
) -> None:
    pid = client.publish(title="Pin me")["id"]
    client.publish(title="Newer")
    page = open_page(f"{base(client)}/{pid}/view", scheme)
    pin = page.get_by_role("button", name="Pin", exact=True)

    pin.click()

    assert status_text(page) == "Pinned: it shows first on the index."
    assert pin.get_attribute("aria-pressed") == "true"
    assert db.execute(META, (pid,)).fetchone() == ([], False, True)
    assert listed_order(client.get("/").text)[0] == pid
    page.reload()
    assert page.get_by_role("button", name="Pin", exact=True).get_attribute("aria-pressed") == "true"
    page.get_by_role("button", name="Pin", exact=True).click()
    assert status_text(page) == "Unpinned."
    assert db.execute(META, (pid,)).fetchone() == ([], False, False)


def test_archiving_from_the_page_hides_the_pergam_from_the_index(open_page, client: Client) -> None:
    pid = client.publish()["id"]
    page = open_page(f"{base(client)}/{pid}/view")
    archive = page.get_by_role("button", name="Archive", exact=True)

    archive.click()

    page.wait_for_function("document.querySelector('[data-meta-flag=archived]').ariaPressed === 'true'")
    assert pid not in listed_order(client.get("/").text)
    assert pid in listed_order(client.get("/?archived=1").text)
    archive.click()
    page.wait_for_function("document.querySelector('[data-meta-flag=archived]').ariaPressed === 'false'")
    assert pid in listed_order(client.get("/").text)


def test_tags_are_edited_inline_with_the_keyboard(open_page, client: Client, db: psycopg.Connection) -> None:
    pid = client.publish(tags=["q3"])["id"]
    page = open_page(f"{base(client)}/{pid}/view")
    edit = page.locator("[data-tags-edit]")

    edit.focus()
    page.keyboard.press("Enter")
    field = page.get_by_role("textbox", name="Tags, separated by commas")
    assert page.evaluate("document.activeElement.id") == "tag-input"
    assert field.input_value() == "q3"
    field.fill("q3, Budget Review, <b>")
    page.keyboard.press("Enter")

    assert status_text(page) == "Tags saved."
    assert page.locator(".bar__tags .tag").all_inner_texts() == ["<b>", "budget-review", "q3"]
    assert page.locator(".bar__tags .tag").nth(1).get_attribute("href") == "/?tag=budget-review"
    assert page.locator(".bar__tags b").count() == 0
    assert db.execute(META, (pid,)).fetchone() == (["<b>", "budget-review", "q3"], False, False)
    assert page.evaluate("document.activeElement.dataset.tagsEdit") == ""

    edit.click()
    field.fill("something else")
    page.keyboard.press("Escape")
    assert field.is_hidden()
    edit.click()
    assert field.input_value() == "<b>, budget-review, q3"


def test_rejected_tags_leave_the_state_and_say_why(open_page, client: Client, db: psycopg.Connection) -> None:
    pid = client.publish(tags=["q3"])["id"]
    page = open_page(f"{base(client)}/{pid}/view")

    page.get_by_role("button", name="Edit tags").click()
    page.get_by_role("textbox", name="Tags, separated by commas").fill("x" * 41)
    page.get_by_role("button", name="Save").click()

    assert status_text(page).startswith("The server did not accept that change.")
    assert page.locator(".meta-status--error").is_visible()
    assert db.execute(META, (pid,)).fetchone() == (["q3"], False, False)


def test_a_refused_change_shows_why_and_leaves_the_state(
    open_page, trusting: Client, db: psycopg.Connection
) -> None:
    owner = trusting.as_user("ana@example.com")
    pid = owner.publish()["id"]
    assert owner.request("PUT", f"/{pid}/collaborators", b'{"collaborators":["bo@example.com"]}', JSON).status == 200
    page = open_page(f"{base(trusting)}/{pid}/view", extra_http_headers={"X-Forwarded-User": "bo@example.com"})
    assert owner.request("PUT", f"/{pid}/collaborators", b'{"collaborators":[]}', JSON).status == 200

    page.get_by_role("button", name="Pin", exact=True).click()

    assert status_text(page) == (
        "Only the owner of this pergam or one of its collaborators can change it. Nothing was changed."
    )
    assert page.get_by_role("button", name="Pin", exact=True).get_attribute("aria-pressed") == "false"
    assert db.execute("SELECT pinned FROM pergam_meta WHERE id = %s", (pid,)).fetchone() == (False,)


def test_an_unknown_user_is_told_to_sign_in(open_page, trusting: Client, db: psycopg.Connection) -> None:
    pid = trusting.as_user("ana@example.com").publish()["id"]
    page = open_page(f"{base(trusting)}/{pid}/view")

    page.get_by_role("button", name="Archive", exact=True).click()

    assert status_text(page).startswith("The server does not know who you are")
    assert db.execute(META, (pid,)).fetchone() is None


def test_a_stranger_gets_no_controls(open_page, trusting: Client) -> None:
    pid = trusting.as_user("ana@example.com").publish(tags=["q3"])["id"]

    page = open_page(f"{base(trusting)}/{pid}/view", extra_http_headers={"X-Forwarded-User": "eve@example.com"})

    assert page.get_by_role("button", name="Pin", exact=True).count() == 0
    assert page.get_by_role("button", name="Edit tags").count() == 0
    assert page.get_by_role("link", name="q3").is_visible()


def test_without_script_the_controls_are_absent(open_page, client: Client) -> None:
    pid = client.publish(tags=["q3"])["id"]

    page = open_page(f"{base(client)}/{pid}/view", java_script_enabled=False)

    for name in ("Pin", "Archive", "Edit tags", "Save"):
        assert page.get_by_role("button", name=name, exact=True).count() == 0
    assert page.get_by_role("link", name="q3").is_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_controls_fit_a_phone_and_their_messages_meet_aa(open_page, client: Client, scheme: str) -> None:
    pid = client.publish(tags=["q3"])["id"]
    page = open_page(f"{base(client)}/{pid}/view", scheme, viewport={"width": 360, "height": 740})

    page.get_by_role("button", name="Pin", exact=True).click()
    ok = status_text(page)
    page.route(f"**/{pid}/meta", lambda route: route.fulfill(status=500, body=""))
    page.get_by_role("button", name="Pin", exact=True).click()
    page.wait_for_selector(".meta-status--error")
    page.get_by_role("button", name="Edit tags").click()

    assert ok
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert page.get_by_role("textbox", name="Tags, separated by commas").bounding_box()["width"] > 100
    ratio = page.evaluate(
        """() => {
            const node = document.querySelector('.meta-status');
            const canvas = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
            const rgb = (css) => { canvas.fillStyle = css; canvas.fillRect(0, 0, 1, 1); return Array.from(canvas.getImageData(0, 0, 1, 1).data).slice(0, 3); };
            const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
            const [a, b] = [lum(rgb(getComputedStyle(node).color)), lum(rgb(getComputedStyle(node).backgroundColor))].sort((x, y) => y - x);
            return (a + 0.05) / (b + 0.05);
        }"""
    )
    assert ratio >= 4.5


@pytest.mark.parametrize("opened", ["framed", "alone"])
def test_a_published_script_cannot_pin_archive_or_tag(
    browser, client: Client, db: psycopg.Connection, opened: str
) -> None:
    target = client.publish(title="Target")["id"]
    pid = client.publish(title="Intruder", html=INTRUDER.replace("{target}", target))["id"]
    page = browser.new_page()
    try:
        if opened == "framed":
            page.goto(f"{base(client)}/{pid}/view")
            frame = page.frame(url=lambda url: "/content" in url)
        else:
            page.goto(f"{base(client)}/{pid}/v1/content")
            frame = page.main_frame
        frame.wait_for_function("Object.keys(window.attempts).length >= 5")
        page.wait_for_timeout(500)
        attempts = frame.evaluate("window.attempts")
        pressed = page.locator("[data-meta-flag]").evaluate_all("nodes => nodes.map(node => node.ariaPressed)")
        status_hidden = page.locator("[data-meta-status]").evaluate_all("nodes => nodes.every(node => node.hidden)")
    finally:
        page.close()

    assert attempts == {
        "other-patch": "blocked",
        "other-blind-patch": "blocked",
        "own-patch": "blocked",
        "own-blind-patch": "blocked",
        "dom": "blocked" if opened == "framed" else "no-parent",
    }
    assert db.execute("SELECT count(*) FROM pergam_meta").fetchone() == (0,)
    assert pressed == (["false", "false"] if opened == "framed" else [])
    assert status_hidden
