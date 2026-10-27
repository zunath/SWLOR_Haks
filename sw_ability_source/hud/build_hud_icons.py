"""Ship HUD icon CENTER ARTWORK generator (same pipeline as the Command, Ace, Ordnance and Bulwark lines).

Reuses the Command builder's SVG helpers. Writes src/<resref>.svg, out_png/<resref>.png and
out_tga/<resref>.tga (32x32, bottom-left origin); the project icon tool stamps the semantic frame.

Run:  python build_hud_icons.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
sys.path.insert(0, HERE + "/../command")

import build_command_icons as bc  # noqa: E402
from PIL import Image  # noqa: E402

SRC = HERE + "/src"
RENDER = HERE + "/render256"
OUT_PNG = HERE + "/out_png"
OUT_TGA = HERE + "/out_tga"
for d in (SRC, RENDER, OUT_PNG, OUT_TGA):
    os.makedirs(d, exist_ok=True)


def all_weapons():
    s = bc.Svg()
    bc.background(s, 7350, nebula=("#1a4a8a", "#2a2a7a"))
    cx, cy, sc = 128, 152, 0.78
    # every weapon firing at once: five bolts fanning out from the ship's guns
    shots = [
        (128, 99, 128, 34, 5.0),
        (94, 135, 80, 36, 4.4),
        (162, 135, 176, 36, 4.4),
        (66, 168, 54, 98, 3.8),
        (190, 168, 202, 98, 3.8),
    ]
    for x1, y1, x2, y2, w in shots:
        bc.bolt(s, x1, y1, x2, y2, w, glow="#1f8fff", mid="#7fe0ff", core="#f2ffff")
    bc.fighter(s, cx, cy, sc, cannons=True)
    for x, y, r in ((128, 99, 13), (94, 135, 11), (162, 135, 11), (66, 168, 9), (190, 168, 9)):
        bc.flare(s, x, y, r, c1="#a8ecff", c2="#2a8cff")
    return s


JOBS = [("ife_allweap", all_weapons)]


def main():
    for resref, fn in JOBS:
        svg = fn()
        sp = f"{SRC}/{resref}.svg"
        open(sp, "w", encoding="utf-8").write(svg.render(256))
        rp = f"{RENDER}/{resref}.png"
        bc.magick_render(sp, rp)
        im = bc.finalize(Image.open(rp))
        im.save(f"{OUT_PNG}/{resref}.png")
        bc.write_tga(im, f"{OUT_TGA}/{resref}.tga")
        big = f"{RENDER}/{resref}_512.svg"
        open(big, "w", encoding="utf-8").write(svg.render(512))
        bc.magick_render(big, f"{RENDER}/{resref}_512.png")
    print("ok", len(JOBS), "icons")


if __name__ == "__main__":
    main()
