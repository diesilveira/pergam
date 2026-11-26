"""The shared stylesheet and theme script: served, adopted by every page, and the one place for colours."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from support import Client

STATIC = Path(__file__).resolve().parent.parent / "src" / "pergam" / "static"
TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "pergam" / "templates"
STYLESHEET = (STATIC / "pergam.css").read_text(encoding="utf-8")
PAGE_STYLESHEETS = sorted(TEMPLATES.glob("*.css"))
COLOUR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\(|(?<![\w-])(?:white|black)(?![\w-])")
FONT_NAME = re.compile(r"font-family\s*:(?!\s*var\()", re.IGNORECASE)


def publish_two(client: Client) -> str:
    pid = client.publish(title="Plan", html="<h1>one</h1>")["id"]
    client.publish(id=pid, title="Plan", html="<h1>two</h1>")
    return pid


def test_stylesheet_and_theme_script_are_static_files(client: Client) -> None:
    css, script = client.get("/static/pergam.css"), client.get("/static/theme.js")

    assert (css.status, css.headers["content-type"]) == (200, "text/css; charset=utf-8")
    assert (script.status, script.headers["content-type"]) == (200, "text/javascript; charset=utf-8")
    for resp in (css, script):
        assert resp.headers["cache-control"] == "public, max-age=300"
        assert resp.headers["x-content-type-options"] == "nosniff"
    assert css.body == (STATIC / "pergam.css").read_bytes()


def asset_urls(page: str) -> dict[str, str]:
    return dict(re.findall(r'(?:href|src)="(/static/(?:pergam\.css|theme\.js))(\?v=[0-9a-f]{12})"', page))


def test_asset_urls_carry_a_content_hash_that_is_cached_for_good(client: Client) -> None:
    urls = asset_urls(client.get("/").text)

    assert set(urls) == {"/static/pergam.css", "/static/theme.js"}
    for path, version in urls.items():
        pinned, plain = client.get(path + version), client.get(path)
        assert pinned.status == 200 and pinned.body == plain.body
        assert pinned.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert plain.headers["cache-control"] == "public, max-age=300"


def test_a_hash_that_is_not_the_current_content_is_not_cached_for_good(client: Client) -> None:
    resp = client.get("/static/pergam.css?v=000000000000")

    assert resp.status == 200
    assert resp.headers["cache-control"] == "public, max-age=300"


def test_the_hash_follows_the_content(client: Client) -> None:
    import hashlib

    version = asset_urls(client.get("/").text)["/static/pergam.css"]

    assert version == "?v=" + hashlib.sha256((STATIC / "pergam.css").read_bytes()).hexdigest()[:12]


@pytest.mark.parametrize("path", ["/", "/{id}/view", "/{id}/v1/view", "/{id}/diff/v1..v2"])
def test_every_page_carries_the_shared_assets_and_one_top_bar(client: Client, path: str) -> None:
    page = client.get(path.format(id=publish_two(client))).text

    assert re.search(r'<link rel="stylesheet" href="/static/pergam\.css\?v=[0-9a-f]{12}">', page)
    assert re.search(r'<script src="/static/theme\.js\?v=[0-9a-f]{12}"></script>', page)
    assert page.index("/static/theme.js") < page.index("<body")
    assert page.count('<header class="topbar">') == 1
    assert page.count("data-theme-toggle") == 1
    assert 'name="color-scheme" content="light dark"' in page


def test_theme_toggle_is_hidden_until_the_script_runs(client: Client) -> None:
    page = client.get("/").text

    assert re.search(r"<button[^>]*data-theme-toggle[^>]*\bhidden\b", page)
    assert re.search(r"<button[^>]*data-theme-toggle[^>]*aria-label=", page)


@pytest.mark.parametrize("sheet", PAGE_STYLESHEETS, ids=lambda path: path.name)
def test_page_stylesheets_hold_layout_only(sheet: Path) -> None:
    css = sheet.read_text(encoding="utf-8")

    assert not COLOUR_LITERAL.findall(css)
    assert not FONT_NAME.findall(css)


def test_pages_set_no_colours_in_style_attributes(client: Client) -> None:
    pid = publish_two(client)
    pages = [client.get(path).text for path in ("/", f"/{pid}/view", f"/{pid}/diff/v1..v2")]

    styles = [style for page in pages for style in re.findall(r' style="([^"]*)"', page)]

    assert styles
    assert all(re.fullmatch(r"--(?:h|type-rgb):[\d,]+", style) for style in styles)


def test_stylesheet_loads_nothing_from_elsewhere_and_names_only_system_fonts() -> None:
    assert not re.search(r"url\(|@import|https?://", STYLESHEET)
    for name in ("Inter", "JetBrains"):
        assert name not in STYLESHEET


Rgba = tuple[float, float, float, float]
COLOUR = r"(#[0-9a-f]{6}|rgba\([\d., ]+\))"


def parse_colour(text: str) -> Rgba:
    if text.startswith("#"):
        return (*(int(text[i : i + 2], 16) for i in (1, 3, 5)), 1.0)
    red, green, blue, alpha = (float(part) for part in text[5:-1].split(","))
    return red, green, blue, alpha


def over(top: Rgba, below: Rgba) -> Rgba:
    alpha = top[3]
    return (*(top[i] * alpha + below[i] * (1 - alpha) for i in range(3)), 1.0)


def luminance(colour: Rgba) -> float:
    linear = [c / 255 / 12.92 if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in colour[:3]]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: Rgba, b: Rgba) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def tokens(theme: int) -> dict[str, Rgba]:
    pairs = re.findall(rf"--([a-z-]+): light-dark\({COLOUR}, {COLOUR}\);", STYLESHEET)
    return {name: parse_colour(pair[theme]) for name, *pair in pairs}


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
def test_text_tokens_meet_aa_on_every_surface(theme: int) -> None:
    colour = tokens(theme)

    for foreground in ("text", "soft", "muted", "accent", "ok", "danger"):
        for background in ("bg", "surface", "raised"):
            assert contrast(colour[foreground], colour[background]) >= 4.5, (foreground, background)
    assert contrast(colour["warn-text"], colour["warn-bg"]) >= 4.5
    assert contrast(colour["on-accent"], colour["accent"]) >= 4.5


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
def test_diff_and_highlight_tokens_keep_their_text_legible(theme: int) -> None:
    colour = tokens(theme)
    surface = colour["surface"]
    added, removed = over(colour["add-bg"], surface), over(colour["del-bg"], surface)
    pairs = {
        "text on added": (colour["text"], added),
        "text on removed": (colour["text"], removed),
        "text on added mark": (colour["text"], over(colour["add-mark"], added)),
        "text on removed mark": (colour["text"], over(colour["del-mark"], removed)),
        "text on highlight": (colour["text"], over(colour["mark-bg"], surface)),
        "ok on added": (colour["ok"], added),
        "danger on removed": (colour["danger"], removed),
        "muted on added": (colour["muted"], added),
        "muted on removed": (colour["muted"], removed),
    }

    for name, (foreground, background) in pairs.items():
        assert contrast(foreground, background) >= 4.5, name
