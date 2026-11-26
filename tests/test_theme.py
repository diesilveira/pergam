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


@pytest.mark.parametrize("path", ["/", "/{id}/view", "/{id}/v1/view", "/{id}/diff/v1..v2"])
def test_every_page_carries_the_shared_assets_and_one_top_bar(client: Client, path: str) -> None:
    page = client.get(path.format(id=publish_two(client))).text

    assert '<link rel="stylesheet" href="/static/pergam.css">' in page
    assert '<script src="/static/theme.js"></script>' in page
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


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def tokens() -> dict[str, tuple[str, str]]:
    pairs = re.findall(r"--([a-z-]+): light-dark\((#[0-9a-f]{6}), (#[0-9a-f]{6})\);", STYLESHEET)
    return {name: (light, dark) for name, light, dark in pairs}


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
def test_text_tokens_meet_aa_on_every_surface(theme: int) -> None:
    colour = {name: pair[theme] for name, pair in tokens().items()}

    for foreground in ("text", "soft", "muted", "accent", "ok", "danger"):
        for background in ("bg", "surface", "raised"):
            assert contrast(colour[foreground], colour[background]) >= 4.5, (foreground, background)
    assert contrast(colour["warn-text"], colour["warn-bg"]) >= 4.5
    assert contrast(colour["on-accent"], colour["accent"]) >= 4.5
