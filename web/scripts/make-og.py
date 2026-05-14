#!/usr/bin/env python3
"""
Generate the Open Graph card for pergam.dev.

The card mirrors the terminal mockup shown in the landing hero — same
palette, same tokens, same response-card layout — so the social
preview is consistent with what visitors see when they click through.

Usage:
    python3 web/scripts/make-og.py

Writes web/public/og.png at 1200x630 (LinkedIn/Twitter/Slack/Discord
all expect this aspect ratio). Re-run when brand copy or palette change.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Canvas & palette ───────────────────────────────────────────────
W, H = 1200, 630

# Mirrors the CSS variables in web/public/styles.css.
BG          = (7, 8, 13)        # --bg
BG_ELEV     = (13, 16, 25)      # --bg-elev
BG_CARD     = (17, 20, 31)      # --bg-card
LINE        = (30, 35, 51)      # --line
LINE_SOFT   = (22, 26, 38)      # --line-soft
INK         = (238, 241, 248)   # --ink
INK_SOFT    = (154, 163, 184)   # --ink-soft
INK_FAINT   = (92, 99, 121)     # --ink-faint
ACCENT      = (122, 162, 255)   # --accent     (blue)
ACCENT_2    = (94, 231, 255)    # --accent-2   (cyan)
WARM        = (240, 210, 138)   # --warm       (parchment)
STR_GREEN   = (184, 232, 144)   # token string

# Token colors (match `.mock-card__body .tok-*` in styles.css).
TOK_CMD  = ACCENT_2
TOK_FLAG = WARM
TOK_STR  = STR_GREEN
TOK_COM  = INK_FAINT

# Mac terminal lights.
LIGHT_RED    = (255, 95, 86)
LIGHT_YELLOW = (255, 189, 46)
LIGHT_GREEN  = (40, 200, 64)

# ── Font discovery ─────────────────────────────────────────────────
HELVETICA = "/System/Library/Fonts/HelveticaNeue.ttc"
MENLO     = "/System/Library/Fonts/Menlo.ttc"

def sans(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Helvetica Neue .ttc: index 0 ≈ Regular, 1 ≈ Bold.
    return ImageFont.truetype(HELVETICA, size, index=(1 if bold else 0))

def mono(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Menlo .ttc: 0 ≈ Regular, 1 ≈ Bold (varies by macOS version,
    # but the glyph fallback keeps it readable either way).
    return ImageFont.truetype(MENLO, size, index=(1 if bold else 0))

# ── Helpers ────────────────────────────────────────────────────────
def rounded(draw, box, r, *, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

def text(draw, xy, s, font, color):
    draw.text(xy, s, font=font, fill=color)

def text_w(draw, s, font) -> int:
    bbox = draw.textbbox((0, 0), s, font=font)
    return bbox[2] - bbox[0]

# ── Compose ────────────────────────────────────────────────────────
img = Image.new("RGB", (W, H), BG)

# Soft cyan glow in the top-left, replicating the radial gradient
# the landing hero uses. Stack a few translucent ellipses with
# decreasing size for a cheap-but-smooth falloff.
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gdraw = ImageDraw.Draw(glow)
for r, a in [(900, 14), (680, 18), (460, 24), (280, 32)]:
    gdraw.ellipse([-r // 3, -r // 3, r, r], fill=(94, 231, 255, a))
img.paste(glow, (0, 0), glow)

draw = ImageDraw.Draw(img)

# ── Wordmark (top-left) ────────────────────────────────────────────
mx, my = 60, 56
bar_w, bar_h, bar_gap = 14, 44, 5
draw.rounded_rectangle([mx, my, mx + bar_w, my + bar_h], radius=2.5, fill=ACCENT_2)
draw.rounded_rectangle(
    [mx + bar_w + bar_gap, my, mx + 2 * bar_w + bar_gap, my + bar_h],
    radius=2.5, fill=ACCENT,
)
text(draw, (mx + 2 * bar_w + bar_gap + 14, my + 2),
     "pergam", sans(34, bold=True), INK)

# Small qualifiers on the right side of the top strip.
qualifier = "immutable parchment for what your AI builds"
qw = text_w(draw, qualifier, sans(20))
text(draw, (W - 60 - qw, my + 12), qualifier, sans(20), INK_SOFT)

# ── Terminal card ──────────────────────────────────────────────────
TX, TY = 90, 140                # top-left of the terminal
TW, TH = 1020, 430              # terminal size
BAR_H  = 44                     # height of the title bar

# Shadow under the terminal.
shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
sdraw = ImageDraw.Draw(shadow)
for off, alpha in [(18, 35), (12, 50), (6, 70)]:
    sdraw.rounded_rectangle(
        [TX, TY + off, TX + TW, TY + TH + off],
        radius=16, fill=(0, 0, 0, alpha),
    )
img.paste(shadow, (0, 0), shadow)
draw = ImageDraw.Draw(img)

# Terminal frame.
rounded(draw, [TX, TY, TX + TW, TY + TH], 16, fill=BG_ELEV, outline=LINE, width=1)
# Bar.
rounded(draw, [TX, TY, TX + TW, TY + BAR_H], 16, fill=BG_CARD)
# Hack: clip the bar bottom corners by drawing over the lower half.
draw.rectangle([TX, TY + BAR_H - 16, TX + TW, TY + BAR_H], fill=BG_CARD)
# Separator under the bar.
draw.rectangle([TX, TY + BAR_H, TX + TW, TY + BAR_H + 1], fill=LINE)

# 3 lights on the bar.
lights_x = TX + 22
lights_y = TY + BAR_H // 2
for i, col in enumerate([LIGHT_RED, LIGHT_YELLOW, LIGHT_GREEN]):
    cx = lights_x + i * 22
    draw.ellipse([cx - 7, lights_y - 7, cx + 7, lights_y + 7], fill=col)
# Bar title centered.
bar_title = "pergam · publish"
bt_w = text_w(draw, bar_title, sans(18))
text(draw, (TX + (TW - bt_w) // 2, TY + BAR_H // 2 - 12),
     bar_title, sans(18), INK_SOFT)

# ── Terminal body — CLI invocation ─────────────────────────────────
BODY_X = TX + 36
y = TY + BAR_H + 28
LINE_GAP = 38

# Comment.
text(draw, (BODY_X, y), "# inside Claude Code", mono(24, bold=False), TOK_COM)
y += LINE_GAP

# claude --skill post-pergam \
fnt = mono(24)
fnt_b = mono(24, bold=True)
x = BODY_X
text(draw, (x, y), "claude", fnt_b, TOK_CMD); x += text_w(draw, "claude ", fnt_b)
text(draw, (x, y), "--skill", fnt, TOK_FLAG); x += text_w(draw, "--skill ", fnt)
text(draw, (x, y), "post-pergam", fnt, TOK_STR); x += text_w(draw, "post-pergam ", fnt)
text(draw, (x, y), "\\", fnt, INK_FAINT)
y += LINE_GAP

# --title "Q3 status dashboard" \
x = BODY_X + 28
text(draw, (x, y), "--title", fnt, TOK_FLAG); x += text_w(draw, "--title ", fnt)
text(draw, (x, y), '"Q3 status dashboard"', fnt, TOK_STR)
x += text_w(draw, '"Q3 status dashboard" ', fnt)
text(draw, (x, y), "\\", fnt, INK_FAINT)
y += LINE_GAP

# --type reporte
x = BODY_X + 28
text(draw, (x, y), "--type", fnt, TOK_FLAG); x += text_w(draw, "--type ", fnt)
text(draw, (x, y), "reporte", fnt, TOK_STR)
y += LINE_GAP

# ── Response card ──────────────────────────────────────────────────
RC_X = TX + 36
RC_Y = y + 6
RC_W = TW - 72
RC_H = 116
rounded(draw, [RC_X, RC_Y, RC_X + RC_W, RC_Y + RC_H],
        12, fill=BG_CARD, outline=LINE, width=1)

# Title row + checkmark.
title_y = RC_Y + 16
text(draw, (RC_X + 18, title_y), "Q3 status dashboard",
     sans(22, bold=True), INK)
# Check on the right.
cx = RC_X + RC_W - 30
draw.line([(cx - 8, title_y + 14), (cx - 2, title_y + 20), (cx + 8, title_y + 8)],
          fill=(86, 211, 100), width=3)

# Pills row.
pill_y = title_y + 36
pill_x = RC_X + 18

def pill(x_start, label, *, kind="default"):
    pad_x, pad_y = 9, 4
    fnt_p = sans(15, bold=True)
    tw = text_w(draw, label, fnt_p)
    w = tw + 2 * pad_x
    h = 22
    bg = {
        "type": (240, 210, 138, 38),
        "ver":  (122, 162, 255, 38),
        "default": (255, 255, 255, 18),
    }[kind]
    fg = {"type": WARM, "ver": ACCENT, "default": INK_SOFT}[kind]
    # Translucent fill via RGBA layer.
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle([0, 0, w, h], radius=11, fill=bg)
    img.paste(overlay, (x_start, pill_y), overlay)
    # Redraw foreground after paste (paste clobbers our draw obj).
    d2 = ImageDraw.Draw(img)
    d2.text((x_start + pad_x, pill_y + 2), label, font=fnt_p, fill=fg)
    return x_start + w + 8

x_end = pill(pill_x, "reporte", kind="type")
x_end = pill(x_end, "v3", kind="ver")
x_end = pill(x_end, "you@example.com", kind="default")

# Re-grab the draw obj (Image.paste invalidates the old one).
draw = ImageDraw.Draw(img)

# URL bar (full-width inside card).
ub_y = pill_y + 36
ub_h = 30
rounded(draw, [RC_X + 14, ub_y, RC_X + RC_W - 14, ub_y + ub_h],
        8, fill=(0, 0, 0))
# Green check + URL text.
chx = RC_X + 26
chy = ub_y + ub_h // 2
draw.line([(chx, chy + 2), (chx + 5, chy + 7), (chx + 13, chy - 3)],
          fill=(86, 211, 100), width=2)
text(draw, (RC_X + 50, ub_y + 5),
     "pergam.dev/a1b2c3d4/view", mono(18), INK)
# 'copied' on the right (the check is already drawn on the left).
copied = "copied"
cw = text_w(draw, copied, sans(13))
text(draw, (RC_X + RC_W - 14 - cw - 10, ub_y + 8),
     copied, sans(13), INK_FAINT)

# ── Save ───────────────────────────────────────────────────────────
out = Path(__file__).resolve().parents[1] / "public" / "og.png"
img.save(out, "PNG", optimize=True)
print(f"wrote {out}  ({out.stat().st_size // 1024} KB)")
