"""Crew station icon CENTER ARTWORK generator (same pipeline as the Ship HUD and the Command line).

The Engineer's four power-routing feats share one motif: a ship's reactor core in a hexagonal casing, with its
power flowing to the subsystem the mode feeds. Balanced splits it three equal ways; Weapons feeds a cannon that
fires; Shields feeds a deflector dome; Engines feeds twin thrusters. Reuses the Command builder's SVG helpers.
Writes src/<resref>.svg, out_png/<resref>.png and out_tga/<resref>.tga (32x32, bottom-left origin); the project
icon tool stamps the semantic frame (Self).

Run:  python build_crew_icons.py
"""
import math
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


def reactor(s, cx, cy, r, core=("#ffffff", "#9ff4ff", "#1f8fff"), casing="#2c3a52"):
    """Hexagonal reactor casing with armoured plates, bolts and a glowing core."""
    shell = s.lin(cx - r, cy - r, cx + r, cy + r, [(0, "#dbe6f6"), (0.35, "#7d8ea8"), (1, "#1b2232")])
    s.glow(bc.circle(cx, cy, r * 1.05, core[2], 0.85), r * 0.45, 0.9)
    s.add(bc.poly(bc.hexagon(cx, cy, r, 30), shell, "#070a12", 3.2))
    s.add(bc.poly(bc.hexagon(cx, cy, r * 0.8, 30), casing, "#070a12", 2.2))
    for i in range(6):
        a = math.radians(30 + 60 * i)
        s.add(bc.circle(cx + r * 0.9 * math.cos(a), cy + r * 0.9 * math.sin(a), r * 0.07, "#c9d6ea", 1, "#070a12", 1.2))
    glow = s.rad(cx, cy, r * 0.62, [(0, core[0], 1), (0.3, core[0], 0.98), (0.6, core[1], 0.95), (1, core[2], 0.2)])
    s.add(bc.circle(cx, cy, r * 0.62, glow))
    s.add(bc.path(bc.arc_d(cx, cy, r * 0.46, 200, 300), "none", "#ffffff", 2.4, 0.75))
    for a in (0, 120, 240):
        ra = math.radians(a - 90)
        s.add(bc.line(cx + r * 0.2 * math.cos(ra), cy + r * 0.2 * math.sin(ra),
                      cx + r * 0.5 * math.cos(ra), cy + r * 0.5 * math.sin(ra), core[2], 2.6, 0.55))


def conduit(s, p, w, glow="#1f8fff", mid="#7fe0ff", core="#f2ffff"):
    """An energy stream along a polyline: wide glow, bright band and a white core."""
    s.glow(bc.polyline(p, glow, w * 2.6), w * 0.9, 0.9)
    s.add(bc.polyline(p, mid, w))
    s.add(bc.polyline(p, core, w * 0.42))


def node(s, cx, cy, r, c1="#a8ecff", c2="#2a8cff"):
    """A conduit terminal: a ring plate with a flare."""
    plate = s.lin(cx - r, cy - r, cx + r, cy + r, [(0, "#dbe6f6"), (0.45, "#7d8ea8"), (1, "#1b2232")])
    s.add(bc.circle(cx, cy, r, plate, 1, "#070a12", 2.4))
    bc.flare(s, cx, cy, r * 0.95, c1=c1, c2=c2)


def balanced():
    s = bc.Svg()
    bc.background(s, 7351, nebula=("#1a4a8a", "#2a2a7a"))
    cx, cy = 128, 136
    ends = []
    for a in (-90, 30, 150):
        ra = math.radians(a)
        ends.append((cx + 74 * math.cos(ra), cy + 74 * math.sin(ra)))
    for ex, ey in ends:
        conduit(s, [(cx, cy), (ex, ey)], 7.0)
    # an even ring joins the three outputs
    s.glow(bc.path(bc.arc_d(cx, cy, 74, -90, 269), "none", "#1f8fff", 6, 0.55), 4, 0.8)
    s.add(bc.path(bc.arc_d(cx, cy, 74, -90, 269), "none", "#7fe0ff", 2.2, 0.75))
    reactor(s, cx, cy, 36)
    for ex, ey in ends:
        node(s, ex, ey, 15)
    return s


def weapons():
    s = bc.Svg()
    bc.background(s, 7352, nebula=("#8a3a1a", "#5a1a3a"))
    hot = dict(glow="#ff4a14", mid="#ff9a3c", core="#fff6dc")
    # a heavy twin-rail cannon angled up and to the right, fed from the reactor below
    barrel = s.lin(0, -20, 0, 20, [(0, "#e4ecf8"), (0.4, "#8a9ab4"), (1, "#1b2232")])
    breech = s.lin(0, -26, 0, 26, [(0, "#c9d6ea"), (0.45, "#5d6e88"), (1, "#141a26")])
    vent = s.rad(0, 0, 9, [(0, "#ffffff", 1), (0.4, "#ffc070", 0.95), (1, "#ff5a14", 0)])
    s.add(f'<g transform="translate(132 124) rotate(-45)">'
          f'{bc.poly([(-34, -24), (8, -24), (14, -16), (14, 16), (8, 24), (-34, 24)], breech, "#070a12", 3)}'
          f'{bc.poly([(12, -14), (62, -12), (62, 12), (12, 14)], barrel, "#070a12", 3)}'
          f'{bc.poly([(60, -17), (76, -17), (76, 17), (60, 17)], breech, "#070a12", 3)}'
          f'{bc.line(16, -6, 58, -5, "#ffffff", 2.4, 0.85)}'
          f'{bc.line(-28, -14, 2, -14, "#ffffff", 2, 0.6)}'
          f'{bc.line(-12, -22, -12, 22, "#070a12", 2, 0.7)}'
          f'<g transform="translate(-22 0)">{bc.circle(0, 0, 8, vent)}</g>'
          f'</g>')
    bc.bolt(s, 186, 70, 214, 42, 9, **hot)
    bc.flare(s, 186, 70, 24, c1="#ffe2b0", c2="#ff6a2a")
    conduit(s, [(84, 184), (100, 160), (112, 146)], 8.5, **hot)
    reactor(s, 78, 190, 34, core=("#ffffff", "#ffd28a", "#ff6a1a"))
    return s


