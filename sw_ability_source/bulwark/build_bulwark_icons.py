"""Bulwark perk line ability-icon CENTER ARTWORK generator (same pipeline as the Ace and Ordnance lines).

Authors each icon as an illustrated SVG (src/<resref>.svg, 256x256 viewBox),
rasterizes it with ImageMagick (librsvg) at 256 px, downsamples to 32x32 with
LANCZOS, applies light edge definition, flattens the outer 3 px ring to plain
dark background (the project icon tool stamps the semantic frame there) and
writes out_png/<resref>.png, out_tga/<resref>.tga (32-bit, bottom-left origin,
descriptor 8), contact_center.png and 512 px previews.

Run:  python build_bulwark_icons.py
"""
import math
import os
import random
import struct
import subprocess

from PIL import Image, ImageEnhance, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
SRC = HERE + "/src"
RENDER = HERE + "/render256"
OUT_PNG = HERE + "/out_png"
OUT_TGA = HERE + "/out_tga"
PREV = HERE + "/previews"
for d in (SRC, RENDER, OUT_PNG, OUT_TGA, PREV):
    os.makedirs(d, exist_ok=True)

BG_RING = (4, 7, 20)
CLIP = (27, 27, 202, 202)  # nothing may reach the outer 3 px (24 units) ring


# ---------------------------------------------------------------- svg helpers
def pts(p):
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in p)


