"""Command perk line ability-icon CENTER ARTWORK generator (same pipeline as the Ace, Ordnance and Bulwark lines).

Authors each icon as an illustrated SVG (src/<resref>.svg, 256x256 viewBox),
rasterizes it with ImageMagick (librsvg) at 256 px, downsamples to 32x32 with
LANCZOS, applies light edge definition, flattens the outer 3 px ring to plain
dark background (the project icon tool stamps the semantic frame there) and
writes out_png/<resref>.png, out_tga/<resref>.tga (32-bit, bottom-left origin,
descriptor 8), contact_center.png and 512 px previews.

Run:  python build_command_icons.py
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



# ------------------------------------------------------------- command art
TEAL_L, TEAL_M, TEAL_D = "#d6fff2", "#3fd6b0", "#0b4a46"


def escort(s, cx, cy, sc, rot=0, hull=("#eef4fc", "#7a8fae", "#1b2434"), glow="#7fe8ff", flame=1.0, trim="#ffd870"):
    """Small arrowhead fleet escort, top-down, nose toward local -y. Local frame ~ +-34 x -44..40."""
    body = s.lin(-30, 0, 30, 0, [(0, hull[2]), (0.3, hull[1]), (0.5, hull[0]), (0.7, hull[1]), (1, hull[2])])
    wing = s.lin(0, -10, 0, 38, [(0, hull[0]), (0.5, hull[1]), (1, hull[2])])
    eng = s.rad(0, 0, 11, [(0, "#ffffff", 1), (0.4, glow, 0.9), (1, glow, 0)])
    cock = s.lin(0, -30, 0, -8, [(0, "#f2fdff"), (0.5, "#58c8f0"), (1, "#0c3a6a")])
    fl = s.lin(0, 28, 0, 28 + 34 * flame, [(0, "#ffffff", 1), (0.3, glow, 0.85), (1, glow, 0)])
    o = [f'<g filter="{s.blur(2.6)}"><polygon points="-8,28 0,{28 + 36 * flame:.1f} 8,28" fill="{fl}"/></g>',
         f'<g transform="translate(0 28)">{circle(0, 0, 11, eng)}</g>']
    for sx in (-1, 1):
        w = [(sx * 8, -12), (sx * 38, 24), (sx * 34, 38), (sx * 12, 28), (sx * 6, 32)]
        o.append(poly(w, wing, "#06101a", 3))
        o.append(line(sx * 12, -4, sx * 34, 28, "#ffffff", 2, 0.85))
        o.append(line(sx * 33, 26, sx * 33, 36, trim, 3.2))
    f = [(0, -46), (7, -26), (10, 8), (8, 30), (0, 36), (-8, 30), (-10, 8), (-7, -26)]
    o.append(poly(f, body, "#06101a", 3))
    o.append(ellipse(0, -16, 4.6, 11, cock, 1, "#06243e", 1.4))
    o.append(ellipse(-1.3, -20, 1.4, 4.6, "#ffffff", 0.9))
    s.add(f'<g transform="translate({cx:.2f} {cy:.2f}) rotate({rot}) scale({sc})">{"".join(o)}</g>')


def hostile_escort(s, cx, cy, sc, rot=0, flame=0.8):
    escort(s, cx, cy, sc, rot, hull=("#e8d8dc", "#8a5a6a", "#241018"), glow="#ff6a50", flame=flame, trim="#ff4a3a")


def crack(s, pts_, w=3.0, color="#ff6a3c", core="#fff0d8"):
    s.glow(polyline(pts_, color, w * 2.6), 2.4, 0.9)
    s.add(polyline(pts_, "#2a0a08", w + 2.2))
    s.add(polyline(pts_, color, w))
    s.add(polyline(pts_, core, w * 0.35))


def hull_top(x):
    return 139 + ((x - 128) / 98.0) ** 2 * 27


# ------------------------------------------------------------- family 1: Emergency Repair
def emergency_repair(rank):
    s = Svg()
    background(s, 4101, nebula=("#0e7a5a", "#1a3a8a"))
    # armoured hull section with a patched breach
    hull = s.lin(0, 120, 0, 215, [(0, "#d5e0ee"), (0.35, "#7488a2"), (1, "#1a2234")])
    s.add(path("M30,214 L30,166 Q128,112 226,166 L226,214 Z", hull, "#05090f", 3.4))
    s.add(path("M30,196 Q128,142 226,196", "none", "#0a1220", 2.4, 0.6))
    s.add(path("M30,180 Q128,126 226,180", "none", "#f4f9ff", 1.6, 0.55))
    for x in range(44, 224, 20):
        y = hull_top(x) + 11
        s.add(circle(x, y, 3.0, "#26324a", 1, "#05090f", 1.2))
        s.add(circle(x - 0.8, y - 0.8, 1.1, "#e8f2ff", 0.9))
    # breach: still glowing on the right, welded shut on the left
    breach = [(150, 150), (160, 143), (172, 148), (184, 142), (194, 152), (189, 166), (197, 178), (180, 184),
              (169, 177), (157, 186), (148, 174), (154, 163)]
    s.glow(poly(breach, "#ff7a2a"), 4, 0.85)
    g = s.rad(172, 164, 28, [(0, "#ffb050"), (0.55, "#6a1c08"), (1, "#14060a")])
    s.add(poly(breach, g, "#ff8a3a", 2.2))
    for a, b in (((156, 150), (150, 140)), ((190, 150), (200, 142)), ((196, 180), (206, 188))):
        s.add(line(a[0], a[1], b[0], b[1], "#0a0808", 3.4, 0.8))
    # welded patch plate over the left half, with a glowing green seam
    plate = [(146, 166), (158, 147), (172, 150), (176, 168), (170, 184), (150, 182)]
    pg = s.lin(146, 150, 176, 184, [(0, "#f2fbff"), (0.5, "#8aa4c0"), (1, "#3a4c68")])
    s.add(poly(plate, pg, "#05090f", 2.4))
    s.add(line(150, 168, 172, 156, "#ffffff", 1.6, 0.8))
    for p in ((152, 172), (166, 176), (162, 156)):
        s.add(circle(p[0], p[1], 2.1, "#1a2840", 1, "#05090f", 1))
        s.add(circle(p[0] - 0.6, p[1] - 0.6, 0.8, "#dff1ff"))
    seam = [(172, 150), (176, 168), (170, 184)]
    s.glow(polyline(seam, "#4cffa8", 9), 3, 0.95)
    s.add(polyline(seam, "#d8ffe8", 3.4))
    # repair drone
    for ex in (-9, 9):
        fl = s.lin(0, 0, 0, 24, [(0, "#ffffff", 1), (0.35, "#8fe8ff", 0.85), (1, "#2a9cff", 0)])
        s.add(f'<g transform="translate({80 + ex} 96)" filter="{s.blur(2)}"><polygon points="-5,0 0,22 5,0" fill="{fl}"/></g>')
    body = s.rad(76, 66, 30, [(0, "#ffffff"), (0.45, "#b6c6da"), (1, "#3c4c66")], fx=68, fy=56)
    s.add(ellipse(56, 78, 11, 7, "#34445e", 1, "#05090f", 2, rot=-30))
    s.add(ellipse(104, 78, 11, 7, "#34445e", 1, "#05090f", 2, rot=30))
    s.add(circle(80, 74, 25, body, 1, "#05090f", 3))
    s.add(path("M58,66 A24,24 0 0 1 102,66", "none", "#f4faff", 2.2, 0.8))
    lens = s.rad(80, 74, 12, [(0, "#ffffff"), (0.35, "#7dffc4"), (1, "#0a6a50")])
    s.add(circle(80, 74, 13, "#0a1220", 1, "#05090f", 2))
    s.add(circle(80, 74, 10.5, lens))
    s.add(circle(76, 70, 3, "#ffffff", 0.95))
    s.add(line(92, 52, 100, 38, "#9db2cc", 2.6))
    s.add(circle(101, 36, 3.2, "#7dffc4"))
    # two-joint arm to the weld point
    for (a, b, w) in (((98, 86), (124, 112), 8.5), ((124, 112), (152, 130), 7.5)):
        s.add(line(a[0], a[1], b[0], b[1], "#05090f", w + 3.4))
        ag = s.lin(a[0], a[1], b[0], b[1], [(0, "#f2f8ff"), (0.5, "#8296b2"), (1, "#3a4a66")])
        s.add(line(a[0], a[1], b[0], b[1], ag, w))
        s.add(line(a[0] - 1, a[1] - 2.4, b[0] - 1, b[1] - 2.4, "#ffffff", 1.6, 0.8))
    s.add(circle(124, 112, 7.5, "#46587a", 1, "#05090f", 2.4))
    s.add(circle(124, 112, 3, "#ffd870"))
    s.add(poly(rect_line(152, 130, 160, 140, 11), "#5a6c88", "#05090f", 2.2))
    s.add(poly([(156, 138), (166, 142), (162, 150)], "#ffd870", "#05090f", 1.6))
    # welding flare and sparks
    flare(s, 170, 156, 24 + 2 * rank, c1="#d8ffe8", c2="#2affa0")
    starburst(s, 170, 156, 14 + 2 * rank, 3.6, rot=15, color="#ffffff", glowc="#8affc8", gl=3)
    s.add(circle(170, 156, 4.2, "#ffffff"))
    sparks(s, 170, 156, [(-80, 14, 30), (-50, 12, 28), (-110, 12, 26), (-20, 12, 24), (-140, 12, 24)][: 2 + rank], "#ffe9a0", 2.6)
    # healing nanites drifting up from the seam
    rnd = random.Random(4100 + rank)
    for _ in range(3 + 3 * rank):
        x, y = rnd.uniform(150, 206), rnd.uniform(110, 146)
        r = rnd.choice([1.8, 2.4, 3.0])
        s.glow(circle(x, y, r * 1.8, "#4cffa8"), 1.8, 0.9)
        s.add(circle(x, y, r, "#d8ffe8"))
    for k in range(rank):
        s.add(ellipse(172, 160, 22 + 10 * k, 12 + 6 * k, "none", 0.75 - 0.15 * k, "#7dffc4", 2.2))
    return s


# ------------------------------------------------------------- family 2: Focus Fire
def focus_fire(rank):
    s = Svg()
    background(s, 4202, nebula=("#8a6a1a", "#1a4a8a"))
    tx, ty = 128, 92
    n = rank + 2
    xs = {3: [60, 128, 196], 4: [50, 104, 152, 206], 5: [42, 85, 128, 171, 214]}[n]
    gold = dict(glow="#ffb020", mid="#ffd86a", core="#fffbe8")
    # fleet escorts below, each with a beam converging on the marked target
    for i, x in enumerate(xs):
        y = 196 + abs(x - 128) * 0.05
        bolt(s, x, y - 36, tx + (x - 128) * 0.12, ty + 14, 3.6, gscale=2.4, **gold)
        escort(s, x, y, 0.34, rot=(x - 128) * -0.07, flame=0.9)
    # hostile target
    hostile_escort(s, tx, ty + 2, 0.34, rot=180, flame=0.0)
    flare(s, tx, ty + 6, 24 + 3 * rank, c1="#fff0c0", c2="#ffb030")
    starburst(s, tx, ty + 6, 18 + 3 * rank, 4.4, rot=0, color="#ffffff", glowc="#ffd870", gl=4)
    s.add(circle(tx, ty + 6, 5, "#ffffff"))
    # target reticle with brackets
    ring = "#ffd870"
    s.glow(circle(tx, ty, 46, "none", 1, ring, 6), 3, 0.85)
    s.add(circle(tx, ty, 46, "none", 1, "#2a1a04", 6.2))
    s.add(circle(tx, ty, 46, "none", 1, ring, 3.2))
    s.add(circle(tx, ty, 30, "none", 0.85, ring, 2))
    for a in range(0, 360, 90):
        ra = math.radians(a)
        s.add(line(tx + 36 * math.cos(ra), ty + 36 * math.sin(ra), tx + 56 * math.cos(ra), ty + 56 * math.sin(ra), "#2a1a04", 6))
        s.add(line(tx + 36 * math.cos(ra), ty + 36 * math.sin(ra), tx + 56 * math.cos(ra), ty + 56 * math.sin(ra), ring, 3))
    for a in range(45, 360, 90):
        ra = math.radians(a)
        s.add(circle(tx + 46 * math.cos(ra), ty + 46 * math.sin(ra), 3.4, "#fffbe8", 1, "#2a1a04", 1.4))
    return s


# ------------------------------------------------------------- family 3: Break Formation
def break_formation(rank):
    s = Svg()
    background(s, 4303, nebula=("#8a1a2a", "#3a1a6a"))
    cx, cy = 128, 124
    # the formation's broken ring
    for a0, a1 in ((-80, -30), (-10, 38), (58, 106), (128, 176), (196, 250)):
        s.glow(path(arc_d(cx, cy, 70, a0, a1), "none", "#ff4a3a", 6), 3, 0.8)
        s.add(path(arc_d(cx, cy, 70, a0, a1), "none", "#2a0808", 8))
        s.add(path(arc_d(cx, cy, 70, a0, a1), "none", "#ff7a5a", 3.2))
    # fractures out from the centre
    rnd = random.Random(4300 + rank)
    n_cracks = 5 + 2 * (rank - 1)
    for k in range(n_cracks):
        a = math.radians(-90 + k * 360 / n_cracks + 14)
        pts_ = [(cx, cy)]
        r = 0
        while r < 64:
            r += rnd.uniform(11, 16)
            aa = a + rnd.uniform(-0.2, 0.2)
            pts_.append((cx + r * math.cos(aa), cy + r * math.sin(aa)))
        crack(s, pts_, 2.6)
    # scattered hostile ships
    ships = [(-90, 62, -14), (-22, 76, 38), (50, 70, 120), (128, 76, 196), (200, 66, 262)]
    for a, d, tilt in ships:
        ra = math.radians(a)
        hostile_escort(s, cx + d * math.cos(ra) * 1.04, cy + d * math.sin(ra), 0.3, rot=tilt, flame=0.7)
    # broken accuracy reticle and shock burst at the centre
    flare(s, cx, cy, 22 + 2 * rank, c1="#ffd0b0", c2="#ff3a2a")
    starburst(s, cx, cy, 15 + 3 * rank, 4, rot=22, color="#ffffff", glowc="#ff8a6a", gl=3)
    s.add(circle(cx, cy, 4.2, "#ffffff"))
    for a0, a1 in ((20, 70), (110, 160), (200, 250), (290, 340)):
        s.add(path(arc_d(cx, cy, 26, a0, a1), "none", "#2a0808", 5))
        s.add(path(arc_d(cx, cy, 26, a0, a1), "none", "#ffb0a0", 2.2))
    if rank >= 2:
        # Weakened: drooping chevrons under the burst
        for k, yy in enumerate((178, 192)):
            s.glow(polyline([(cx - 18, yy - 8), (cx, yy + 4), (cx + 18, yy - 8)], "#ff4a3a", 9), 2.5, 0.8)
            s.add(polyline([(cx - 18, yy - 8), (cx, yy + 4), (cx + 18, yy - 8)], "#2a0808", 9))
            s.add(polyline([(cx - 18, yy - 8), (cx, yy + 4), (cx + 18, yy - 8)], "#ff8a6a", 4.2, 0.95 - 0.2 * k))
    return s


# ------------------------------------------------------------- family 4: Formation
def formation():
    s = Svg()
    background(s, 4404, nebula=("#0e7a6a", "#1a3a8a"))
    cx, cy = 128, 128
    # protective aura: soft disc, two rings, nodes
    s.add(circle(cx, cy, 92, s.rad(cx, cy, 92, [(0.55, "#4cf0c8", 0.0), (0.88, "#4cf0c8", 0.28), (1, "#4cf0c8", 0)])))
    for R, w, op in ((86, 5, 0.95),):
        s.glow(circle(cx, cy, R, "none", 1, "#4cf0c8", w + 4), 3, 0.8)
        s.add(circle(cx, cy, R, "none", 1, "#06201e", w + 2.4))
        s.add(circle(cx, cy, R, "none", op, "#aaffe8", w))
    for a in range(0, 360, 60):
        ra = math.radians(a + 30)
        x, y = cx + 84 * math.cos(ra), cy + 84 * math.sin(ra)
        s.glow(circle(x, y, 8, "#7dffd8"), 3, 0.9)
        s.add(circle(x, y, 5.4, "#eafff8", 1, "#06201e", 1.6))
    # formation links
    pos = [(128, 76), (90, 118), (166, 118), (54, 160), (202, 160)]
    for a, b in ((0, 1), (0, 2), (1, 3), (2, 4), (1, 2)):
        s.glow(line(pos[a][0], pos[a][1], pos[b][0], pos[b][1], "#7dffd8", 4), 2, 0.8)
        s.add(line(pos[a][0], pos[a][1], pos[b][0], pos[b][1], "#d8fff4", 1.6, 0.9))
    # the wedge of escorts, the leader largest and gold-trimmed
    for k in (3, 4, 1, 2, 0):
        x, y = pos[k]
        sc = 0.5 if k == 0 else 0.42 if k in (1, 2) else 0.36
        escort(s, x, y + 6, sc, flame=0.8, trim="#ffd870" if k == 0 else "#7dffd8", glow="#7fe8ff")
    return s


# ------------------------------------------------------------- family 5: Fleet Action
def fleet_action():
    s = Svg()
    background(s, 4505, nebula=("#8a6a1a", "#6a2a8a"))
    cx, cy = 128, 150
    # sunburst of command rays
    for i in range(18):
        a = math.radians(-180 + i * 10 + 5)
        w = math.radians(3.6)
        r = 98
        p = [(cx, cy), (cx + r * math.cos(a - w), cy + r * math.sin(a - w)), (cx + r * math.cos(a + w), cy + r * math.sin(a + w))]
        g = s.lin(cx, cy, cx + r * math.cos(a), cy + r * math.sin(a), [(0, "#ffe9a0", 0.0), (0.4, "#ffd060", 0.5), (1, "#ffb020", 0.85)])
        s.add(poly(p, g))
    # escorts streaking out in an arc, each trailing a wake
    for ang in (-160, -128, -52, -20):
        ra = math.radians(ang)
        x, y = cx + 74 * math.cos(ra), cy + 74 * math.sin(ra) * 0.98
        wake = s.lin(cx, cy, x, y, [(0, "#7fe8ff", 0.0), (1, "#7fe8ff", 0.65)])
        s.add(line(cx + 30 * math.cos(ra), cy + 30 * math.sin(ra), x - 14 * math.cos(ra), y - 14 * math.sin(ra), wake, 3))
        escort(s, x, y, 0.27, rot=ang + 90, flame=0.7, trim="#ffd870")
    # command beacon base
    base = s.rad(128, 206, 26, [(0, "#ffffff"), (0.4, "#b6c6da"), (1, "#3c4c66")], fx=120, fy=198)
    s.add(ellipse(128, 210, 30, 11, "#0a1220", 1, "#05090f", 2))
    s.add(ellipse(128, 206, 24, 9, base, 1, "#05090f", 2.4))
    s.add(ellipse(128, 206, 14, 5, "#ffd870", 0.95))
    # standard pole, pennant and finial
    s.add(line(128, 206, 128, 62, "#05090f", 8))
    pg = s.lin(124, 0, 132, 0, [(0, "#f2f8ff"), (0.5, "#8296b2"), (1, "#3a4a66")])
    s.add(line(128, 204, 128, 64, pg, 4.6))
    pen = [(128, 74), (182, 88), (160, 96), (184, 108), (128, 116)]
    pgd = s.lin(128, 74, 184, 116, [(0, "#fff0b0"), (0.5, "#f2b83a"), (1, "#8a5410")])
    s.glow(poly(pen, "#ffc94a"), 4, 0.85)
    s.add(poly(pen, pgd, "#2a1804", 3))
    s.add(line(134, 82, 170, 91, "#fff8d8", 2, 0.9))
    s.add(poly(star4(148, 98, 9, 3.4, 0), "#ffffff", "#7a4a0c", 1.2))
    s.glow(circle(128, 58, 12, "#ffe08a"), 4, 0.95)
    s.add(poly(star4(128, 58, 16, 5, 0), "#ffffff", "#7a4a0c", 1.4))
    flare(s, 128, 58, 20, c1="#fff0c0", c2="#ffb030")
    s.add(circle(128, 58, 4.4, "#ffffff"))
    return s



# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 5):
    JOBS.append((f"ife_cmd_rep{r}", "rep", lambda r=r: emergency_repair(r)))
for r in range(1, 4):
    JOBS.append((f"ife_cmd_foc{r}", "foc", lambda r=r: focus_fire(r)))
for r in range(1, 3):
    JOBS.append((f"ife_cmd_brk{r}", "brk", lambda r=r: break_formation(r)))
JOBS.append(("ife_cmd_form", "form", formation))
JOBS.append(("ife_cmd_fleet", "fleet", fleet_action))


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
        if resref.endswith("1") or fam in ("form", "fleet") or fam == "rep" and resref.endswith("4"):
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
    rows = [["ife_cmd_rep%d" % i for i in range(1, 5)], ["ife_cmd_foc%d" % i for i in range(1, 4)],
            ["ife_cmd_brk1", "ife_cmd_brk2"], ["ife_cmd_form", "ife_cmd_fleet"]]
    cell, pad = 128, 8
    W = pad + 4 * (cell + pad)
    H = pad + len(rows) * (cell + pad)
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    for ri, row in enumerate(rows):
        for ci, rr in enumerate(row):
            sheet.paste(finals[rr].resize((cell, cell), Image.NEAREST), (pad + ci * (cell + pad), pad + ri * (cell + pad)))
    sheet.save(HERE + "/contact_center.png")
    # 512 previews montage of rank-1 icons
    names = ["ife_cmd_rep1", "ife_cmd_foc1", "ife_cmd_brk1", "ife_cmd_form", "ife_cmd_fleet", "ife_cmd_rep4"]
    mont = Image.new("RGB", (512 * 3, 512 * 2), (128, 128, 128))
    for i, f in enumerate(names):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB"), ((i % 3) * 512, (i // 3) * 512))
    mont.save(HERE + "/previews_512.png")
    print("ok", len(finals), "icons")


if __name__ == "__main__":
    main()
