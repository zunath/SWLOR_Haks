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


def jump_to_signature():
    s = bc.Svg()
    bc.background(s, 7358, nebula=("#1a5a7a", "#2a2a6a"))
    tx, ty = 176, 82  # the scanned signature the ship is jumping to
    sx, sy = 116, 143  # the ship, mid micro-jump
    ux, uy = 0.7071, -0.7071  # flight direction (up-right)
    px, py = 0.7071, 0.7071  # across the flight path
    # pulsing signature marker: fading sonar rings around a bright contact
    for r, op in ((44, 0.28), (34, 0.5)):
        s.glow(bc.circle(tx, ty, r, "none", 1, "#3fe8c8", 4), 3, op)
        s.add(bc.circle(tx, ty, r, "none", op, "#8ff8e0", 1.6))
    bc.reticle(s, tx, ty, 22, color="#5ff0d0", w=3.0)
    bc.flare(s, tx, ty, 15, c1="#c8fff0", c2="#20c8a0")
    s.add(bc.circle(tx, ty, 3.4, "#ffffff"))
    # micro-jump blur: light streaks trailing back from the ship
    for off, l0, l1, w in ((-24, 22, 70, 2.6), (-12, 26, 92, 3.4), (0, 36, 104, 4.2),
                           (12, 26, 92, 3.4), (24, 22, 70, 2.6)):
        x0, y0 = sx + px * off - ux * l0, sy + py * off - uy * l0
        x1, y1 = sx + px * off - ux * l1, sy + py * off - uy * l1
        g = s.lin(x0, y0, x1, y1, [(0, "#e8fbff", 0.95), (0.35, "#5fd0ff", 0.6), (1, "#1f6fff", 0)])
        s.glow(bc.line(x0, y0, x1, y1, g, w * 2.4), w)
        s.add(bc.line(x0, y0, x1, y1, g, w * 0.6))
    # fading after-images of the hull along the jump path
    for back, op in ((44, 0.3), (23, 0.5)):
        bc.escort(s, sx - ux * back, sy - uy * back, 1.0, rot=45, flame=0.0,
                  hull=("#bff2ff", "#3f8fd0", "#0c2448"), glow="#5fd0ff", trim="#7fe8ff")
        s.body[-1] = f'<g opacity="{op}">{s.body[-1]}</g>'
    bc.escort(s, sx, sy, 1.0, rot=45, flame=1.2)
    # jump flash where the nose is punching forward
    nx, ny = sx + ux * 48, sy + uy * 48
    bc.starburst(s, nx, ny, 13, 3.2, rot=45, color="#ffffff", glowc="#8fe3ff", gl=3)
    return s


JOBS = [("ife_allweap", all_weapons), ("ife_sigjump", jump_to_signature)]


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
