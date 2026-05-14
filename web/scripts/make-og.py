#!/usr/bin/env python3
"""
Generate the Open Graph card for pergam.dev.

Usage:
    python3 web/scripts/make-og.py

Writes web/public/og.png at 1200x630 (the size LinkedIn/Twitter/etc. expect).
Re-run any time the brand copy or palette changes.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Canvas & palette ───────────────────────────────────────────────
W, H = 1200, 630
BG = (13, 17, 23)             # --bg
BG_GLOW = (28, 36, 56)        # accent-tinted dark for soft top-left glow
INK = (230, 237, 243)         # --ink
INK_SOFT = (139, 148, 158)    # --ink-soft
INK_FAINT = (90, 100, 115)    # one notch dimmer than ink_soft
ACCENT = (122, 162, 255)      # blue
ACCENT_2 = (94, 231, 255)     # cyan

# ── Font discovery ─────────────────────────────────────────────────
# Helvetica Neue ships with macOS; index 1 in the .ttc is usually
# the Bold face which we want for the title.
HELVETICA = "/System/Library/Fonts/HelveticaNeue.ttc"

def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(HELVETICA, size, index=(1 if bold else 0))

# ── Compose ────────────────────────────────────────────────────────
img = Image.new("RGB", (W, H), BG)

# Soft radial glow in the top-left (cheap fake — circle with reduced
# opacity, blended in via paste with an alpha mask).
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gdraw = ImageDraw.Draw(glow)
for r, a in [(700, 18), (520, 26), (360, 34), (220, 44)]:
    gdraw.ellipse(
        [-r // 2, -r // 2, r // 2 + 200, r // 2 + 200],
        fill=(94, 231, 255, a),
    )
img.paste(glow, (0, 0), glow)

draw = ImageDraw.Draw(img)

# Brand mark (two bars) + wordmark — top-left.
mx, my = 80, 84
bar_w, bar_h, bar_gap = 16, 56, 6
draw.rounded_rectangle(
    [mx, my, mx + bar_w, my + bar_h], radius=3, fill=ACCENT_2
)
draw.rounded_rectangle(
    [mx + bar_w + bar_gap, my, mx + 2 * bar_w + bar_gap, my + bar_h],
    radius=3, fill=ACCENT,
)
draw.text(
    (mx + 2 * bar_w + bar_gap + 18, my + 6),
    "pergam",
    font=font(40, bold=True),
    fill=INK,
)

# Headline (two lines, big & bold) — left-aligned, vertically centered.
title_top = 250
draw.text((80, title_top), "Immutable parchment",
          font=font(78, bold=True), fill=INK)
draw.text((80, title_top + 92), "for what your AI builds.",
          font=font(78, bold=True), fill=INK_SOFT)

# Footer strip — small, faded.
draw.text(
    (80, H - 80),
    "pergam.dev  ·  self-hostable  ·  MIT  ·  no telemetry",
    font=font(22),
    fill=INK_FAINT,
)

# Subtle 1px accent rule above the footer.
draw.rectangle([80, H - 100, 220, H - 99], fill=ACCENT_2)

# ── Save ───────────────────────────────────────────────────────────
out = Path(__file__).resolve().parents[1] / "public" / "og.png"
img.save(out, "PNG", optimize=True)
print(f"wrote {out}  ({out.stat().st_size // 1024} KB)")
