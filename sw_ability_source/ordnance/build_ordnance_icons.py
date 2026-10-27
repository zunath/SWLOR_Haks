"""Ordnance perk line ability-icon CENTER ARTWORK generator (same pipeline as the Ace line).

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



# --------------------------------------------------------------- ordnance art
def missile(s, cx, cy, ang, sc=1.0, flame=1.0, trail=0.0):
    """A guided missile, nose toward local -y (about -64..+60). ang rotates it clockwise in degrees."""
    body = s.lin(-9, 0, 9, 0, [(0, "#2a3446"), (0.28, "#e4edf8"), (0.62, "#8798b2"), (1, "#1c2433")])
    nose = s.lin(-9, 0, 9, 0, [(0, "#7a1410"), (0.3, "#ff7a50"), (0.65, "#d02a1c"), (1, "#4a0a0a")])
    fin = s.lin(0, 20, 0, 58, [(0, "#8da0bc"), (1, "#2a3446")])
    fl = s.lin(0, 46, 0, 46 + 54 * flame, [(0, "#ffffff", 1), (0.25, "#ffe08a", 1), (0.6, "#ff7a1c", 0.85), (1, "#ff3a10", 0)])
    flg = s.lin(0, 46, 0, 46 + 74 * flame, [(0, "#ffb060", 0.9), (1, "#ff3a10", 0)])
    o = []
    if trail > 0:
        tg = s.lin(0, 46, 0, 46 + trail, [(0, "#e8eef8", 0.55), (1, "#8090a8", 0)])
        o.append(f'<rect x="-6" y="46" width="12" height="{trail}" fill="{tg}"/>')
    # exhaust flame and glow
    o.append(f'<g filter="{s.blur(4)}" opacity="0.9"><polygon points="-9,46 0,{46 + 74 * flame:.1f} 9,46" fill="{flg}"/></g>')
    o.append(f'<polygon points="-6,46 0,{46 + 54 * flame:.1f} 6,46" fill="{fl}"/>')
    # fins
    for sx in (-1, 1):
        o.append(poly([(sx * 8, 14), (sx * 24, 46), (sx * 24, 58), (sx * 8, 44)], fin, "#06080e", 2.4))
        o.append(line(sx * 12, 20, sx * 22, 46, "#ff5a3c", 1.6, 0.9))
    # body
    o.append(f'<rect x="-9" y="-36" width="18" height="84" rx="3" fill="{body}" stroke="#06080e" stroke-width="2.6"/>')
    # hazard band (yellow / black) and a red band
    for i in range(4):
        o.append(poly([(-9 + i * 5, -2), (-4 + i * 5, -2), (-9 + i * 5 + 2.5, 10), (-9 + i * 5 - 2.5, 10)], "#f5c518", op=0.95))
    o.append('<rect x="-9" y="-2" width="18" height="2.6" fill="#06080e" opacity="0.8"/>')
    o.append('<rect x="-9" y="10" width="18" height="2.6" fill="#06080e" opacity="0.8"/>')
    o.append('<rect x="-9" y="26" width="18" height="5" fill="#c4281c"/>')
    o.append('<rect x="-5" y="-30" width="3" height="52" fill="#ffffff" opacity="0.75"/>')
    # nose cone
    o.append(path("M-9,-36 C-9,-52 -3,-62 0,-66 C3,-62 9,-52 9,-36 Z", nose, "#06080e", 2.6))
    o.append(path("M-5,-40 C-5,-50 -2,-57 -1,-60", "none", "#ffe0d0", 2.4, 0.85))
    # nozzle
    o.append('<rect x="-7" y="46" width="14" height="6" rx="1.5" fill="#1a1f2a" stroke="#06080e" stroke-width="1.6"/>')
    s.add(f'<g transform="translate({cx:.2f} {cy:.2f}) rotate({ang:.2f}) scale({sc:.3f})">{"".join(o)}</g>')


def smoke(s, x1, y1, x2, y2, w, op=0.5):
    g = s.lin(x1, y1, x2, y2, [(0, "#dfe6f2", op), (0.55, "#8a96ac", op * 0.6), (1, "#5a647a", 0)])
    s.add(f'<g filter="{s.blur(3.5)}">{line(x1, y1, x2, y2, g, w)}</g>')


def fireball(s, cx, cy, R, seed=1, hot=True):
    rnd = random.Random(seed)
    # smoke puffs behind
    for _ in range(7):
        a = rnd.uniform(0, 2 * math.pi)
        r = R * rnd.uniform(0.7, 1.0)
        s.add(f'<g filter="{s.blur(5)}">{circle(cx + r * math.cos(a), cy + r * math.sin(a), R * rnd.uniform(0.28, 0.42), "#3a2a2a", 0.55)}</g>')
    outer = []
    n = 14
    for i in range(n):
        a = i * 2 * math.pi / n
        rr = R * (1.0 if i % 2 == 0 else 0.68) * rnd.uniform(0.9, 1.08)
        outer.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    g1 = s.rad(cx, cy, R, [(0, "#fff6d8"), (0.35, "#ffb02a"), (0.7, "#ff5a14"), (1, "#a8200a")])
    s.glow(poly(outer, "#ff7a2a"), 5, 0.9)
    s.add(poly(outer, g1, "#5a0e06", 2.5))
    inner = []
    for i in range(10):
        a = i * 2 * math.pi / 10 + 0.3
        rr = R * (0.62 if i % 2 == 0 else 0.4) * rnd.uniform(0.92, 1.06)
        inner.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    g2 = s.rad(cx, cy, R * 0.6, [(0, "#ffffff"), (0.5, "#ffe08a"), (1, "#ff9a2a")])
    s.add(poly(inner, g2))
    s.add(circle(cx, cy, R * 0.22, "#ffffff", 0.95))
    # debris
    for _ in range(6):
        a = rnd.uniform(0, 2 * math.pi)
        r0, r1 = R * 1.05, R * rnd.uniform(1.25, 1.5)
        s.add(line(cx + r0 * math.cos(a), cy + r0 * math.sin(a), cx + r1 * math.cos(a), cy + r1 * math.sin(a), "#ffd28a", 3.2, 0.95))


def tube_front(s, cx, cy, r, warhead=True):
    """A missile launch tube seen end-on, with a warhead nose inside."""
    ring = s.rad(cx, cy, r, [(0, "#05070c"), (0.7, "#10151f"), (0.86, "#3a475c"), (1, "#a9b9d0")])
    s.add(circle(cx, cy, r, ring, 1, "#06080e", 2.4))
    if warhead:
        w = s.rad(cx - r * 0.2, cy - r * 0.25, r * 0.7, [(0, "#ffe0d0"), (0.3, "#ff6a44"), (0.75, "#b01c14"), (1, "#3a0808")])
        s.add(circle(cx, cy, r * 0.62, w, 1, "#06080e", 1.6))
        s.add(circle(cx - r * 0.22, cy - r * 0.26, r * 0.14, "#ffffff", 0.9))


# ------------------------------------------------------------- family 1
def volley(rank):
    s = Svg()
    background(s, 611, nebula=("#8a3a1a", "#1a4a7a"))
    fx, fy = 164, 162
    ux, uy = 0.62, 0.78  # flight direction (down-right)
    ang = math.degrees(math.atan2(ux, -uy))
    count = rank + 1
    offs = {2: [-22, 22], 3: [-34, 0, 34], 4: [-42, -14, 14, 42], 5: [-48, -24, 0, 24, 48]}[count]
    pxn, pyn = -uy, ux
    # smoke trails and missiles, farthest first
    items = []
    for i, off in enumerate(offs):
        back = 6 + 12 * ((i * 2) % 3)
        nx = fx - ux * (60 + back) + pxn * off * 0.9 + ux * 0
        ny = fy - uy * (60 + back) + pyn * off * 0.9
        items.append((nx, ny, off))
    for nx, ny, off in sorted(items, key=lambda t: -abs(t[2])):
        smoke(s, nx - ux * 20, ny - uy * 20, nx - ux * 66, ny - uy * 66, 12, 0.5)
    fireball(s, fx, fy, 40 + 3 * rank, seed=rank)
    for nx, ny, off in sorted(items, key=lambda t: -abs(t[2])):
        missile(s, nx, ny, ang, 0.46, flame=0.9)
    sp = [(-132, 30, 52), (-166, 32, 54), (-100, 34, 52), (-180, 30, 50), (-80, 36, 54), (-150, 40, 56)]
    for a, r0, r1 in sp[:2 + rank]:
        ra = math.radians(a)
        s.add(line(fx + (r0 + 20) * math.cos(ra), fy + (r0 + 20) * math.sin(ra),
                   fx + (r1 + 22) * math.cos(ra), fy + (r1 + 22) * math.sin(ra), "#ffe2b0", 3, 0.9))
    return s


# ------------------------------------------------------------- family 2
def flare_missile(rank):
    s = Svg()
    background(s, 712, nebula=("#4a2a8a", "#1a6a8a"))
    cx, cy = 168, 92  # flare centre, at the missile's nose
    ux, uy = 0.78, -0.62
    ang = math.degrees(math.atan2(ux, -uy))
    smoke(s, 60, 190, 112, 150, 14, 0.5)
    missile(s, 112, 150, ang, 0.62, flame=1.0)
    # flare rays and halo
    s.add(circle(cx, cy, 62, s.rad(cx, cy, 62, [(0, "#ffffff", 0.9), (0.35, "#fff2c0", 0.55), (0.7, "#a8d0ff", 0.22), (1, "#a8d0ff", 0)])))
    for a, L, w in [(0, 66, 7), (90, 60, 7), (45, 50, 5), (135, 50, 5), (22, 40, 3.4), (112, 40, 3.4), (68, 40, 3.4), (158, 40, 3.4)][:4 + 2 * rank]:
        for sg in (-1, 1):
            ra = math.radians(a)
            x2, y2 = cx + sg * L * math.cos(ra), cy + sg * L * math.sin(ra)
            g = s.lin(cx, cy, x2, y2, [(0, "#ffffff", 1), (0.6, "#fff0b8", 0.9), (1, "#fff0b8", 0)])
            s.add(line(cx, cy, x2, y2, g, w))
    starburst(s, cx, cy, 36 + 4 * rank, 7, rot=0, color="#ffffff", glowc="#fff2b0", gl=5)
    starburst(s, cx, cy, 22, 6, rot=45, color="#ffffff", glowc="#cfe6ff", gl=3)
    s.add(circle(cx, cy, 11, "#ffffff"))
    # ghost discs of the lens flare, off the flare toward the dark side
    s.add(circle(cx, cy, 50, "none", 0.7, "#cfe6ff", 2))
    for x, y, r, c in [(138, 68, 8, "#8fd0ff"), (118, 54, 5, "#ffd28a"), (102, 46, 3.4, "#a0e0ff")][:1 + rank]:
        s.glow(circle(x, y, r * 1.4, c), 2.5, 0.8)
        s.add(circle(x, y, r, c, 0.7, "#ffffff", 1.4))
    return s


# ------------------------------------------------------------- family 3
def concussion(rank):
    e = rank - 1
    s = Svg()
    background(s, 813, nebula=("#3a2a8a", "#1a6a9a"))
    cx = cy = 128
    s.add(circle(cx, cy, 92, s.rad(cx, cy, 92, [(0, "#9fd8ff", 0.0), (0.6, "#6fb0ff", 0.22), (0.85, "#4a8aff", 0.34), (1, "#2a5ae0", 0)])))
    # concussive shock rings, double and wavy
    radii = [(28, 5), (50, 7), (72, 8.5)] + ([(88, 5)] if rank >= 2 else [])
    for r, w in radii:
        rg = s.lin(40, 40, 216, 216, [(0, "#f2fbff"), (0.5, "#6fc0ff"), (1, "#d6f0ff")])
        s.glow(circle(cx, cy, r, "none", 1, "#4aa8ff", w + 4), 3.5, 0.8)
        s.add(circle(cx, cy, r, "none", 1, "#06101f", w + 3))
        s.add(circle(cx, cy, r, "none", 1, rg, w))
    # eight missiles bursting outward
    for i in range(8):
        a = i * 45 + 22.5
        ra = math.radians(a)
        d = 58 + 6 * e
        mx, my = cx + d * math.cos(ra), cy + d * math.sin(ra)
        smoke(s, cx + 24 * math.cos(ra), cy + 24 * math.sin(ra), mx, my, 9, 0.5)
        missile(s, mx, my, a + 90, 0.30, flame=0.7)
    # central blast core and dizzy stars
    flare(s, cx, cy, 30, c1="#bfe6ff", c2="#3a8aff")
    starburst(s, cx, cy, 20, 6, rot=22, color="#ffffff", glowc="#9fd8ff", gl=4)
    for k, a in enumerate((20, 140, 260)):
        ra = math.radians(a)
        sx_, sy_ = cx + 38 * math.cos(ra), cy + 38 * math.sin(ra)
        s.glow(poly(star4(sx_, sy_, 9, 3), "#ffd45a"), 2.5, 0.9)
        s.add(poly(star4(sx_, sy_, 8, 2.6), "#fff1b0"))
    return s


# ------------------------------------------------------------- family 4
def ordnance_stance():
    s = Svg()
    background(s, 914, nebula=("#1a4a9a", "#9a5a1a"))
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
    # three missiles on a steel launch rail, flames down
    base = s.lin(0, 176, 0, 200, [(0, "#d4e0f0"), (0.4, "#7a8ca8"), (1, "#1c2536")])
    s.add(poly([(62, 178), (194, 178), (204, 196), (52, 196)], base, "#04101f", 3.5))
    s.add(line(64, 181, 192, 181, "#ffffff", 2.2, 0.8))
    for i in range(9):
        x0 = 58 + i * 15
        s.add(poly([(x0, 186), (x0 + 7, 186), (x0 + 3, 194), (x0 - 4, 194)], "#f5c518", op=0.9))
    for x, ang, sc, y in [(86, -10, 0.62, 140), (170, 10, 0.62, 140), (128, 0, 0.8, 128)]:
        missile(s, x, y, ang, sc, flame=0.55)
    s.add(poly([(112, 172), (144, 172), (148, 184), (108, 184)], "#3a475c", "#04101f", 2.4))
    cg = s.lin(0, 198, 0, 226, [(0, "#ffffff"), (0.5, "#b9d6ee"), (1, "#4f7aa8")])
    s.add(poly([(128, 202), (156, 218), (156, 227), (128, 211), (100, 227), (100, 218)], cg, "#04101f", 2.4))
    return s


# ------------------------------------------------------------- family 5
def saturation_strike():
    s = Svg()
    background(s, 1015, nebula=("#8a2a1a", "#5a2a7a"))
    rnd = random.Random(5)
    # fragmentation zones: glowing discs of jagged shards on the target field
    zones = [(88, 166, 30), (168, 166, 30), (128, 132, 28)]
    for zx, zy, zr in zones:
        s.add(circle(zx, zy, zr + 14, s.rad(zx, zy, zr + 14, [(0, "#ff8a30", 0.6), (0.65, "#ff5a14", 0.4), (1, "#ff3a10", 0)])))
        s.glow(circle(zx, zy, zr, "none", 1, "#ff7a2a", 5), 3, 0.9)
        s.add(circle(zx, zy, zr, "none", 1, "#3a0e06", 5))
        s.add(f'<circle cx="{zx}" cy="{zy}" r="{zr}" fill="none" stroke="#ffd28a" stroke-width="2.6" stroke-dasharray="7 5"/>')
        shard = []
        n = 16
        for k in range(n):
            a = k * 2 * math.pi / n + rnd.uniform(-0.06, 0.06)
            rr = zr * (rnd.uniform(0.82, 0.96) if k % 2 == 0 else rnd.uniform(0.22, 0.34))
            shard.append((zx + rr * math.cos(a), zy + rr * math.sin(a)))
        s.add(poly(shard, "#ffe9c0", "#7a2a0a", 1.4, 0.95))
        s.add(circle(zx, zy, 7, "#ffffff", 0.95))
    # missiles converging on the field
    shots = [(54, 70, 88, 166), (92, 56, 88, 166), (128, 50, 128, 132), (164, 56, 168, 166), (202, 70, 168, 166)]
    for mx, my, tx, ty in shots:
        dx, dy = tx - mx, ty - my
        L = math.hypot(dx, dy)
        ang = math.degrees(math.atan2(dx / L, -dy / L))
        smoke(s, mx - dx / L * 16, my - dy / L * 16, mx - dx / L * 40, my - dy / L * 40, 9, 0.5)
        missile(s, mx, my + 10, ang, 0.42, flame=0.8)
    flare(s, 128, 132, 26)
    starburst(s, 128, 132, 24, 5, rot=22, color="#fffbe8", glowc="#ffd45a", gl=3)
    return s


# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 5):
    JOBS.append((f"ife_ord_volley{r}", "volley", lambda r=r: volley(r)))
for r in range(1, 4):
    JOBS.append((f"ife_ord_flare{r}", "flare", lambda r=r: flare_missile(r)))
for r in range(1, 3):
    JOBS.append((f"ife_ord_conc{r}", "conc", lambda r=r: concussion(r)))
JOBS.append(("ife_ord_stance", "stance", ordnance_stance))
JOBS.append(("ife_ord_strike", "strike", saturation_strike))


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
        if resref.endswith("1") or fam in ("stance", "strike"):
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
    rows = [["ife_ord_volley%d" % i for i in range(1, 5)], ["ife_ord_flare%d" % i for i in range(1, 4)],
            ["ife_ord_conc1", "ife_ord_conc2"], ["ife_ord_stance"], ["ife_ord_strike"]]
    cell, pad = 256, 16
    W = pad + 4 * (cell + pad)
    H = pad + len(rows) * (cell + pad)
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    for ri, row in enumerate(rows):
        for ci, rr in enumerate(row):
            sheet.paste(finals[rr].resize((cell, cell), Image.NEAREST), (pad + ci * (cell + pad), pad + ri * (cell + pad)))
    sheet.save(HERE + "/contact_center.png")
    # previews montage (512 rank-1)
    fams = ["volley", "flare", "conc", "stance", "strike"]
    mont = Image.new("RGB", (512 * 3, 512 * 2), (128, 128, 128))
    for i, f in enumerate(fams):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB"), ((i % 3) * 512, (i // 3) * 512))
    mont.save(HERE + "/previews_512.png")
    # ring check on the 256 renders (content must not reach outer 3px = 24 units); final ring is flattened
    print("ok", len(finals), "icons")


if __name__ == "__main__":
    main()
