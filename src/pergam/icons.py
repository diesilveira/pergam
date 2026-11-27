"""Inline SVG icons for the view and diff pages: drawn in `currentColor`, hidden from assistive technology.

The control that carries an icon names itself with text or `aria-label`; the icon never does.
"""

from __future__ import annotations


def _svg(body: str) -> str:
    return (
        '<svg class="icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false" '
        'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    )


MORE = _svg('<circle cx="3" cy="8" r=".9" fill="currentColor"/><circle cx="8" cy="8" r=".9" fill="currentColor"/>'
            '<circle cx="13" cy="8" r=".9" fill="currentColor"/>')
CLOSE = _svg('<path d="M4 4l8 8M12 4l-8 8"/>')
PIN = _svg('<path class="icon__fill" d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z"/>')
SECTIONS = _svg('<path d="M2.5 4h11M2.5 8h11M2.5 12h7"/>')