class Svg:
    def __init__(self):
        self.defs = []
        self.body = []
        self.bg = []
        self.n = 0
        self.blurs = {}

    def uid(self, p):
        self.n += 1
        return f"{p}{self.n}"

    @staticmethod
    def _stops(stops):
        out = []
        for st in stops:
            o, c = st[0], st[1]
            a = st[2] if len(st) > 2 else 1
            out.append(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>')
        return "".join(out)

    def lin(self, x1, y1, x2, y2, stops):
        i = self.uid("l")
        self.defs.append(
            f'<linearGradient id="{i}" gradientUnits="userSpaceOnUse" x1="{x1}" y1="{y1}" '
            f'x2="{x2}" y2="{y2}">{self._stops(stops)}</linearGradient>')
        return f"url(#{i})"

    def rad(self, cx, cy, r, stops, fx=None, fy=None):
        i = self.uid("r")
        f = f' fx="{fx}" fy="{fy}"' if fx is not None else ""
        self.defs.append(
            f'<radialGradient id="{i}" gradientUnits="userSpaceOnUse" cx="{cx}" cy="{cy}" r="{r}"{f}>'
            f'{self._stops(stops)}</radialGradient>')
        return f"url(#{i})"

    def blur(self, std):
        key = round(std, 1)
        if key not in self.blurs:
            i = f"b{len(self.blurs)}"
            self.blurs[key] = i
            self.defs.append(
                f'<filter id="{i}" filterUnits="userSpaceOnUse" x="-64" y="-64" width="384" height="384">'
                f'<feGaussianBlur stdDeviation="{key}"/></filter>')
        return f"url(#{self.blurs[key]})"

    def add(self, s):
        self.body.append(s)

    def glow(self, s, std, op=1.0):
        self.body.append(f'<g filter="{self.blur(std)}" opacity="{op}">{s}</g>')

    def render(self, size=256):
        clip = (f'<clipPath id="clip"><rect x="{CLIP[0]}" y="{CLIP[1]}" width="{CLIP[2]}" '
                f'height="{CLIP[3]}"/></clipPath>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
                f'viewBox="0 0 256 256"><defs>{clip}{"".join(self.defs)}</defs>'
                f'{"".join(self.bg)}<g clip-path="url(#clip)">{"".join(self.body)}</g></svg>')


def line(x1, y1, x2, y2, stroke, w, op=1, cap="round"):
    return (f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{stroke}" '
            f'stroke-width="{w:.2f}" stroke-linecap="{cap}" opacity="{op}"/>')


def circle(cx, cy, r, fill, op=1, stroke="none", sw=0):
    return (f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}" opacity="{op}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>')


def ellipse(cx, cy, rx, ry, fill, op=1, stroke="none", sw=0, rot=0):
    return (f'<ellipse cx="{cx:.2f}" cy="{cy:.2f}" rx="{rx:.2f}" ry="{ry:.2f}" fill="{fill}" '
            f'opacity="{op}" stroke="{stroke}" stroke-width="{sw}" '
            f'transform="rotate({rot} {cx:.2f} {cy:.2f})"/>')


def polyline(p, stroke, w, op=1):
    return (f'<polyline points="{pts(p)}" fill="none" stroke="{stroke}" stroke-width="{w}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="{op}"/>')


def poly(p, fill, stroke="none", sw=0, op=1):
    return (f'<polygon points="{pts(p)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
            f'stroke-linejoin="round" opacity="{op}"/>')


def path(d, fill, stroke="none", sw=0, op=1, extra=""):
    return (f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
            f'stroke-linejoin="round" stroke-linecap="round" opacity="{op}" {extra}/>')


def star4(cx, cy, R, r, rot=0):
    p = []
    for i in range(8):
        a = math.radians(rot + i * 45 - 90)
        rr = R if i % 2 == 0 else r
        p.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return p


def rect_line(x1, y1, x2, y2, w):
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    nx, ny = -dy / L * w / 2, dx / L * w / 2
    return [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]


# --------------------------------------------------------------- shared art
def background(s, seed, nebula=("#5b2a95", "#1a6a8a")):
    g = s.rad(128, 128, 190, [(0, "#142058"), (0.55, "#080e2c"), (1, "#02030b")])
    s.bg.append(f'<rect width="256" height="256" fill="{g}"/>')
    s.bg.append(f'<g filter="{s.blur(22)}" opacity="0.55">{ellipse(70, 62, 62, 34, nebula[0], 1, rot=-25)}</g>')
    s.bg.append(f'<g filter="{s.blur(22)}" opacity="0.5">{ellipse(190, 196, 58, 30, nebula[1], 1, rot=-20)}</g>')
    rnd = random.Random(seed)
    for _ in range(20):
        x, y = rnd.uniform(32, 224), rnd.uniform(32, 224)
        r = rnd.choice([1.5, 1.8, 2.2, 2.6])
        c = rnd.choice(["#ffffff", "#cfe6ff", "#ffe9c8", "#9fc4ff"])
        s.bg.append(circle(x, y, r, c, rnd.uniform(0.45, 0.95)))


def bolt(s, x1, y1, x2, y2, w, glow="#ff4a14", mid="#ff9a3c", core="#fff6dc", gscale=2.7):
    g = s.lin(x1, y1, x2, y2, [(0, glow, 0), (0.5, glow, 0.75), (1, glow, 1)])
    s.glow(line(x1, y1, x2, y2, g, w * gscale), w * 0.9)
    m = s.lin(x1, y1, x2, y2, [(0, mid, 0), (0.45, mid, 1), (1, mid, 1)])
    s.add(line(x1, y1, x2, y2, m, w))
    c = s.lin(x1, y1, x2, y2, [(0, core, 0.1), (0.4, core, 1), (1, core, 1)])
    s.add(line(x1, y1, x2, y2, c, w * 0.45))


def flare(s, cx, cy, r, c1="#ffe2b0", c2="#ff6a2a"):
    g = s.rad(cx, cy, r, [(0, "#ffffff", 1), (0.22, "#ffffff", 0.95), (0.5, c1, 0.6),
                          (0.8, c2, 0.28), (1, c2, 0)])
    s.add(circle(cx, cy, r, g))


def starburst(s, cx, cy, R, r, rot=0, color="#ffffff", glowc="#ffc58a", gl=4):
    p = star4(cx, cy, R, r, rot)
    s.glow(poly(p, glowc), gl, 0.9)
    s.add(poly(p, color))


def sparks(s, cx, cy, items, color, w=3.2, op=1.0):
    """items: (angle_deg, r0, r1) radial streaks."""
    for a, r0, r1 in items:
        ra = math.radians(a)
        s.add(line(cx + r0 * math.cos(ra), cy + r0 * math.sin(ra),
                   cx + r1 * math.cos(ra), cy + r1 * math.sin(ra), color, w, op))


def steel_gradients(s):
    return s.lin(0, -10, 0, 66, [(0, "#d3e0f2"), (0.35, "#7d8ea8"), (1, "#222a3a")])


def fighter(s, cx, cy, sc, rot=0, cannons=True, tone=1.0):
    """Swept-wing starfighter, top-down, nose toward local -y. Local frame is ~+-90 x -62..68."""
    wing = s.lin(0, -10, 0, 68, [(0, "#d5e2f4"), (0.3, "#8294ae"), (1, "#232c3d")])
    body = s.lin(-12, 0, 12, 0, [(0, "#1f2736"), (0.4, "#c9d7ea"), (0.7, "#7587a2"), (1, "#1b2230")])
    eng = s.rad(0, 0, 14, [(0, "#ffffff", 1), (0.35, "#8fe3ff", 0.95), (1, "#2a8cff", 0)])
    cock = s.lin(0, -36, 0, -8, [(0, "#e8fbff"), (0.5, "#4cc4ff"), (1, "#0c3a7a")])
    o = []
    for sx in (-1, 1):
        o.append(f'<g transform="translate({sx * 6} 60)">{circle(0, 0, 13, eng)}</g>')
    for sx in (-1, 1):
        w = [(sx * 10, -8), (sx * 90, 56), (sx * 86, 68), (sx * 52, 57), (sx * 14, 53)]
        o.append(poly(w, wing, "#080b12", 3))
        o.append(line(sx * 15, -3, sx * 85, 53, "#f2f8ff", 2.6, 0.9))
        o.append(line(sx * 30, 27, sx * 56, 56, "#0b1018", 2.2, 0.7))
        o.append(circle(sx * 84, 63, 3, "#ff5a3c"))
        if cannons:
            o.append(poly(rect_line(sx * 44, 28, sx * 44, -16, 7), "#2e3849", "#080b12", 2))
            o.append(line(sx * 42.5, 24, sx * 42.5, -13, "#aab9cf", 1.8, 0.9))
            o.append(circle(sx * 44, -17, 4.4, "#ffd9a0"))
    fus = [(0, -64), (7, -42), (11, -10), (13, 32), (8, 60), (0, 52), (-8, 60), (-13, 32), (-11, -10), (-7, -42)]
    o.append(poly(fus, body, "#080b12", 3))
    o.append(ellipse(0, -22, 5.8, 15, cock, 1, "#06243e", 1.5))
    o.append(ellipse(-1.6, -27, 1.8, 6, "#ffffff", 0.9))
    o.append(line(0, 12, 0, 46, "#0b1018", 2, 0.6))
    s.add(f'<g transform="translate({cx} {cy}) rotate({rot}) scale({sc})">{"".join(o)}</g>')


# --------------------------------------------------------------- bulwark art
GOLD_L, GOLD_M, GOLD_D = "#fff0b0", "#f2b83a", "#8a5410"


def arc_d(cx, cy, R, a0, a1):
    """SVG arc path from angle a0 to a1 (degrees, clockwise from +x, y down)."""
    x0, y0 = cx + R * math.cos(math.radians(a0)), cy + R * math.sin(math.radians(a0))
    x1, y1 = cx + R * math.cos(math.radians(a1)), cy + R * math.sin(math.radians(a1))
    large = 1 if abs(a1 - a0) > 180 else 0
    return f"M{x0:.2f},{y0:.2f} A{R},{R} 0 {large} 1 {x1:.2f},{y1:.2f}"


def hexagon(cx, cy, r, rot=0):
    return [(cx + r * math.cos(math.radians(rot + 60 * i)), cy + r * math.sin(math.radians(rot + 60 * i)))
            for i in range(6)]


def hex_cells(cx, cy, hs, test):
    """Centres of a flat-top hex grid (circumradius hs) for which test(x, y) is true."""
    out = []
    dx, dy = 1.5 * hs, math.sqrt(3) * hs
    for i in range(-14, 15):
        for j in range(-14, 15):
            x = cx + i * dx
            y = cy + j * dy + (dy / 2 if i % 2 else 0)
            if test(x, y):
                out.append((x, y))
    return out


def hex_arc(s, cx, cy, R, a0, a1, bright=1.0, hs=9.5, color=GOLD_M, glowc="#ffc94a"):
    """Hex-plated deflector arc: glow band, hex cells along the band, bright rim line."""
    def inside(x, y):
        d = math.hypot(x - cx, y - cy)
        a = math.degrees(math.atan2(y - cy, x - cx))
        if a < a0 - 40:
            a += 360
        return abs(d - R) < hs * 0.95 and a0 - 3 <= a <= a1 + 3
    s.glow(path(arc_d(cx, cy, R, a0, a1), "none", glowc, 16 * bright, 0.75 * min(bright, 1.2)), 6, 0.9)
    cells = hex_cells(cx, cy, hs, inside)
    for x, y in cells:
        d = math.hypot(x - cx, y - cy)
        k = 0.5 + 0.5 * (d - (R - hs)) / (2 * hs)
        s.add(poly(hexagon(x, y, hs * 0.92, 0), color, "#fff6d0", 1.5, 0.22 + 0.3 * k * bright))
    s.add(path(arc_d(cx, cy, R + hs * 0.8, a0, a1), "none", "#fff4c8", 3.6, 0.95))
    s.add(path(arc_d(cx, cy, R + hs * 0.8, a0, a1), "none", "#ffffff", 1.4, 1))
    s.add(path(arc_d(cx, cy, R - hs * 0.8, a0, a1), "none", GOLD_M, 2.2, 0.85))


def armored_ship(s, cx, cy, sc, rot=0, flame=1.0, shield_glow=0.0):
    """Heavy armoured frigate, top-down, nose toward local -y. Symmetric. Local frame ~ +-52 x -68..70."""
    hull = s.lin(-36, 0, 36, 0, [(0, "#15303f"), (0.25, "#5aa0b8"), (0.5, "#cfeaf2"), (0.75, "#4a8aa4"), (1, "#122a38")])
    spon = s.lin(-56, 0, -22, 0, [(0, "#10232f"), (0.55, "#4c8ea6"), (1, "#a9d6e4")])
    gold = s.lin(0, -70, 0, -34, [(0, GOLD_L), (0.4, GOLD_M), (1, GOLD_D)])
    eng = s.rad(0, 0, 16, [(0, "#ffffff", 1), (0.35, "#8fe8ff", 0.95), (1, "#2a9cff", 0)])
    glass = s.lin(0, -14, 0, 14, [(0, "#e8fbff"), (0.5, "#4cc4e0"), (1, "#0c3a5a")])
    o = []
    # engine flames
    for ex in (-18, 0, 18):
        fl = s.lin(0, 62, 0, 62 + 38 * flame, [(0, "#ffffff", 1), (0.3, "#9af0ff", 0.9), (1, "#2a9cff", 0)])
        o.append(f'<g filter="{s.blur(3)}"><polygon points="{ex - 8},62 {ex},{62 + 40 * flame:.1f} {ex + 8},62" fill="{fl}"/></g>')
        o.append(f'<g transform="translate({ex} 62)">{circle(0, 0, 11, eng)}</g>')
    # side sponsons with armour plating
    for sx in (-1, 1):
        sp = [(sx * 22, -10), (sx * 50, 6), (sx * 54, 40), (sx * 46, 62), (sx * 22, 58)]
        o.append(poly(sp, spon if sx < 0 else s.lin(56, 0, 22, 0, [(0, "#10232f"), (0.55, "#4c8ea6"), (1, "#a9d6e4")]), "#06121a", 3))
        o.append(poly([(sx * 26, 4), (sx * 50, 10), (sx * 53, 34), (sx * 26, 30)], gold, "#06121a", 2))
        o.append(line(sx * 29, 8, sx * 49, 14, "#fff6d0", 1.8, 0.85))
        o.append(line(sx * 38, 40, sx * 38, 58, "#06121a", 2, 0.6))
        o.append(circle(sx * 44, 50, 5, "#1a2c3a", 1, "#06121a", 1.6))
        o.append(circle(sx * 44, 50, 2.2, "#7fe8ff"))
    # main hull
    h = [(0, -52), (13, -44), (28, -16), (31, 22), (24, 58), (10, 66), (-10, 66), (-24, 58), (-31, 22), (-28, -16), (-13, -44)]
    o.append(poly(h, hull, "#06121a", 3.2))
    o.append(line(0, -40, 0, 56, "#0b1c28", 2.2, 0.55))
    for yy in (-6, 18, 40):
        o.append(line(-27, yy, 27, yy, "#0b1c28", 1.8, 0.5))
    # blunt armoured prow plate
    o.append(poly([(-17, -54), (-8, -63), (8, -63), (17, -54), (13, -36), (-13, -36)], gold, "#06121a", 3))
    o.append(line(-9, -58, 9, -58, "#ffffff", 2.2, 0.9))
    o.append(line(-11, -45, 11, -45, "#7a4a0c", 1.6, 0.7))
    # turrets and bridge
    for tx, ty in ((-15, 14), (15, 14)):
        o.append(circle(tx, ty, 7, "#1d3442", 1, "#06121a", 2))
        o.append(circle(tx - 1.5, ty - 1.5, 2.6, "#c6e8f2", 0.9))
        o.append(line(tx, ty, tx, ty - 14, "#8fb4c4", 3.4))
    o.append(ellipse(0, -8, 8, 15, glass, 1, "#06243e", 1.8))
    o.append(ellipse(-2.4, -14, 2, 6.5, "#ffffff", 0.9))
    if shield_glow > 0:
        o.append(circle(0, 0, 80, s.rad(0, 0, 80, [(0.55, "#ffd870", 0), (1, "#ffd870", shield_glow)])))
    s.add(f'<g transform="translate({cx:.2f} {cy:.2f}) rotate({rot}) scale({sc * 1.16:.3f} {sc})">{"".join(o)}</g>')


def raider(s, cx, cy, sc, rot=0):
    """Hostile dart-wing raider: dark graphite hull, crimson glow. Symmetric, nose toward local -y."""
    hull = s.lin(-30, 0, 30, 0, [(0, "#14101c"), (0.3, "#6a5a82"), (0.5, "#b7a8cc"), (0.7, "#5a4a72"), (1, "#100c18")])
    wing = s.lin(0, -10, 0, 60, [(0, "#8a7aa4"), (0.5, "#40345a"), (1, "#14101c")])
    eng = s.rad(0, 0, 11, [(0, "#ffffff", 1), (0.4, "#ff8aa0", 0.9), (1, "#ff2a4a", 0)])
    glass = s.lin(0, -30, 0, -6, [(0, "#ffd0d8"), (0.5, "#ff4a6a"), (1, "#5a0a1c")])
    o = []
    for ex in (-9, 9):
        o.append(f'<g transform="translate({ex} 56)">{circle(0, 0, 9, eng)}</g>')
    for sx in (-1, 1):
        w = [(sx * 8, -16), (sx * 46, 24), (sx * 50, 58), (sx * 26, 46), (sx * 10, 52)]
        o.append(poly(w, wing, "#07040c", 2.8))
        o.append(line(sx * 12, -6, sx * 46, 28, "#d7ccea", 1.8, 0.8))
        o.append(line(sx * 48, 30, sx * 48, 54, "#ff4a6a", 3))
    f = [(0, -62), (8, -36), (12, -4), (12, 40), (7, 56), (-7, 56), (-12, 40), (-12, -4), (-8, -36)]
    o.append(poly(f, hull, "#07040c", 2.8))
    o.append(ellipse(0, -20, 5, 13, glass, 1, "#3a0610", 1.4))
    o.append(ellipse(-1.4, -25, 1.6, 5, "#ffffff", 0.85))
    s.add(f'<g transform="translate({cx:.2f} {cy:.2f}) rotate({rot}) scale({sc})">{"".join(o)}</g>')


def shard(s, x, y, size, ang, tone=0):
    """A jagged chunk of hull plating."""
    a = math.radians(ang)
    base = [(-1.0, -0.4), (0.2, -1.0), (1.1, 0.1), (0.3, 0.9), (-0.7, 0.6)]
    p = []
    for u, v in base:
        ru, rv = u * size, v * size * 0.7
        p.append((x + ru * math.cos(a) - rv * math.sin(a), y + ru * math.sin(a) + rv * math.cos(a)))
    cols = [("#cfe3ee", "#3a5a70"), ("#ffe9a0", "#8a5410"), ("#a8d0e0", "#1c3648")]
    fl, sd = cols[tone % 3]
    g = s.lin(p[0][0], p[0][1], p[2][0], p[2][1], [(0, fl), (1, sd)])
    s.add(poly(p, g, "#06121a", 1.8))


def laser_hit(s, x1, y1, x2, y2, w=4.0, glow="#ff2a52", mid="#ff6a86", core="#fff0f2"):
    bolt(s, x1, y1, x2, y2, w, glow=glow, mid=mid, core=core, gscale=2.4)


def reticle(s, cx, cy, r, color="#ff5a7a", w=2.6, op=1.0):
    s.glow(circle(cx, cy, r, "none", 1, color, w + 3), 2.5, 0.8 * op)
    s.add(circle(cx, cy, r, "none", op, "#14040a", w + 2.2))
    s.add(circle(cx, cy, r, "none", op, color, w))
    for a in (0, 90, 180, 270):
        ra = math.radians(a)
        s.add(line(cx + (r - 4) * math.cos(ra), cy + (r - 4) * math.sin(ra),
                   cx + (r + 6) * math.cos(ra), cy + (r + 6) * math.sin(ra), color, w, op))


# ------------------------------------------------------------- family 1: Shield Ram
def shield_ram(rank):
    s = Svg()
    background(s, 2101, nebula=("#1a5a7a", "#8a5a1a"))
    cx, contact = 128, 116
    # speed streaks behind the ram
    for dx in (-34, -16, 16, 34):
        g = s.lin(0, 150, 0, 224, [(0, "#9fe0ff", 0.0), (0.5, "#9fe0ff", 0.4), (1, "#9fe0ff", 0)])
        s.add(line(cx + dx, 150, cx + dx, 224, g, 2.4))
    # shockwave rings (flattened discs) under the ships
    rings = [(26, 12, 5), (42, 19, 5), (58, 26, 5), (74, 33, 4), (90, 40, 4)][:rank + 1]
    for i, (rx, ry, w) in enumerate(rings):
        a = 0.95 - 0.12 * i
        s.glow(ellipse(cx, contact, rx, ry, "none", 1, "#ffd070", w + 4), 4, 0.85)
        s.add(ellipse(cx, contact, rx, ry, "none", 1, "#fff4d0", w, ))
        s.add(ellipse(cx, contact, rx, ry, "none", a, "#ffffff", w * 0.4))
    s.add(circle(cx, contact, 40 + 5 * rank, s.rad(cx, contact, 40 + 5 * rank, [(0, "#fff6d0", 0.6), (0.5, "#ffc94a", 0.3), (1, "#ffc94a", 0)])))
    # rammed enemy, knocked a little off axis
    raider(s, cx + 3, 76, 0.64, rot=186)
    # ram ship with its glowing deflector in front
    armored_ship(s, cx, 176, 0.66, rot=0, flame=1.0 + 0.12 * rank)
    hex_arc(s, cx, 138, 44, -160, -20, bright=0.9 + 0.1 * rank, hs=8.5)
    # impact flash and shards
    flare(s, cx, contact, 20 + 3 * rank, c1="#fff0c0", c2="#ffb030")
    starburst(s, cx, contact, 20 + 4 * rank, 5, rot=0, color="#ffffff", glowc="#ffd870", gl=4)
    starburst(s, cx, contact, 12 + 2 * rank, 4, rot=45, color="#ffffff", glowc="#fff0c0", gl=3)
    s.add(circle(cx, contact, 6, "#ffffff"))
    angs = [-168, -12, -140, -40, -178, -2, -120, -60, -156, -24][: 4 + 2 * (rank - 1)]
    for k, a in enumerate(angs):
        r = 44 + 11 * (k % 3) + 3 * rank
        ra = math.radians(a)
        shard(s, cx + r * math.cos(ra) * 1.12, contact + r * math.sin(ra) * 0.75 + (5 if k % 2 else -3),
              7 + (k % 3) * 2, a + 40 * k, tone=k)
        s.add(line(cx + (r - 22) * math.cos(ra) * 1.12, contact + (r - 22) * math.sin(ra) * 0.75,
                   cx + (r - 10) * math.cos(ra) * 1.12, contact + (r - 10) * math.sin(ra) * 0.75, "#fff0c0", 2.4, 0.85))
    return s


# ------------------------------------------------------------- family 2: Defiance
def defiance(rank):
    s = Svg()
    background(s, 2202, nebula=("#1a5a7a", "#7a2a4a"))
    cx, cy = 128, 180
    # hex deflector arc well in front of the ship
    R = 70
    s.add(circle(cx, cy, R + 14, s.rad(cx, cy, R + 14, [(0.7, "#ffc94a", 0), (0.9, "#ffc94a", 0.12), (1, "#ffc94a", 0)])))
    armored_ship(s, cx, cy, 0.70, rot=0, flame=1.0)
    hex_arc(s, cx, cy, R, -158, -22, bright=0.9 + 0.12 * rank, hs=9.5)
    # incoming fire converging, bouncing off
    n = rank + 2
    spread = {3: [-62, 0, 62], 4: [-70, -24, 24, 70], 5: [-76, -40, 0, 40, 76]}[n]
    for k, a in enumerate(spread):
        ang = -90 + a * 0.78
        ra = math.radians(ang)
        hx, hy = cx + (R + 4) * math.cos(ra), cy + (R + 4) * math.sin(ra)
        # source off at the top, spread wider than the hit points
        sx_, sy_ = cx + a * 1.12, 52 + abs(a) * 0.28
        reticle(s, sx_, sy_, 9, op=0.95)
        laser_hit(s, sx_, sy_ + 10, hx, hy, 3.6)
        # deflected streak bouncing away
        dx, dy = hx - sx_, hy - sy_
        L = math.hypot(dx, dy)
        nx_, ny_ = math.cos(ra), math.sin(ra)  # surface normal
        dot = dx / L * nx_ + dy / L * ny_
        rx, ry = dx / L - 2 * dot * nx_, dy / L - 2 * dot * ny_
        s.add(line(hx, hy, hx + rx * 22, hy + ry * 22, "#fff0f2", 2.6, 0.95))
        s.glow(line(hx, hy, hx + rx * 22, hy + ry * 22, "#ff6a86", 5), 2, 0.8)
        flare(s, hx, hy, 11, c1="#fff0c8", c2="#ffb030")
        s.add(circle(hx, hy, 3.6, "#ffffff"))
    return s


# ------------------------------------------------------------- family 3: Hail
def hail(rank):
    s = Svg()
    background(s, 2303, nebula=("#6a2a9a", "#1a5a7a"))
    cx, bx, by = 128, 128, 152
    # signal arcs radiating from the prow beacon
    for i, (R, w) in enumerate([(22, 6), (40, 6), (58, 6)][: 2 + rank]):
        op = 1.0 - 0.18 * i
        s.glow(path(arc_d(bx, by, R, -150, -30), "none", "#ffc94a", w + 5), 4, 0.8 * op)
        s.add(path(arc_d(bx, by, R, -150, -30), "none", "#1a0a02", w + 3, op))
        g = s.lin(bx - R, by, bx + R, by, [(0, "#ffc94a"), (0.5, "#fff6d0"), (1, "#ffc94a")])
        s.add(path(arc_d(bx, by, R, -150, -30), "none", g, w, op))
        s.add(path(arc_d(bx, by, R, -150, -30), "none", "#ffffff", w * 0.35, op))
    # enemies turning toward the signal
    if rank == 1:
        # ghost of the old heading and the swing arc
        raider(s, 102, 74, 0.56, rot=148)
        s.add(path("M54,88 C50,58 66,44 80,44", "none", "#c9a8ff", 3, 0.7))
        s.add(path("M80,44 L72,39 M80,44 L76,53", "none", "#c9a8ff", 3, 0.7))
    else:
        raider(s, 62, 86, 0.52, rot=146)
        raider(s, 194, 86, 0.52, rot=214)
        raider(s, 128, 70, 0.50, rot=180)
    # a lock reticle on the signal (violet)
    if rank == 1:
        reticle(s, bx, by - 2, 34, color="#c89aff", w=3)
    else:
        reticle(s, bx, by - 2, 38, color="#c89aff", w=3)
    # our ship
    armored_ship(s, cx, 190, 0.50, rot=0, flame=0.9)
    # beacon at the prow
    flare(s, bx, by, 20, c1="#fff0c0", c2="#ffb030")
    starburst(s, bx, by, 18 + 3 * rank, 4.5, rot=0, color="#ffffff", glowc="#ffd870", gl=4)
    starburst(s, bx, by, 11, 3.5, rot=45, color="#ffffff", glowc="#fff0c0", gl=3)
    s.add(circle(bx, by, 5, "#ffffff"))
    return s


# ------------------------------------------------------------- family 4: Bulwark Stance
def bulwark_stance():
    s = Svg()
    background(s, 2404, nebula=("#1a4a9a", "#8a5a1a"))
    cx = cy = 128
    s.add(circle(cx, cy, 86, s.rad(cx, cy, 86, [(0, "#0e2a58"), (0.7, "#081634"), (1, "#050c20")])))
    rg = s.lin(40, 40, 216, 216, [(0, "#e6f6ff"), (0.35, "#4fc3ff"), (0.7, "#2a74c8"), (1, "#9fe0ff")])
    s.glow(circle(cx, cy, 90, "none", 1, "#4fc3ff", 9), 3.5, 0.9)
    s.add(circle(cx, cy, 90, "none", 1, "#04101f", 11))
    s.add(circle(cx, cy, 90, "none", 1, rg, 7.5))
    s.add(circle(cx, cy, 81.5, "none", 0.9, "#8fdcff", 1.6))
    for a in (0, 90, 180, 270):
        ra = math.radians(a)
        s.add(line(cx + 83 * math.cos(ra), cy + 83 * math.sin(ra), cx + 96 * math.cos(ra),
                   cy + 96 * math.sin(ra), "#eaf9ff", 5.5))
    armored_ship(s, 128, 156, 0.72, rot=0, flame=0.8)
    # bastion shield emblem in front
    mark = len(s.body)
    top = "M92,72 Q128,60 164,72 L164,106 C164,130 148,146 128,156 C108,146 92,130 92,106 Z"
    s.glow(path(top, "#ffc94a"), 7, 0.9)
    rim = s.lin(0, 60, 0, 156, [(0, "#fff6d0"), (0.4, GOLD_M), (1, "#7a4a0c")])
    s.add(path(top, rim, "#1a0e02", 4))
    field = s.lin(0, 70, 0, 150, [(0, "#8fe0f0"), (0.45, "#2a8aa8"), (1, "#0e2f48")])
    s.add(path("M99,77 Q128,67 157,77 L157,105 C157,125 144,139 128,148 C112,139 99,125 99,105 Z", field, "#06202e", 2.4))
    # hex bastion bevel on the field
    hx, hy = 128, 104
    s.add(poly(hexagon(hx, hy, 26, 30), "none", "#e8fbff", 3, 0.9))
    s.add(poly(hexagon(hx, hy, 17, 30), s.rad(hx, hy, 17, [(0, "#ffffff"), (0.5, "#ffe9a0"), (1, GOLD_M)]), "#7a4a0c", 2.4))
    for i in range(6):
        a = math.radians(30 + 60 * i)
        s.add(line(hx + 17 * math.cos(a), hy + 17 * math.sin(a), hx + 26 * math.cos(a), hy + 26 * math.sin(a), "#e8fbff", 2, 0.8))
    s.add(path("M104,84 Q128,74 150,82", "none", "#ffffff", 2.4, 0.55))
    # lower chevron plates
    s.add(path("M108,128 L128,140 L148,128", "none", "#fff0b0", 3.4, 0.9))
    s.body[mark:] = ['<g transform="translate(0 -9)">' + "".join(s.body[mark:]) + "</g>"]
    return s


# ------------------------------------------------------------- family 5: Invincible
def invincible():
    s = Svg()
    background(s, 2505, nebula=("#7a5a1a", "#1a5a9a"))
    cx = cy = 128
    R = 80
    # radiant halo
    s.add(circle(cx, cy, 108, s.rad(cx, cy, 108, [(0.6, "#ffe9a0", 0.55), (0.8, "#ffc94a", 0.25), (1, "#ffc94a", 0)])))
    for i in range(16):
        a = math.radians(i * 22.5 - 90)
        L = 100 if i % 2 == 0 else 90
        wd = 0.045 if i % 2 == 0 else 0.03
        p = [(cx + (R + 5) * math.cos(a - wd), cy + (R + 5) * math.sin(a - wd)),
             (cx + L * math.cos(a), cy + L * math.sin(a)),
             (cx + (R + 5) * math.cos(a + wd), cy + (R + 5) * math.sin(a + wd))]
        s.glow(poly(p, "#ffe9a0"), 2, 0.8)
        s.add(poly(p, "#fff6d0", op=0.95))
    # sphere body
    sph = s.rad(cx - 20, cy - 24, R * 1.2, [(0, "#fffbe8", 0.35), (0.55, "#ffd870", 0.22), (0.9, "#ffb030", 0.55), (1, "#fff0b0", 0.85)])
    s.defs.append(f'<clipPath id="sph"><circle cx="{cx}" cy="{cy}" r="{R}"/></clipPath>')
    s.add(circle(cx, cy, R, "#0a2238", 0.92))
    s.add(circle(cx, cy, R, sph))
    cells = hex_cells(cx + 3, cy, 12.5, lambda x, y: math.hypot(x - cx, y - cy) < R + 6)
    hexes = []
    for x, y in cells:
        d = math.hypot(x - cx, y - cy) / R
        hexes.append(poly(hexagon(x, y, 11.6, 0), "#ffd870", "#fff8dc", 1.7, 0.08 + 0.5 * d ** 2.2))
    s.add(f'<g clip-path="url(#sph)">{"".join(hexes)}</g>')
    # ship inside
    armored_ship(s, cx, cy + 2, 0.66, rot=0, flame=0.8)
    # fresnel rim and specular highlights
    s.glow(circle(cx, cy, R, "none", 1, "#ffe9a0", 10), 4, 0.95)
    rimg = s.lin(40, 40, 216, 216, [(0, "#ffffff"), (0.5, "#ffc94a"), (1, "#fff6d0")])
    s.add(circle(cx, cy, R, "none", 1, "#3a2204", 8.5))
    s.add(circle(cx, cy, R, "none", 1, rimg, 5.5))
    s.add(circle(cx, cy, R - 4, "none", 0.9, "#ffffff", 1.6))
    s.add(path(arc_d(cx, cy, R - 12, -150, -100), "none", "#ffffff", 3.6, 0.8))
    # shots shattering on the surface
    for a, off in [(-135, 0), (-45, 0), (45, 0), (135, 0)]:
        ra = math.radians(a)
        hx, hy = cx + (R + 2) * math.cos(ra), cy + (R + 2) * math.sin(ra)
        sx_, sy_ = cx + 122 * math.cos(ra), cy + 122 * math.sin(ra)
        laser_hit(s, sx_, sy_, hx, hy, 3.8)
        flare(s, hx, hy, 15, c1="#fff0c8", c2="#ffb030")
        s.add(circle(hx, hy, 4, "#ffffff"))
        for da in (-70, -35, 35, 70):
            rb = math.radians(a + da)
            s.add(line(hx + 4 * math.cos(rb), hy + 4 * math.sin(rb), hx + 15 * math.cos(rb), hy + 15 * math.sin(rb), "#fff0f2", 2.2, 0.95))
    return s


# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 5):
    JOBS.append((f"ife_bw_ram{r}", "ram", lambda r=r: shield_ram(r)))
for r in range(1, 4):
    JOBS.append((f"ife_bw_defi{r}", "defi", lambda r=r: defiance(r)))
for r in range(1, 3):
    JOBS.append((f"ife_bw_hail{r}", "hail", lambda r=r: hail(r)))
JOBS.append(("ife_bw_stance", "stance", bulwark_stance))
JOBS.append(("ife_bw_invinc", "invinc", invincible))


def magick_render(svg_path, png_path):
    subprocess.run(["magick", "-background", "none", svg_path, png_path], check=True)


def finalize(img):
    im = img.convert("RGB").resize((32, 32), Image.LANCZOS)
    im = im.filter(ImageFilter.UnsharpMask(radius=0.7, percent=55, threshold=0))
    im = ImageEnhance.Contrast(im).enhance(1.10)
    px = im.load()
    for y in range(32):
        for x in range(32):
            if min(x, y, 31 - x, 31 - y) < 3:
                px[x, y] = BG_RING
    return im


def write_tga(im, path_):
    w, h = im.size
    rgba = im.convert("RGBA")
    hdr = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 32, 8)
    rows = []
    for y in range(h - 1, -1, -1):  # bottom-up rows
        row = bytearray()
        for x in range(w):
            r, g, b, a = rgba.getpixel((x, y))
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    with open(path_, "wb") as f:
        f.write(hdr + b"".join(rows))


