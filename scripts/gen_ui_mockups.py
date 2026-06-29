"""Render Discord-dark mockups of the reel-capture bot UI for the README.

These are *mockups* drawn from the real embed/button definitions in
app/cards.py and app/bot.py — not live screenshots (the dev container can't
reach Discord). Run:  python scripts/gen_ui_mockups.py
Output: docs/images/*.png
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def f(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


# Discord dark palette
BG = (49, 51, 56)          # message area
EMBED_BG = (43, 45, 49)    # embed body
WHITE = (242, 243, 245)
GREY = (181, 186, 193)
MUTE = (148, 155, 164)
BLURPLE = (88, 101, 242)
GREEN = (59, 165, 92)
RED = (218, 67, 64)
PILL = (78, 80, 88)


def rrect(d, xy, r, fill, outline=None, width=1):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def pill(d, x, y, text, fg, bg, font, pad=14, h=34):
    w = d.textlength(text, font=font)
    rrect(d, (x, y, x + w + 2 * pad, y + h), 8, bg)
    d.text((x + pad, y + h / 2), text, font=font, fill=fg, anchor="lm")
    return x + w + 2 * pad + 8


def avatar(d, x, y, color, letter, r=20):
    d.ellipse((x, y, x + 2 * r, y + 2 * r), fill=color)
    d.text((x + r, y + r), letter, font=f(18, True), fill=WHITE, anchor="mm")


def header(d, x, y, name, name_color, when="Today at 7:42 PM"):
    d.text((x, y), name, font=f(16, True), fill=name_color, anchor="lm")
    nw = d.textlength(name, font=f(16, True))
    rrect(d, (x + nw + 8, y - 8, x + nw + 8 + 38, y + 8), 4, BLURPLE)
    d.text((x + nw + 8 + 19, y), "APP", font=f(10, True), fill=WHITE, anchor="mm")
    d.text((x + nw + 8 + 46, y), when, font=f(12), fill=MUTE, anchor="lm")


def embed_card(img, x, y, w, accent, build):
    """Draw an embed shell (left accent bar) and let `build` fill the body.
    Returns bottom y."""
    d = ImageDraw.Draw(img)
    bottom = build(d, x + 16, y + 14)
    h = bottom - y + 14
    # accent bar + body (drawn under the text via a fresh pass)
    layer = Image.new("RGB", img.size, BG)
    dl = ImageDraw.Draw(layer)
    rrect(dl, (x, y, x + w, y + h), 8, EMBED_BG)
    dl.rectangle((x, y, x + 4, y + h), fill=accent)
    layer.paste(img.crop((x + 6, y, x + w, y + h)), (x + 6, y))
    # simpler: redraw body on top of shell
    base = Image.new("RGB", img.size, BG)
    db = ImageDraw.Draw(base)
    rrect(db, (x, y, x + w, y + h), 8, EMBED_BG)
    db.rectangle((x, y, x + 4, y + h), fill=accent)
    build(db, x + 16, y + 14)
    img.paste(base.crop((x, y, x + w, y + h)), (x, y))
    return y + h


def field(d, x, y, label, value):
    d.text((x, y), label, font=f(13, True), fill=WHITE, anchor="lm")
    d.text((x, y + 20), value, font=f(14), fill=GREY, anchor="lm")


# ── 1. Spot card (reel capture result) ──────────────────────────────────────
def spot_card():
    W, H = 760, 430
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    avatar(d, 24, 24, BLURPLE, "S")
    header(d, 76, 36, "SpotBot", (126, 168, 254))
    d.text((76, 64), "shared an Instagram reel  ·  @nick: \"this looks unreal\"",
           font=f(13), fill=MUTE, anchor="lm")

    def body(d, x, y):
        # category dot + title
        d.ellipse((x, y, x + 16, y + 16), fill=(224, 123, 57))
        d.text((x + 26, y + 8), "Casa Loma", font=f(20, True), fill=WHITE, anchor="lm")
        y += 38
        d.text((x, y), "Late-night birria tacos pop-up — handmade, all cash, til 2am.",
               font=f(14), fill=GREY, anchor="lm")
        y += 30
        d.text((x, y), "Watch the reel", font=f(14, True), fill=(126, 168, 254), anchor="lm")
        y += 34
        field(d, x, y, "Category", "food drink")
        field(d, x + 220, y, "When", "Fri Jun 26 · 8:00 PM")
        y += 56
        d.text((x, y), "shared by nick  ·  via Instagram", font=f(12), fill=MUTE, anchor="lm")
        return y + 16

    bottom = embed_card(img, 76, 84, 600, (224, 123, 57), body)

    # buttons row
    by = bottom + 14
    bx = 76
    bx = pill(d, bx, by, "Want to go · 3", WHITE, BLURPLE, f(14, True))
    bx = pill(d, bx, by, "Not for me", WHITE, PILL, f(14))
    bx = pill(d, bx, by, "Similar nearby", WHITE, PILL, f(14))
    bx = pill(d, bx, by, "Remove", WHITE, PILL, f(14))
    img.save(OUT / "discord_spot_card.png")


# ── 2. /plan card ────────────────────────────────────────────────────────────
def plan_card():
    W, H = 760, 320
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    avatar(d, 24, 24, GREEN, "S")
    header(d, 76, 36, "SpotBot", (126, 168, 254))
    d.text((76, 64), "/plan  ·  in thread “Friday plan”", font=f(13), fill=MUTE, anchor="lm")

    def body(d, x, y):
        d.text((x, y + 8), "Here's what I heard", font=f(20, True), fill=WHITE, anchor="lm")
        y += 40
        d.text((x, y), "Vote on the picks below — or keep chatting and /plan again to refine.",
               font=f(14), fill=GREY, anchor="lm")
        y += 34
        field(d, x, y, "vibe", "late-night tacos")
        field(d, x + 200, y, "area", "Long Beach")
        field(d, x + 400, y, "when", "tonight")
        return y + 50

    embed_card(img, 76, 84, 600, (110, 168, 254), body)
    d.text((76, 250), "picks posted as cards below — vote one in",
           font=f(13), fill=MUTE, anchor="lm")
    img.save(OUT / "discord_plan_card.png")


# ── 3. Welcome / onboarding ──────────────────────────────────────────────────
def welcome_card():
    W, H = 760, 230
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    avatar(d, 24, 24, BLURPLE, "S")
    header(d, 76, 36, "SpotBot", (126, 168, 254))

    def body(d, x, y):
        d.text((x, y + 8), "Drop a reel, get a spot", font=f(20, True), fill=WHITE, anchor="lm")
        y += 42
        d.text((x, y), "Paste any Instagram reel or post link here — no commands needed.",
               font=f(14), fill=GREY, anchor="lm")
        y += 24
        d.text((x, y), "I'll catalog the venue and post a card you can vote up.",
               font=f(14), fill=GREY, anchor="lm")
        return y + 18

    embed_card(img, 76, 72, 600, (110, 168, 254), body)
    img.save(OUT / "discord_welcome_card.png")


if __name__ == "__main__":
    spot_card()
    plan_card()
    welcome_card()
    print("wrote:", *(p.name for p in sorted(OUT.glob("*.png"))))
