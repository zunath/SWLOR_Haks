"""Writes the illustrated SVG source for every Pazaak side card inventory icon.

Each SVG is rasterized and exported as a 32x32 transparent inventory TGA,
sw_item/iit_card_<model>.tga, for base item 401 (card). The model number of
each card is listed in CARDS and matches the item blueprints.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Theme colors: (light, mid, dark)
BLUE = ("#9fd4ff", "#2f8cff", "#0b2f73")
RED = ("#ffb0a8", "#ff3b3b", "#6b0b0b")
GOLD = ("#fff1a8", "#f2b632", "#6b4307")
VIOLET = ("#e3c2ff", "#a65cff", "#3a0d73")
TEAL = ("#b5fff1", "#22d3b5", "#064f45")

# (model, file stem, kind, value, theme)
CARDS = [
    *[(98 + i, f"plus{v}", "plus", v, BLUE) for i, v in enumerate(range(1, 7))],
    *[(104 + i, f"minus{v}", "minus", v, RED) for i, v in enumerate(range(1, 7))],
    *[(110 + i, f"plusminus{v}", "plusminus", v, None) for i, v in enumerate(range(1, 7))],
    (116, "flip24", "flip", (2, 4), VIOLET),
    (117, "flip36", "flip", (3, 6), TEAL),
    (118, "double", "double", None, GOLD),
    (119, "tiebreaker", "tiebreaker", 1, GOLD),
]

BOTTOM_CHEVRON = ('<path d="M112 222 l16 -12 l16 12" fill="none" stroke="#ffffff" stroke-opacity="0.8" '
                  'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')

FONT = "font-family=\"Arial Black, Arial\" font-weight=\"900\""


def defs(theme_a, theme_b):
    a_light, a_mid, a_dark = theme_a
    b_light, b_mid, b_dark = theme_b
    return f"""
  <defs>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{a_light}"/>
      <stop offset="0.5" stop-color="{a_mid}"/>
      <stop offset="0.5" stop-color="{b_mid}"/>
      <stop offset="1" stop-color="{b_dark}"/>
    </linearGradient>
    <linearGradient id="face" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3a4658"/>
      <stop offset="0.5" stop-color="#1b2230"/>
      <stop offset="1" stop-color="#0c1018"/>
    </linearGradient>
    <linearGradient id="bandA" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{a_light}"/>
      <stop offset="1" stop-color="{a_dark}"/>
    </linearGradient>
    <linearGradient id="bandB" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="{b_light}"/>
      <stop offset="1" stop-color="{b_dark}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{a_mid}" stop-opacity="0.75"/>
      <stop offset="0.6" stop-color="{b_mid}" stop-opacity="0.25"/>
      <stop offset="1" stop-color="{b_mid}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="shine" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0"/>
      <stop offset="0.5" stop-color="#ffffff" stop-opacity="0.28"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="glyph" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#d6e2f0"/>
    </linearGradient>
  </defs>"""


def card_body(bottom_chevron=True):
    # Card: x 40..216, y 12..244. Rim, inset face, top and bottom bands, chevrons, shine.
    return """
  <rect x="46" y="18" width="176" height="232" rx="26" fill="#000000" opacity="0.45"/>
  <rect x="40" y="12" width="176" height="232" rx="26" fill="url(#rim)"/>
  <rect x="44" y="16" width="168" height="224" rx="22" fill="none" stroke="#ffffff" stroke-opacity="0.35" stroke-width="3"/>
  <rect x="54" y="26" width="148" height="204" rx="16" fill="url(#face)"/>
  <path d="M54 42 a16 16 0 0 1 16 -16 h116 a16 16 0 0 1 16 16 v16 h-148 z" fill="url(#bandA)"/>
  <path d="M54 198 h148 v16 a16 16 0 0 1 -16 16 h-116 a16 16 0 0 1 -16 -16 z" fill="url(#bandB)"/>
  <path d="M112 34 l16 12 l16 -12" fill="none" stroke="#ffffff" stroke-opacity="0.8" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  {bottom}
  <circle cx="68" cy="128" r="4" fill="#ffffff" opacity="0.25"/>
  <circle cx="188" cy="128" r="4" fill="#ffffff" opacity="0.25"/>
  <ellipse cx="128" cy="128" rx="70" ry="64" fill="url(#glow)"/>
  <path d="M60 30 h50 l-40 196 h-14 a10 10 0 0 1 -6 -6 z" fill="url(#shine)"/>""".replace("{bottom}", BOTTOM_CHEVRON if bottom_chevron else "")


def glyph(text, y, size, stroke="#05070c"):
    return (f'\n  <text x="128" y="{y}" text-anchor="middle" {FONT} font-size="{size}" '
            f'fill="url(#glyph)" stroke="{stroke}" stroke-width="7" paint-order="stroke" '
            f'letter-spacing="-4">{text}</text>')


def svg(kind, value, theme):
    if kind == "plusminus":
        theme_a, theme_b = BLUE, RED
    else:
        theme_a = theme_b = theme

    body = card_body(bottom_chevron=kind != "tiebreaker")
    if kind == "plus":
        art = glyph(f"+{value}", 166, 112)
    elif kind == "minus":
        art = glyph(f"−{value}", 166, 112)
    elif kind == "plusminus":
        art = glyph(f"±{value}", 166, 112)
    elif kind == "tiebreaker":
        art = glyph("±1", 158, 100)
        # A bold T badge in the bottom band marks the tie-winning card.
        art += """
  <circle cx="128" cy="214" r="24" fill="#2a1a02" stroke="#fff1a8" stroke-width="4"/>
  <path d="M114 202 h28 v7 h-10 v19 h-8 v-19 h-10 z" fill="#fff1a8"/>"""
    elif kind == "double":
        art = glyph("×2", 166, 112)
    else:
        low, high = value
        art = glyph(str(low), 116, 76) + glyph(str(high), 194, 76)
        # Swap arrows between the two digits.
        art += """
  <path d="M86 132 h84" stroke="#ffffff" stroke-width="7" stroke-linecap="round"/>
  <path d="M164 122 l10 10 l-10 10" fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M92 122 l-10 10 l10 10" fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>"""

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
            f"{defs(theme_a, theme_b)}{body}{art}\n</svg>\n")


def main():
    for model, stem, kind, value, theme in CARDS:
        path = os.path.join(HERE, f"iit_card_{model:03d}_{stem}.svg")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(svg(kind, value, theme))
    print(f"Wrote {len(CARDS)} SVG sources to {HERE}")


if __name__ == "__main__":
    main()