def read_tga(path_):
    data = open(path_, "rb").read()
    idlen, cmap, itype, _, _, _, _, _, w, h, bpp, desc = struct.unpack("<BBBHHBHHHHBB", data[:18])
    assert itype == 2 and bpp == 32 and desc == 8 and idlen == 0, (itype, bpp, desc)
    body = data[18:18 + w * h * 4]
    im = Image.new("RGBA", (w, h))
    for i in range(h):
        y = h - 1 - i
        for x in range(w):
            o = (i * w + x) * 4
            b, g, r, a = body[o:o + 4]
            im.putpixel((x, y), (r, g, b, a))
    return im


def main():
    finals = {}
    for resref, fam, fn in JOBS:
        svg = fn()
        sp = f"{SRC}/{resref}.svg"
        open(sp, "w", encoding="utf-8").write(svg.render(256))
        rp = f"{RENDER}/{resref}.png"
        magick_render(sp, rp)
        big = Image.open(rp)
        assert big.size == (256, 256), big.size
        im = finalize(big)
        im.save(f"{OUT_PNG}/{resref}.png")
        write_tga(im, f"{OUT_TGA}/{resref}.tga")
        chk = read_tga(f"{OUT_TGA}/{resref}.tga")
        assert list(chk.convert("RGB").getdata()) == list(im.getdata()), resref
        assert all(a == 255 for _, _, _, a in chk.getdata())
        finals[resref] = im
        if resref.endswith("1") or fam in ("stance", "invinc") or fam == "ram" and resref.endswith("4"):
            op = f"{PREV}/{resref}_{fam}.svg"
            open(op, "w", encoding="utf-8").write(svg.render(512))
            magick_render(op, f"{PREV}/{resref}.png")
            os.remove(op)
    # uniqueness check across all icons
    keys = list(finals)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert list(finals[keys[i]].getdata()) != list(finals[keys[j]].getdata()), (keys[i], keys[j])
    print("uniqueness assertion passed for", len(keys), "icons")
    rows = [["ife_bw_ram%d" % i for i in range(1, 5)], ["ife_bw_defi%d" % i for i in range(1, 4)],
            ["ife_bw_hail1", "ife_bw_hail2"], ["ife_bw_stance", "ife_bw_invinc"]]
    cell, pad = 128, 8
    W = pad + 4 * (cell + pad)
    H = pad + len(rows) * (cell + pad)
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    for ri, row in enumerate(rows):
        for ci, rr in enumerate(row):
            sheet.paste(finals[rr].resize((cell, cell), Image.NEAREST), (pad + ci * (cell + pad), pad + ri * (cell + pad)))
    sheet.save(HERE + "/contact_center.png")
    # 512 previews montage of rank-1 icons
    names = ["ife_bw_ram1", "ife_bw_defi1", "ife_bw_hail1", "ife_bw_stance", "ife_bw_invinc", "ife_bw_ram4"]
    mont = Image.new("RGB", (512 * 3, 512 * 2), (128, 128, 128))
    for i, f in enumerate(names):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB"), ((i % 3) * 512, (i // 3) * 512))
    mont.save(HERE + "/previews_512.png")
    print("ok", len(finals), "icons")


if __name__ == "__main__":
    main()
