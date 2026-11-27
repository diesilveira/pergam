"""Inline SVG icons for every page's top bar and the view and diff pages: drawn in `currentColor`, hidden from assistive technology.

The control that carries an icon names itself with text or `aria-label`; the icon never does.
"""

from __future__ import annotations


def _svg(body: str, name: str = "") -> str:
    css = f"icon icon--{name}" if name else "icon"
    return (
        f'<svg class="{css}" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false" '
        'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    )


MORE = _svg('<circle cx="3" cy="8" r=".9" fill="currentColor"/><circle cx="8" cy="8" r=".9" fill="currentColor"/>'
            '<circle cx="13" cy="8" r=".9" fill="currentColor"/>')
CLOSE = _svg('<path d="M4 4l8 8M12 4l-8 8"/>')
PIN = _svg('<path class="icon__fill" d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z"/>')
SECTIONS = _svg('<path d="M2.5 4h11M2.5 8h11M2.5 12h7"/>')
SEARCH = _svg('<circle cx="7" cy="7" r="4.25"/><path d="M10.2 10.2l3.3 3.3"/>')
THEME_SYSTEM = _svg('<circle cx="8" cy="8" r="5.5"/><path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor"/>', "theme-system")
THEME_LIGHT = _svg(
    '<circle cx="8" cy="8" r="2.75"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.06 1.06M11.54 11.54l1.06 1.06'
    'M3.4 12.6l1.06-1.06M11.54 4.46l1.06-1.06"/>',
    "theme-light",
)
THEME_DARK = _svg('<path d="M13.2 9.6A5.5 5.5 0 0 1 6.4 2.8a5.5 5.5 0 1 0 6.8 6.8z"/>', "theme-dark")