def shields():
    s = bc.Svg()
    bc.background(s, 7353, nebula=("#1a6a7a", "#1a3a8a"))
    cool = dict(glow="#1fb8ff", mid="#8af0ff", core="#f2ffff")
    cx, cy = 128, 190
    dome = s.rad(cx, cy, 112, [(0.62, "#3ad8ff", 0), (0.86, "#3ad8ff", 0.32), (0.97, "#c8faff", 0.7), (1, "#c8faff", 0)])
    s.add(bc.path(f"M{cx - 108},{cy} A108,108 0 0 1 {cx + 108},{cy} Z", dome))
    for i, a in enumerate(range(-160, -10, 30)):
        ra0, ra1 = math.radians(a), math.radians(a + 24)
        s.add(bc.path(bc.arc_d(cx, cy, 94 - (i % 2) * 6, a, a + 24), "none", "#bff8ff", 2.2, 0.55))
        s.add(bc.line(cx + 94 * math.cos(ra0), cy + 94 * math.sin(ra0), cx + 82 * math.cos(ra0), cy + 82 * math.sin(ra0),
                      "#bff8ff", 1.6, 0.4))
        s.add(bc.line(cx + 94 * math.cos(ra1), cy + 94 * math.sin(ra1), cx + 82 * math.cos(ra1), cy + 82 * math.sin(ra1),
                      "#bff8ff", 1.6, 0.4))
    s.glow(bc.path(bc.arc_d(cx, cy, 104, 180, 360), "none", "#3ad8ff", 10, 0.9), 5, 0.9)
    s.add(bc.path(bc.arc_d(cx, cy, 104, 180, 360), "none", "#ffffff", 3, 0.95))
    for a in (-135, -90, -45):
        ra = math.radians(a)
        conduit(s, [(cx, cy - 20), (cx + 86 * math.cos(ra), cy + 86 * math.sin(ra))], 5.2, **cool)
    reactor(s, cx, cy - 4, 34, core=("#ffffff", "#9ff4ff", "#1fb8ff"))
    return s


def engines():
    s = bc.Svg()
    bc.background(s, 7354, nebula=("#4a2a8a", "#7a3a2a"))
    nozzle = s.lin(0, -20, 0, 20, [(0, "#e4ecf8"), (0.4, "#8a9ab4"), (1, "#1b2232")])
    for nx in (92, 164):
        flame = s.lin(nx, 150, nx, 236, [(0, "#ffffff", 1), (0.25, "#ffd0f0", 0.95), (0.55, "#c86aff", 0.8), (1, "#5a2aff", 0)])
        s.glow(bc.poly([(nx - 26, 150), (nx, 238), (nx + 26, 150)], "#a04aff", "none", 0, 0.9), 8, 0.85)
        s.add(bc.poly([(nx - 22, 150), (nx, 232), (nx + 22, 150)], flame))
        s.add(bc.poly([(nx - 7, 150), (nx, 200), (nx + 7, 150)], "#ffffff", "none", 0, 0.9))
        bell = f"M{nx - 13},108 L{nx + 13},108 C{nx + 16},126 {nx + 22},140 {nx + 27},152 L{nx - 27},152 C{nx - 22},140 {nx - 16},126 {nx - 13},108 Z"
        s.add(bc.path(bell, nozzle, "#070a12", 3))
        s.add(bc.ellipse(nx, 151, 25, 6.5, "#ffe8ff", 0.95, "#070a12", 1.6))
        s.add(bc.line(nx - 16, 124, nx + 16, 124, "#070a12", 2, 0.7))
        s.add(bc.line(nx - 9, 113, nx - 4, 146, "#ffffff", 1.8, 0.6))
    violet = dict(glow="#a04aff", mid="#e0a8ff", core="#fff4ff")
    conduit(s, [(128, 84), (92, 108)], 6.2, **violet)
    conduit(s, [(128, 84), (164, 108)], 6.2, **violet)
    reactor(s, 128, 70, 32, core=("#ffffff", "#f0c8ff", "#a04aff"))
    return s


JOBS = [
    ("ife_pwrbal", balanced),
    ("ife_pwrwep", weapons),
    ("ife_pwrshd", shields),
    ("ife_pwreng", engines),
]


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
