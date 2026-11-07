"""Ace perk line ability-icon CENTER ARTWORK generator.

Authors each icon as an illustrated SVG (src/<resref>.svg, 256x256 viewBox),
rasterizes it with ImageMagick (librsvg) at 256 px, downsamples to 32x32 with
LANCZOS, applies light edge definition, flattens the outer 3 px ring to plain
dark background (the project icon tool stamps the semantic frame there) and
writes out_png/<resref>.png, out_tga/<resref>.tga (32-bit, bottom-left origin,
descriptor 8), contact_center.png and 512 px previews.

Run:  python -I build_ace_icons.py
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


# ------------------------------------------------------------- family 1
def disabling_shot(rank):
    e = (rank - 1) / 3.0
    s = Svg()
    background(s, 101)
    cx0, cy0 = 154, 136  # dome centre
    # hull plate (enemy ship fragment) behind the turret
    plate = [(104, 160), (222, 104), (226, 224), (122, 226)]
    pg = s.lin(104, 100, 226, 226, [(0, "#8494ac"), (0.5, "#3c485c"), (1, "#18202e")])
    s.add(poly(plate, pg, "#06080e", 3.5))
    s.add(line(108, 162, 220, 108, "#a6b8d0", 2.6, 0.85))
    for p in [((126, 196), (224, 176)), ((172, 168), (176, 224))]:
        s.add(line(p[0][0], p[0][1], p[1][0], p[1][1], "#080b12", 2.6, 0.85))
    for rx, ry in [(136, 214), (160, 214), (190, 214), (208, 120), (212, 196)]:
        s.add(circle(rx, ry, 3.4, "#8a98ae"))
        s.add(circle(rx - 0.7, ry - 0.9, 1.3, "#e8f1ff"))
    # twin barrels pointing up-left: left one intact, right one snapped
    d = (-0.30, -0.954)
    nrm = (0.954, -0.30)

    def along(t, off):
        return (cx0 + d[0] * t + nrm[0] * off, cy0 + d[1] * t + nrm[1] * off)

    # intact barrel (left)
    a, b = along(20, -13), along(92, -13)
    s.add(poly(rect_line(a[0], a[1], b[0], b[1], 19), "#3d485c", "#06080e", 3.2))
    a2, b2 = along(24, -17), along(90, -17)
    s.add(poly(rect_line(a2[0], a2[1], b2[0], b2[1], 5.5), "#b9c8de", op=0.9))
    m1, m2 = along(76, -13), along(94, -13)
    s.add(poly(rect_line(m1[0], m1[1], m2[0], m2[1], 27), "#5a687e", "#06080e", 3.2))
    s.add(circle(b[0], b[1], 5, "#0b0f18"))
    # snapped barrel (right): short stub with ragged end and a hot break
    a, b = along(20, 13), along(52, 13)
    s.add(poly(rect_line(a[0], a[1], b[0], b[1], 19), "#343f52", "#06080e", 3.2))
    a2, b2 = along(24, 17), along(50, 17)
    s.add(poly(rect_line(a2[0], a2[1], b2[0], b2[1], 5), "#9aa9c0", op=0.85))
    rag = [along(50, 4), along(54, 8), along(60, 3), along(56, 14), along(62, 17), along(52, 22), along(46, 22)]
    s.add(poly(rag, "#2a3445", "#06080e", 2.6))
    s.glow(circle(*along(58, 13), 8, "#ff7a2a"), 3.5, 0.95)
    s.add(circle(*along(58, 13), 3.2, "#ffe2b0"))
    # dome
    s.add(ellipse(cx0, cy0 + 32, 52, 15, "#06080e", 0.6))
    dome = s.rad(cx0, cy0, 50, [(0, "#f0f6ff"), (0.3, "#aebfd6"), (0.7, "#4c5b74"), (1, "#18202e")],
                 fx=cx0 - 16, fy=cy0 - 22)
    s.add(circle(cx0, cy0, 46, dome, 1, "#05070c", 4))
    s.add(path(f"M{cx0 - 42},{cy0 + 6} Q{cx0},{cy0 + 24} {cx0 + 42},{cy0 + 6}", "none", "#0a0d14", 2.6, 0.7))
    s.add(path(f"M{cx0 - 28},{cy0 - 24} Q{cx0 - 8},{cy0 - 36} {cx0 + 14},{cy0 - 32}", "none", "#f4f9ff", 3.2, 0.75))
    # ion arcs around the dome
    ion = "#9ae8ff"
    arcs = [[(196, 104), (206, 116), (199, 128), (209, 140)],
            [(180, 186), (172, 176), (182, 168)]]
    if rank >= 2:
        arcs.append([(128, 110), (120, 122), (127, 132)])
    if rank >= 4:
        arcs.append([(200, 164), (192, 156), (200, 148)])
    for ar in arcs:
        s.glow(polyline(ar, "#3aa8ff", 6.5), 3.5)
        s.add(polyline(ar, ion, 2.8))
        s.add(polyline(ar, "#ffffff", 1.2, 0.9))
    # impact on the dome's lower-left shoulder + fracture
    ix, iy = 128, 156
    crack = [(ix, iy), (146, 134), (140, 116), (154, 100)]
    br1 = [(146, 134), (166, 132), (178, 118)]
    br2 = [(ix, iy), (156, 166), (158, 182)]
    for c in (crack, br1, br2):
        s.add(polyline(c, "#06080e", 6))
    for c in (crack, br1, br2):
        s.glow(polyline(c, "#ff8a30", 4.5), 2.5)
        s.add(polyline(c, "#ffe9b8", 2.1))
    # bolt + impact flare
    bolt(s, 38, 218, ix, iy, 15 + 2 * e)
    flare(s, ix, iy, 20 + 6 * e)
    starburst(s, ix, iy, 27 + 6 * e, 5, rot=20)
    ion_sparks = [(205, 22, 42), (238, 24, 42), (160, 22, 40), (265, 20, 38), (315, 24, 42), (-18, 28, 44),
                  (118, 20, 36)]
    n = 4 + rank
    sparks(s, ix, iy, ion_sparks[:n], "#bfeaff", 3.4)
    extra = [(86, 128, 3), (100, 176, 2.6), (60, 190, 2.4), (96, 96, 2.4)]
    for i in range(min(len(extra), rank)):
        x, y, r = extra[i]
        s.glow(circle(x, y, r * 1.8, "#ffd29a"), 2.5)
        s.add(circle(x, y, r, "#fff1d0"))
    return s


# ------------------------------------------------------------- family 2
def double_shot(rank):
    e = (rank - 1) / 2.0
    s = Svg()
    background(s, 202, nebula=("#6a2a7a", "#1a4a8a"))
    cy, sc = 160, 0.95
    xl, xr, y0, y1 = 86, 170, 66, 145
    # speed streaks
    for x, y, L, op in [(46, 150, 40, 0.4), (210, 150, 40, 0.4), (60, 100, 26, 0.3), (196, 100, 26, 0.3)]:
        s.add(line(x, y, x, y + L, "#7fb8ff", 2.4, op))
    for x in (xl, xr):
        bolt(s, x, y1, x, y0, 12 + 1.5 * e, glow="#ff3a12", mid="#ff7a30", core="#fff4d8")
    fighter(s, 128, cy, sc)
    for x in (xl, xr):
        fr = 25 + 5 * e
        flare(s, x, y0 - 2, fr)
        starburst(s, x, y0 - 2, 28 + 5 * e, 5.5, rot=0)
        sparks(s, x, y0 - 2, [(-30, 18, 34), (-150, 18, 34), (30, 18, 32), (150, 18, 32)][:2 + rank],
               "#ffd7a0", 2.8)
        s.glow(circle(x, y1 - 1, 9, "#ffb070"), 3, 0.9)
    # muzzle flashes already glow; extra tracer sparks per rank
    extra = [(128, 70, 2.8), (112, 110, 2.4), (146, 110, 2.4)]
    for i in range(rank):
        x, y, r = extra[i]
        s.glow(circle(x, y, r * 1.8, "#ff9a3c"), 2.5)
        s.add(circle(x, y, r, "#fff1d0"))
    return s


# ------------------------------------------------------------- family 3
def point_blank(rank):
    e = rank - 1
    s = Svg()
    background(s, 303, nebula=("#7a2a5a", "#1a5a7a"))
    cx = cy = 128
    RR = 64  # shock ring radius
    # blast glow, kept behind the ring so the fighter stays dark and readable
    s.add(circle(cx, cy, 96 + 3 * e, s.rad(cx, cy, 96 + 3 * e,
                                          [(0, "#ff8a40", 0.0), (0.55, "#ff6a2a", 0.28), (0.8, "#ff4a18", 0.35),
                                           (1, "#ff3a10", 0)])))
    s.add(circle(cx, cy, RR - 3, s.rad(cx, cy, RR, [(0, "#0e1c44", 0.95), (0.7, "#0a1534", 0.95),
                                                    (1, "#3a1a30", 0.9)])))
    rg = s.lin(40, 40, 216, 216, [(0, "#ffe9c0"), (0.5, "#ff8a40"), (1, "#ffd0a0")])
    s.glow(circle(cx, cy, RR, "none", 1, "#ff6a2a", 10), 3.5)
    s.add(circle(cx, cy, RR, "none", 1, "#3a1006", 10))
    s.add(circle(cx, cy, RR, "none", 1, rg, 7))
    s.add(circle(cx, cy, RR - 1.5, "none", 0.85, "#fff5e0", 1.8))
    # radial bolts
    for i in range(12):
        a = math.radians(i * 30 + 15)
        long_ = (i % 2 == 0)
        r0, r1 = (78, 98) if long_ else (80, 92)
        r1 += 1.2 * e
        x1, y1 = cx + r0 * math.cos(a), cy + r0 * math.sin(a)
        x2, y2 = cx + r1 * math.cos(a), cy + r1 * math.sin(a)
        bolt(s, x1, y1, x2, y2, 9.5 + 1.2 * e, gscale=2.2)
    if rank >= 2:
        for i in range(12):
            a = math.radians(i * 30)
            r0, r1 = 84, 94
            s.add(line(cx + r0 * math.cos(a), cy + r0 * math.sin(a), cx + r1 * math.cos(a),
                       cy + r1 * math.sin(a), "#ffe2b0", 3.4, 0.95))
    # fighter at the centre
    s.glow(circle(cx, cy, 30, "#7fd0ff"), 9, 0.55)
    fighter(s, cx, cy + 2, 0.52, cannons=False)
    s.glow(circle(cx, cy + 22, 10 + 3 * e, "#ffffff"), 5, 0.6)
    return s


# ------------------------------------------------------------- family 4
def ace_stance():
    s = Svg()
    background(s, 404, nebula=("#1a4a9a", "#1a8aaa"))
    cx = cy = 128
    s.add(circle(cx, cy, 86, s.rad(cx, cy, 86, [(0, "#0e2a58"), (0.7, "#081634"), (1, "#050c20")])))
    # stance ring
    rg = s.lin(40, 40, 216, 216, [(0, "#e6f6ff"), (0.35, "#4fc3ff"), (0.7, "#2a74c8"), (1, "#9fe0ff")])
    s.glow(circle(cx, cy, 90, "none", 1, "#4fc3ff", 9), 3.5, 0.9)
    s.add(circle(cx, cy, 90, "none", 1, "#04101f", 11))
    s.add(circle(cx, cy, 90, "none", 1, rg, 7.5))
    s.add(circle(cx, cy, 81.5, "none", 0.9, "#8fdcff", 1.6))
    for a in (0, 90, 180, 270):
        ra = math.radians(a)
        s.add(line(cx + 83 * math.cos(ra), cy + 83 * math.sin(ra), cx + 96 * math.cos(ra),
                   cy + 96 * math.sin(ra), "#eaf9ff", 5.5))
    # helmet
    hg = s.lin(84, 46, 172, 170, [(0, "#d0e2f6"), (0.35, "#6a86b0"), (1, "#18284a")])
    helm = ("M128,44 C166,44 172,88 172,118 L170,148 C168,164 150,172 128,172 "
            "C106,172 88,164 86,148 L84,118 C84,88 90,44 128,44 Z")
    s.glow(path(helm, "#4fc3ff"), 5, 0.55)
    s.add(path(helm, hg, "#04101f", 3.5))
    s.add(path("M128,48 L128,92", "none", "#cfe4ff", 5, 0.8))
    s.add(path("M96,70 C100,56 112,50 120,49", "none", "#eaf4ff", 3, 0.9))
    s.add(ellipse(86, 122, 7, 12, "#1a2540", 1, "#04101f", 2))
    s.add(ellipse(170, 122, 7, 12, "#1a2540", 1, "#04101f", 2))
    visor = "M91,98 C104,90 152,90 165,98 L167,126 C150,140 106,140 89,126 Z"
    vg = s.lin(90, 92, 166, 138, [(0, "#1d4a78"), (0.5, "#08172c"), (1, "#03080f")])
    s.add(path(visor, vg, "#04101f", 3))
    s.add(path("M96,100 L120,96 L106,128 L94,124 Z", "#7fd6ff", op=0.5))
    s.add(path("M126,96 L136,95 L122,132 L114,132 Z", "#7fd6ff", op=0.28))
    s.add(path("M92,99 C104,92 152,92 164,99", "none", "#8fdcff", 2.2, 0.9))
    # winged chevron crest in front
    cg = s.lin(0, 120, 0, 190, [(0, "#ffffff"), (0.5, "#b9d6ee"), (1, "#4f7aa8")])
    wg = s.lin(130, 130, 215, 180, [(0, "#e4f4ff"), (0.6, "#6fb0e6"), (1, "#244e86")])
    for sx in (-1, 1):
        def X(x):
            return 128 + sx * (x - 128)
        feathers = [
            [(X(168), 150), (X(210), 122), (X(204), 140), (X(172), 164)],
            [(X(170), 164), (X(206), 144), (X(200), 158), (X(174), 176)],
            [(X(172), 176), (X(198), 162), (X(192), 174), (X(176), 186)],
        ]
        for f in feathers:
            s.glow(poly(f, "#4fc3ff"), 3, 0.7)
            s.add(poly(f, wg, "#04101f", 2.2))
        s.add(line(X(172), 152, X(206), 126, "#ffffff", 2, 0.85))
    chev1 = [(128, 120), (178, 158), (178, 180), (128, 142), (78, 180), (78, 158)]
    chev2 = [(128, 150), (166, 180), (166, 196), (128, 168), (90, 196), (90, 180)]
    s.glow(poly(chev1, "#4fc3ff"), 4, 0.8)
    s.add(poly(chev1, cg, "#04101f", 3.5))
    s.add(poly(chev2, cg, "#04101f", 3))
    s.add(path("M128,124 L174,158", "none", "#ffffff", 2, 0.8))
    s.add(path("M128,153 L162,180", "none", "#ffffff", 2, 0.7))
    s.glow(circle(128, 133, 9, "#9fe8ff"), 3)
    s.add(poly(star4(128, 133, 8, 2.6), "#ffffff"))
    return s


# ------------------------------------------------------------- family 5
def ace_in_the_hole():
    s = Svg()
    background(s, 505, nebula=("#8a2a3a", "#7a5a1a"))
    ang = -16
    spade = ("M0,-40 C8,-26 34,-10 34,8 C34,24 16,28 8,18 C10,28 12,34 18,40 L-18,40 "
             "C-12,34 -10,28 -8,18 C-16,28 -34,24 -34,8 C-34,-10 -8,-26 0,-40 Z")
    face = s.lin(-56, -80, 56, 80, [(0, "#ffffff"), (0.55, "#fbf0d4"), (1, "#d8c79c")])
    sp = s.lin(0, -40, 0, 40, [(0, "#ff5a5a"), (0.5, "#c01a2e"), (1, "#5a0a16")])
    card = []
    card.append(f'<rect x="-52" y="-76" width="112" height="160" rx="12" fill="#02040a" opacity="0.55"/>')
    card.append(f'<rect x="-56" y="-80" width="112" height="160" rx="12" fill="{face}" stroke="#2a1d08" '
                f'stroke-width="3.5"/>')
    card.append('<rect x="-47" y="-71" width="94" height="142" rx="7" fill="none" stroke="#e0a830" stroke-width="3"/>')
    card.append(f'<g transform="translate(0 -24) scale(0.74)">{path(spade, sp, "#3a0610", 3.5)}'
                f'{path("M-18,-18 C-24,-6 -26,4 -22,12", "none", "#ffd0d0", 3.5, 0.8)}</g>')
    # corner stars
    for sx, sy, rot in [(-34, -56, 0), (34, 56, 180)]:
        card.append(poly(star4(sx, sy, 10, 3.2, rot), "#e0a830", "#6a4a10", 1.5))
    # starfighter silhouette in the emblem space (below the spade)
    fcol = "#2a1d3a"
    fp = []
    for sx in (-1, 1):
        fp.append(poly([(sx * 3, -6), (sx * 31, 16), (sx * 26, 25), (sx * 3, 17)], fcol))
    fp.append(poly([(0, -22), (4, -4), (5, 16), (0, 12), (-5, 16), (-4, -4)], fcol))
    card.append(f'<g transform="translate(0 49) scale(0.8)">{"".join(fp)}</g>')
    s.add(f'<g transform="translate(128 128) rotate({ang})">{"".join(card)}</g>')
    # bolt through the card
    px, py = 133.5, 147.2
    dx, dy = math.cos(math.radians(-28)), math.sin(math.radians(-28))
    bolt(s, px - 100 * dx, py - 100 * dy, px + 98 * dx, py + 98 * dy, 12, glow="#ff3a12", mid="#ff7a30", gscale=1.4)
    # scorched hole + critical sparkle
    s.add(circle(px, py, 12, "#14060a", 1, "#ff8a30", 2.5))
    flare(s, px, py, 22, c1="#fff0b8", c2="#ffc040")
    starburst(s, px, py, 32, 5.5, rot=0, color="#fffbe8", glowc="#ffd45a", gl=4)
    starburst(s, px, py, 19, 4.5, rot=45, color="#fff2b8", glowc="#ffcf4a", gl=3)
    for (x, y, r) in [(188, 88, 3), (86, 196, 2.6), (196, 168, 2.4)]:
        s.glow(circle(x, y, r * 1.8, "#ffd45a"), 2.5)
        s.add(circle(x, y, r, "#fff8dc"))
    return s


# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 5):
    JOBS.append((f"ife_ace_disab{r}", "disabling", lambda r=r: disabling_shot(r)))
for r in range(1, 4):
    JOBS.append((f"ife_ace_dblsht{r}", "doubleshot", lambda r=r: double_shot(r)))
for r in range(1, 3):
    JOBS.append((f"ife_ace_ptblnk{r}", "pointblank", lambda r=r: point_blank(r)))
JOBS.append(("ife_ace_stance", "stance", ace_stance))
JOBS.append(("ife_ace_hole", "hole", ace_in_the_hole))


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
        if resref.endswith("1") or fam in ("stance", "hole"):
            op = f"{PREV}/{fam}_{resref}.svg"
            open(op, "w", encoding="utf-8").write(svg.render(512))
            magick_render(op, f"{PREV}/{fam}.png")
            os.remove(op)
    # uniqueness check across ranks
    keys = list(finals)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert list(finals[keys[i]].getdata()) != list(finals[keys[j]].getdata()), (keys[i], keys[j])
    # contact sheet
    rows = [["ife_ace_disab%d" % i for i in range(1, 5)], ["ife_ace_dblsht%d" % i for i in range(1, 4)],
            ["ife_ace_ptblnk1", "ife_ace_ptblnk2"], ["ife_ace_stance"], ["ife_ace_hole"]]
    cell, pad = 256, 16
    W = pad + 4 * (cell + pad)
    H = pad + len(rows) * (cell + pad)
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    for ri, row in enumerate(rows):
        for ci, rr in enumerate(row):
            sheet.paste(finals[rr].resize((cell, cell), Image.NEAREST), (pad + ci * (cell + pad), pad + ri * (cell + pad)))
    sheet.save(HERE + "/contact_center.png")
    # previews montage (512 rank-1)
    fams = ["disabling", "doubleshot", "pointblank", "stance", "hole"]
    mont = Image.new("RGB", (512 * 3, 512 * 2), (128, 128, 128))
    for i, f in enumerate(fams):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB"), ((i % 3) * 512, (i // 3) * 512))
    mont.save(HERE + "/previews_512.png")
    # ring check on the 256 renders (content must not reach outer 3px = 24 units); final ring is flattened
    print("ok", len(finals), "icons")


if __name__ == "__main__":
    main()
