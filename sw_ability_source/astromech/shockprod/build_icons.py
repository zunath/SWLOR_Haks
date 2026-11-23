"""Astromech Armaments - Shock Prod ability-icon CENTER ARTWORK generator.

Follows SWLOR_Haks/sw_ability_source/ace/build_ace_icons.py: each icon is an
illustrated SVG (src/<resref>.svg, 256x256 viewBox), rasterized with ImageMagick
at 256 px, downsampled to 32x32 with LANCZOS, given light edge definition, with
the outer 3 px ring flattened to the plain dark background (the project icon
tool stamps the semantic frame there). Writes out_png/<resref>.png,
out_tga/<resref>.tga (32-bit, bottom-left origin, descriptor 8),
status_png/<resref>.png (256x256 RGB status subjects), contact_center.png and
512 px previews.

Run:  python -I build_icons.py
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
STATUS_DIR = HERE + "/status_png"
for d in (SRC, RENDER, OUT_PNG, OUT_TGA, PREV, STATUS_DIR):
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


# ------------------------------------------------------------ shock prod art
CY_GLOW, CY_MID, CY_CORE = "#1f8cff", "#7fe6ff", "#ffffff"


def zig(x1, y1, x2, y2, n, amp, seed):
    """Jagged lightning polyline from (x1,y1) to (x2,y2)."""
    rnd = random.Random(seed)
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1
    nx, ny = -dy / L, dx / L
    p = [(x1, y1)]
    for i in range(1, n):
        t = i / n
        o = rnd.uniform(-amp, amp) * (1 if i % 2 else -1)
        p.append((x1 + dx * t + nx * o, y1 + dy * t + ny * o))
    p.append((x2, y2))
    return p


def arc(s, p, w, glow=CY_GLOW, mid=CY_MID, core=CY_CORE, gop=1.0):
    s.glow(polyline(p, glow, w * 2.6), w * 0.9, gop)
    s.add(polyline(p, mid, w))
    s.add(polyline(p, core, w * 0.42, 0.95))


def spark_dots(s, items, glowc="#7fe6ff", core="#ffffff"):
    for x, y, r in items:
        s.glow(circle(x, y, r * 1.9, glowc), 2.5)
        s.add(circle(x, y, r, core))


def prod(s, tx, ty, rot, sc, length=150, tipc="#ffd27a", glow=CY_GLOW):
    """Shock prod: two-pronged emitter fork on an insulated arm. Local frame: tip at (0,0),
    arm runs toward +y (rotate 0 = pointing up)."""
    steel = s.lin(-12, 0, 12, 0, [(0, "#1c2433"), (0.35, "#cfdcee"), (0.65, "#7b8ca6"), (1, "#1a2130")])
    prong = s.lin(-5, 0, 5, 0, [(0, "#2a3446"), (0.4, "#e2ecf8"), (1, "#3a4558")])
    head = s.lin(-24, 0, 24, 0, [(0, "#151b28"), (0.3, "#9fb0c8"), (0.6, "#56657e"), (1, "#121826")])
    tip = s.rad(0, 0, 9, [(0, "#ffffff"), (0.5, tipc), (1, "#8a4a10")])
    o = []
    # arm (insulated shaft)
    o.append(f'<rect x="-13" y="50" width="26" height="{length - 50}" fill="{steel}" stroke="#06080e" stroke-width="3.5"/>')
    for yy in range(70, int(length) - 10, 22):
        o.append(f'<rect x="-15" y="{yy}" width="30" height="9" rx="3" fill="#1a2232" stroke="#06080e" stroke-width="2"/>')
        o.append(line(-11, yy + 2.4, 11, yy + 2.4, "#5a6a84", 1.8, 0.9))
    o.append(line(-4, 56, -4, length - 6, "#eef5ff", 3, 0.6))
    # emitter head
    o.append(f'<rect x="-26" y="24" width="52" height="32" rx="7" fill="{head}" stroke="#06080e" stroke-width="3.5"/>')
    o.append(f'<rect x="-17" y="33" width="34" height="9" rx="3" fill="#0a1a30" stroke="#06080e" stroke-width="1"/>')
    o.append(f'<rect x="-15" y="35" width="30" height="5" rx="2" fill="{CY_MID}"/>')
    o.append(line(-21, 28, 21, 28, "#e8f2ff", 2.4, 0.8))
    # prongs
    for sx in (-1, 1):
        p = [(sx * 16 - 6, 26), (sx * 16 + 6, 26), (sx * 13 + 4, 4), (sx * 13 - 4, 4)]
        o.append(poly(p, prong, "#06080e", 3))
        o.append(circle(sx * 13, 3, 7, tip, 1, "#3a1a06", 2))
    s.add(f'<g transform="translate({tx} {ty}) rotate({rot}) scale({sc})">{"".join(o)}</g>')


def to_world(tx, ty, rot, sc, x, y):
    a = math.radians(rot)
    return (tx + sc * (x * math.cos(a) - y * math.sin(a)), ty + sc * (x * math.sin(a) + y * math.cos(a)))


# ------------------------------------------------------------- Shock Jab
def shock_jab(rank):
    e = (rank - 1) / 3.0
    s = Svg()
    background(s, 601, nebula=("#1a3a8a", "#1a6a8a"))
    tx, ty, rot, sc = 146, 110, 45, 1.2
    # thrust streaks behind the prod
    for off, op in [(-34, 0.35), (34, 0.35), (-20, 0.25)]:
        a = to_world(tx, ty, rot, sc, off, 80)
        b = to_world(tx, ty, rot, sc, off, 150)
        s.add(line(a[0], a[1], b[0], b[1], "#7fb8ff", 2.6, op))
    prod(s, tx, ty, rot, sc, length=150)
    l, r = to_world(tx, ty, rot, sc, -13, 3), to_world(tx, ty, rot, sc, 13, 3)
    arc(s, zig(l[0], l[1], r[0], r[1], 4, 5, 7), 3.4)
    # strike point ahead of the fork
    hx, hy = to_world(tx, ty, rot, sc, 0, -26)
    for i, (dx, dy) in enumerate([(-6, 3), (6, 3)]):
        src = to_world(tx, ty, rot, sc, dx * 2, 3)
        arc(s, zig(src[0], src[1], hx, hy, 3, 4, 11 + i), 3.0)
    s.add(circle(hx, hy, 34 + 10 * e, s.rad(hx, hy, 34 + 10 * e, [(0, "#ffffff", 1), (0.25, "#c8f4ff", 0.9),
                                                                    (0.6, CY_GLOW, 0.35 + 0.25 * e), (1, CY_GLOW, 0)])))
    starburst(s, hx, hy, 32 + 8 * e, 6, rot=45, color="#ffffff", glowc="#8fe6ff")
    starburst(s, hx, hy, 18 + 4 * e, 4, rot=0, color="#e8fbff", glowc="#5fc8ff", gl=3)
    # forked arcs off the strike, one more per rank
    forks = [(hx + 34, hy - 18, 21), (hx - 14, hy - 36, 22), (hx + 30, hy + 22, 23), (hx - 36, hy - 4, 24)]
    for i in range(rank):
        x2, y2, sd = forks[i]
        arc(s, zig(hx, hy, x2, y2, 3, 4, sd), 3.0)
    spark_dots(s, [(214, 56, 2.8), (186, 40, 2.4), (216, 120, 2.4), (140, 50, 2.2)][:rank])
    return s


# ------------------------------------------------------------- Stun Prod
def stun_prod(rank):
    e = (rank - 1) / 2.0
    s = Svg()
    background(s, 602, nebula=("#6a4a1a", "#1a4a7a"))
    tx, ty, rot, sc = 104, 112, -45, 1.15
    prod(s, tx, ty, rot, sc, length=150, tipc="#ffe27a")
    hx, hy = to_world(tx, ty, rot, sc, 0, -16)
    # stun flash: big golden 8-point flash
    s.add(circle(hx, hy, 52 + 4 * e, s.rad(hx, hy, 52 + 4 * e, [(0, "#ffffff", 1), (0.2, "#fff4c0", 0.95),
                                                                   (0.55, "#ffc23a", 0.45), (1, "#ff9a1a", 0)])))
    starburst(s, hx, hy, 50 + 4 * e, 7, rot=0, color="#fffbe6", glowc="#ffcc40", gl=5)
    starburst(s, hx, hy, 30 + 3 * e, 6, rot=45, color="#fff2b0", glowc="#ffb020", gl=3)
    # daze stars orbiting on a tilted ring
    ox, oy = 150, 64
    s.glow(ellipse(ox, oy, 46, 15, "none", 1, "#ffb020", 6, rot=-12), 3, 0.7)
    s.add(ellipse(ox, oy, 46, 15, "none", 0.9, "#ffe08a", 3.2, rot=-12))
    n = 2 + rank
    for i in range(n):
        a = math.radians(-100 + i * 360 / n)
        x = ox + 46 * math.cos(a) * math.cos(math.radians(-12)) - 15 * math.sin(a) * math.sin(math.radians(-12))
        y = oy + 46 * math.cos(a) * math.sin(math.radians(-12)) + 15 * math.sin(a) * math.cos(math.radians(-12))
        starburst(s, x, y, 13, 4.2, rot=0, color="#fff7d0", glowc="#ffc030", gl=3)
    arc(s, zig(hx, hy, 186, 120, 3, 6, 31), 2.6, glow="#ff9a1a", mid="#ffe08a")
    if rank >= 2:
        arc(s, zig(hx, hy, 60, 46, 3, 5, 32), 2.4, glow="#ff9a1a", mid="#ffe08a")
    if rank >= 3:
        arc(s, zig(hx, hy, 52, 104, 3, 5, 33), 2.4, glow="#ff9a1a", mid="#ffe08a")
    return s


# --------------------------------------------------- Overcharged Capacitors
def capacitor(s, cx, top, w, h):
    body = s.lin(cx - w / 2, 0, cx + w / 2, 0, [(0, "#3a1806"), (0.3, "#ffb04a"), (0.55, "#d8741a"), (1, "#3a1404")])
    s.add(f'<rect x="{cx - w / 2}" y="{top}" width="{w}" height="{h}" rx="8" fill="{body}" stroke="#0a0604" stroke-width="3.5"/>')
    # bulged dome top (overcharged)
    dome = s.rad(cx - 6, top - 4, w * 0.7, [(0, "#fff2d0"), (0.35, "#ffc060"), (1, "#7a3a0a")])
    s.add(ellipse(cx, top + 2, w / 2 + 3, 15, dome, 1, "#0a0604", 3))
    s.add(path(f"M{cx - w / 2 + 6},{top - 4} Q{cx - 4},{top - 12} {cx + 8},{top - 8}", "none", "#ffffff", 2.4, 0.8))
    # glowing bands
    for yy in (top + 30, top + h - 30):
        s.glow(f'<rect x="{cx - w / 2 - 2}" y="{yy}" width="{w + 4}" height="9" fill="#7fe6ff"/>', 3, 0.9)
        s.add(f'<rect x="{cx - w / 2 - 1}" y="{yy}" width="{w + 2}" height="9" rx="2" fill="#bff4ff" stroke="#06202e" stroke-width="1.5"/>')
    s.add(line(cx - w / 2 + 8, top + 14, cx - w / 2 + 8, top + h - 8, "#fff0d8", 3, 0.7))
    # base cap
    s.add(f'<rect x="{cx - w / 2 - 3}" y="{top + h - 10}" width="{w + 6}" height="14" rx="3" fill="#2a3446" stroke="#06080e" stroke-width="2.5"/>')
    # terminal
    s.add(f'<rect x="{cx - 5}" y="{top - 28}" width="10" height="18" fill="#9aa8c0" stroke="#06080e" stroke-width="2"/>')
    s.add(circle(cx, top - 30, 6, "#e8f2ff", 1, "#06080e", 2))


def overcharged_caps():
    s = Svg()
    background(s, 603, nebula=("#7a3a1a", "#1a5a8a"))
    s.glow(circle(128, 130, 70, "#ff8a2a"), 18, 0.45)
    capacitor(s, 94, 84, 50, 118)
    capacitor(s, 162, 84, 50, 118)
    # arc bridging the terminals, venting crackle
    arc(s, zig(94, 54, 162, 54, 5, 8, 41), 3.4)
    arc(s, zig(94, 54, 68, 36, 3, 4, 42), 2.4)
    arc(s, zig(162, 54, 190, 38, 3, 4, 43), 2.4)
    flare(s, 128, 52, 18, c1="#c8f4ff", c2=CY_GLOW)
    spark_dots(s, [(56, 64, 2.4), (200, 66, 2.4)])
    return s


# --------------------------------------------------------- Residual Charge
def residual_charge():
    s = Svg()
    background(s, 604, nebula=("#6a2a9a", "#2a3a8a"))
    tx, ty, rot, sc = 128, 100, 0, 1.2
    # fading crackle trails (after-images) swirling around the fork
    for i, (r, op) in enumerate([(72, 0.55), (58, 0.9)]):
        p = []
        for k in range(10):
            a = math.radians(200 + k * 22)
            jit = (4 if k % 2 else -4)
            p.append((128 + (r + jit) * math.cos(a), 112 + (r + jit) * 0.8 * math.sin(a)))
        s.glow(polyline(p, "#a04aff", 10), 4, op)
        s.add(polyline(p, "#b070ff", 5, op))
        s.add(polyline(p, "#f4e4ff", 2, op))
    prod(s, tx, ty, rot, sc, length=110, tipc="#ffd27a")
    # charge orb held between the prongs
    ox, oy = 128, 84
    s.glow(circle(ox, oy, 30, "#b05aff"), 8, 0.95)
    s.add(circle(ox, oy, 22, s.rad(ox, oy, 19, [(0, "#ffffff"), (0.35, "#f0d8ff"), (0.75, "#b06aff"), (1, "#5a1a9a")],
                                  fx=ox - 6, fy=oy - 6), 1, "#2a0a4a", 2))
    for sx in (-1, 1):
        a = to_world(tx, ty, rot, sc, sx * 13, 3)
        arc(s, zig(a[0], a[1], ox + sx * 14, oy - 2, 3, 3, 50 + sx), 2.4, glow="#8a3aff", mid="#e0b8ff")
    spark_dots(s, [(70, 74, 2.4), (190, 70, 2.2), (196, 140, 2.0)], glowc="#c08aff")
    return s


# ---------------------------------------------------------- Arc Conductors
def conductor(s, bx, by, tx, ty):
    rod = s.lin(bx - 8, 0, bx + 8, 0, [(0, "#1c2433"), (0.4, "#d8e4f4"), (1, "#2a3446")])
    s.add(poly(rect_line(bx, by, tx, ty, 17), "#7a8aa4", "#06080e", 3.5))
    s.add(poly(rect_line(bx, by, tx, ty, 6), "#e6eef8", op=0.6))
    # stacked insulator discs
    for t in (0.3, 0.52, 0.74):
        x, y = bx + (tx - bx) * t, by + (ty - by) * t
        ang = math.degrees(math.atan2(ty - by, tx - bx)) + 90
        s.add(ellipse(x, y, 22 - t * 6, 7, rod, 1, "#06080e", 2.6, rot=ang))
    g = s.rad(tx - 4, ty - 4, 16, [(0, "#ffffff"), (0.45, "#9ab0cc"), (1, "#2a3446")])
    s.add(circle(tx, ty, 17, g, 1, "#06080e", 3))


def arc_conductors():
    s = Svg()
    background(s, 605, nebula=("#1a4a9a", "#3a2a8a"))
    # base mount
    s.add(f'<rect x="84" y="186" width="88" height="20" rx="5" fill="#3a4558" stroke="#06080e" stroke-width="3"/>')
    s.add(line(90, 190, 166, 190, "#b8c8de", 2, 0.8))
    conductor(s, 108, 192, 66, 70)
    conductor(s, 148, 192, 190, 70)
    # main arc between tips, plus a second thinner one
    s.add(circle(128, 66, 50, s.rad(128, 66, 50, [(0, "#c8f4ff", 0.6), (1, CY_GLOW, 0)])))
    arc(s, zig(78, 66, 178, 66, 6, 10, 61), 4.2)
    arc(s, zig(76, 76, 180, 76, 5, 8, 62), 2.4, gop=0.7)
    spark_dots(s, [(60, 48, 2.6), (196, 50, 2.6), (128, 104, 2.2)])
    return s


# ---------------------------------------------------------- Discharge Ring
def discharge_ring(rank):
    s = Svg()
    background(s, 606, nebula=("#1a3a9a", "#1a7a9a"))
    cx = cy = 128
    R = 78
    s.add(circle(cx, cy, R + 14, s.rad(cx, cy, R + 14, [(0, CY_GLOW, 0), (0.75, CY_GLOW, 0.25 + 0.15 * rank),
                                                       (0.9, "#9ae8ff", 0.4), (1, CY_GLOW, 0)])))
    # jagged lightning ring
    rnd = random.Random(70 + rank)
    p = []
    N = 36
    for k in range(N + 1):
        a = math.radians(k * 360 / N)
        rr = R + (rnd.uniform(4, 9) * (1 if k % 2 else -1) if k < N else 0)
        if k == N:
            p.append(p[0])
        else:
            p.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    arc(s, p, 4.6 + 0.8 * rank)
    if rank >= 2:
        p2 = []
        for k in range(N + 1):
            a = math.radians(k * 360 / N + 5)
            rr = R - 12 + (rnd.uniform(2, 5) * (1 if k % 2 else -1))
            p2.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        p2[-1] = p2[0]
        arc(s, p2, 3.0)
    # radial discharges from emitter to ring
    nb = 6 * rank
    for i in range(nb):
        a = math.radians(i * 360 / nb + 30)
        arc(s, zig(cx + 30 * math.cos(a), cy + 30 * math.sin(a), cx + (R - 4) * math.cos(a), cy + (R - 4) * math.sin(a),
                   3, 4, 80 + i), 2.2, gop=0.8)
    # central emitter puck (top-down)
    s.add(circle(cx, cy + 4, 34, "#02040a", 0.6))
    rim = s.rad(cx - 10, cy - 12, 40, [(0, "#eef4ff"), (0.45, "#8494ae"), (1, "#1a2230")])
    s.add(circle(cx, cy, 32, rim, 1, "#06080e", 3.5))
    for i in range(8):
        a = math.radians(i * 45)
        s.add(line(cx + 22 * math.cos(a), cy + 22 * math.sin(a), cx + 30 * math.cos(a), cy + 30 * math.sin(a),
                   "#1a2232", 3))
    s.add(circle(cx, cy, 17, "#0a1a30", 1, "#06080e", 2))
    flare(s, cx, cy, 22, c1="#c8f4ff", c2=CY_GLOW)
    s.add(circle(cx, cy, 6, "#ffffff"))
    return s


# ------------------------------------------------------ Aggression Routine
def aggression(status=False):
    s = Svg()
    background(s, 607, nebula=("#8a2a1a", "#5a1a3a"))
    cx, cy = 128, 128
    R = 88 if not status else 68
    s.add(circle(cx, cy, R - 4, s.rad(cx, cy, R, [(0, "#3a0c10"), (0.7, "#1a0610"), (1, "#0a0308")])))
    rg = s.lin(40, 40, 216, 216, [(0, "#ffe0b0"), (0.4, "#ff5a2a"), (0.75, "#b01a10"), (1, "#ffa060")])
    s.glow(circle(cx, cy, R, "none", 1, "#ff4a1a", 9), 3.5, 0.9)
    s.add(circle(cx, cy, R, "none", 1, "#1a0404", 11))
    s.add(circle(cx, cy, R, "none", 1, rg, 7.5))
    s.add(circle(cx, cy, R - 8.5, "none", 0.85, "#ffb080", 1.6))
    # angular glyph teeth on the ring
    for a in range(0, 360, 45):
        ra = math.radians(a)
        p = [(cx + (R - 6) * math.cos(ra - 0.09), cy + (R - 6) * math.sin(ra - 0.09)),
             (cx + (R - 20) * math.cos(ra), cy + (R - 20) * math.sin(ra)),
             (cx + (R - 6) * math.cos(ra + 0.09), cy + (R - 6) * math.sin(ra + 0.09))]
        s.add(poly(p, "#ffd0a0", "#1a0404", 1.5))
    sc = 0.95 if not status else 0.56
    # inward chevrons flanking the prod (aggressive glyphs)
    cg = s.lin(0, 90, 0, 170, [(0, "#fff0d8"), (0.5, "#ff8a4a"), (1, "#d0301a")])
    for sx in (-1, 1):
        def X(x):
            return cx + sx * (x - cx) * (sc / 0.95)
        for k, yy in enumerate((106, 140)):
            yy = cy + (yy - cy) * (sc / 0.95)
            ch = [(X(184), yy - 20), (X(158), yy), (X(184), yy + 20), (X(172), yy + 20), (X(146), yy), (X(172), yy - 20)]
            s.glow(poly(ch, "#ff4a1a"), 3, 0.8)
            s.add(poly(ch, cg, "#1a0404", 2.2))
    prod(s, cx, cy - 76 * sc, 0, sc, length=150, tipc="#ffb060")
    l, r = to_world(cx, cy - 76 * sc, 0, sc, -13, 3), to_world(cx, cy - 76 * sc, 0, sc, 13, 3)
    arc(s, zig(l[0], l[1], r[0], r[1], 4, 4, 91), 3.2 * sc / 0.95, glow="#ff3a10", mid="#ffb070")
    return s


# --------------------------------------------------------- Circuit Breaker
def circuit_breaker():
    s = Svg()
    background(s, 608, nebula=("#1a5a6a", "#7a3a1a"))
    cx, cy = 128, 128
    hexp = [(cx + 82 * math.cos(math.radians(a)), cy + 82 * math.sin(math.radians(a))) for a in range(-90, 270, 60)]
    # split the plate into two halves along a jagged break line
    brk = [(112, 42), (126, 82), (114, 112), (136, 142), (122, 176), (140, 214)]
    plate = s.lin(60, 50, 200, 210, [(0, "#a8b8d0"), (0.45, "#4a5870"), (1, "#141a26")])
    clipL = "M0,0 L112,0 " + " ".join(f"L{x},{y}" for x, y in brk) + " L140,256 L0,256 Z"
    clipR = "M256,0 L112,0 " + " ".join(f"L{x},{y}" for x, y in brk) + " L140,256 L256,256 Z"
    for i, (cp, shift) in enumerate(((clipL, (-6, 3)), (clipR, (6, -3)))):
        cid = s.uid("c")
        s.defs.append(f'<clipPath id="{cid}"><path d="{cp}"/></clipPath>')
        g = []
        g.append(poly(hexp, plate, "#06080e", 4))
        g.append(poly([(cx + 70 * math.cos(math.radians(a)), cy + 70 * math.sin(math.radians(a)))
                       for a in range(-90, 270, 60)], "none", "#c8d6ea", 2, 0.5))
        # circuit traces
        traces = [[(56, 100), (92, 100), (108, 116), (150, 116), (166, 100), (200, 100)],
                  [(56, 156), (84, 156), (100, 140), (156, 140), (172, 156), (200, 156)],
                  [(128, 52), (128, 76), (100, 92)], [(128, 204), (128, 184), (156, 170)]]
        for t in traces:
            g.append(polyline(t, "#06202a", 7.5))
            g.append(polyline(t, "#3ad8ff", 4))
            g.append(polyline(t, "#d8faff", 1.4, 0.9))
        for x, y in [(56, 100), (200, 100), (56, 156), (200, 156), (100, 92), (156, 170)]:
            g.append(circle(x, y, 6, "#d8faff", 1, "#06202a", 2))
        s.add(f'<g clip-path="url(#{cid})"><g transform="translate({shift[0]} {shift[1]})">{"".join(g)}</g></g>')
    # glowing gap + spark at the break
    s.glow(polyline(brk, "#ff7a2a", 9), 4, 0.9)
    s.add(polyline(brk, "#ffe9b8", 2.4))
    flare(s, 122, 128, 26)
    starburst(s, 122, 128, 30, 5, rot=10)
    sparks(s, 122, 128, [(200, 26, 42), (320, 26, 44), (30, 24, 40), (150, 24, 38)], "#ffd7a0", 3)
    return s


# --------------------------------------------------------- Sensor Scramble
def sensor_scramble():
    s = Svg()
    background(s, 609, nebula=("#3a1a6a", "#1a5a6a"))
    cx, cy = 128, 128
    # housing
    s.add(circle(cx, cy + 6, 78, "#02040a", 0.6))
    hous = s.rad(cx - 24, cy - 30, 110, [(0, "#e4ecf8"), (0.4, "#7486a0"), (1, "#141a26")])
    s.add(circle(cx, cy, 76, hous, 1, "#06080e", 4))
    for a in range(0, 360, 30):
        ra = math.radians(a)
        s.add(line(cx + 64 * math.cos(ra), cy + 64 * math.sin(ra), cx + 73 * math.cos(ra), cy + 73 * math.sin(ra),
                   "#1a2232", 3.5))
    s.add(circle(cx, cy, 58, "#0a0e18", 1, "#06080e", 3))
    # lens
    lens = s.rad(cx, cy, 50, [(0, "#ffe0d0"), (0.2, "#ff5a3a"), (0.6, "#a01020"), (1, "#2a0408")], fx=cx - 12, fy=cy - 14)
    s.add(circle(cx, cy, 50, lens))
    s.add(circle(cx, cy, 20, "#1a0204"))
    s.add(ellipse(cx - 20, cy - 22, 14, 8, "#ffffff", 0.7, rot=-30))
    # static / scramble bars across the lens (offset slices)
    cid = s.uid("c")
    s.defs.append(f'<clipPath id="{cid}"><circle cx="{cx}" cy="{cy}" r="58"/></clipPath>')
    bars = [(86, 8, -10, "#ffffff", 0.75), (104, 5, 12, "#7fe6ff", 0.85), (122, 10, -16, "#e0f6ff", 0.6),
            (146, 6, 8, "#7fe6ff", 0.85), (162, 9, -6, "#ffffff", 0.7)]
    g = []
    for y, h, dx, c, op in bars:
        g.append(f'<rect x="{40 + dx}" y="{y}" width="176" height="{h}" fill="#04060c" opacity="0.55"/>')
        g.append(f'<rect x="{52 + dx}" y="{y + 1}" width="{60 + abs(dx) * 3}" height="{h - 2}" fill="{c}" opacity="{op}"/>')
    s.add(f'<g clip-path="url(#{cid})">{"".join(g)}</g>')
    # jagged scramble arcs leaping off the housing
    arc(s, zig(58, 92, 40, 60, 3, 4, 101), 2.6)
    arc(s, zig(198, 164, 216, 196, 3, 4, 102), 2.6)
    arc(s, zig(196, 84, 216, 56, 3, 4, 103), 2.2)
    return s


# ---------------------------------------------------------- Overload Burst
def overload_burst():
    s = Svg()
    background(s, 610, nebula=("#3a2a9a", "#1a7a9a"))
    tx, ty, sc = 128, 112, 0.9
    hx, hy = 128, 102
    s.add(circle(hx, hy, 104, s.rad(hx, hy, 104, [(0, "#ffffff", 0.9), (0.25, "#c8b8ff", 0.6),
                                                   (0.6, "#6a4aff", 0.3), (1, "#3a1a9a", 0)])))
    # radial jagged bolts, long and short alternating
    for i in range(14):
        a = math.radians(i * (360 / 14) - 90 + 6)
        L = 92 if i % 2 == 0 else 66
        x2, y2 = hx + L * math.cos(a), hy + L * math.sin(a)
        arc(s, zig(hx + 18 * math.cos(a), hy + 18 * math.sin(a), x2, y2, 4, 5, 120 + i),
            3.6 if i % 2 == 0 else 2.6, glow="#7a4aff", mid="#c8e8ff")
    prod(s, tx, ty + 12, 0, sc, length=170)
    flare(s, hx, hy, 34, c1="#e0f4ff", c2="#8a6aff")
    starburst(s, hx, hy, 44, 8, rot=0, color="#ffffff", glowc="#b09aff", gl=6)
    starburst(s, hx, hy, 28, 6, rot=45, color="#ffffff", glowc="#9ae8ff", gl=4)
    return s


# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 5):
    JOBS.append((f"ife_am_shkjab{r}", "shockjab", lambda r=r: shock_jab(r)))
JOBS.append(("ife_am_ovrcap", "ovrcap", overcharged_caps))
JOBS.append(("ife_am_reschrg", "reschrg", residual_charge))
for r in range(1, 4):
    JOBS.append((f"ife_am_stunprod{r}", "stunprod", lambda r=r: stun_prod(r)))
JOBS.append(("ife_am_arccond", "arccond", arc_conductors))
for r in range(1, 3):
    JOBS.append((f"ife_am_dischrng{r}", "dischrng", lambda r=r: discharge_ring(r)))
JOBS.append(("ife_am_aggro", "aggro", aggression))
JOBS.append(("ife_am_circbrk", "circbrk", circuit_breaker))
JOBS.append(("ife_am_sensscr", "sensscr", sensor_scramble))
JOBS.append(("ife_am_ovrburst", "ovrburst", overload_burst))

STATUS = [("ief_am_aggro", lambda: aggression(status=True))]


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
    fams = []
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
        if fam not in fams:
            fams.append(fam)
            op = f"{PREV}/{fam}_{resref}.svg"
            open(op, "w", encoding="utf-8").write(svg.render(512))
            magick_render(op, f"{PREV}/{fam}.png")
            os.remove(op)
    # status icons: 256x256 RGB subject PNGs
    for resref, fn in STATUS:
        svg = fn()
        sp = f"{SRC}/{resref}.svg"
        open(sp, "w", encoding="utf-8").write(svg.render(256))
        rp = f"{STATUS_DIR}/{resref}.png"
        magick_render(sp, rp)
        im = Image.open(rp).convert("RGB")
        assert im.size == (256, 256)
        im.save(rp)
        finals[resref + "@32"] = finalize(im)
    # uniqueness check
    keys = list(finals)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert list(finals[keys[i]].getdata()) != list(finals[keys[j]].getdata()), (keys[i], keys[j])
    # contact sheet (32x32 enlarged 8x nearest)
    rows = [[f"ife_am_shkjab{i}" for i in range(1, 5)],
            [f"ife_am_stunprod{i}" for i in range(1, 4)] + ["ife_am_ovrburst"],
            ["ife_am_dischrng1", "ife_am_dischrng2", "ife_am_aggro", "ief_am_aggro@32"],
            ["ife_am_ovrcap", "ife_am_reschrg", "ife_am_arccond", "ife_am_circbrk"],
            ["ife_am_sensscr"]]
    cell, pad = 256, 16
    W = pad + 4 * (cell + pad)
    H = pad + len(rows) * (cell + pad)
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    for ri, row in enumerate(rows):
        for ci, rr in enumerate(row):
            sheet.paste(finals[rr].resize((cell, cell), Image.NEAREST),
                        (pad + ci * (cell + pad), pad + ri * (cell + pad)))
    sheet.save(HERE + "/contact_center.png")
    # previews montage (512 per family)
    cols = 4
    mont = Image.new("RGB", (512 * cols, 512 * ((len(fams) + cols - 1) // cols)), (128, 128, 128))
    for i, f in enumerate(fams):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB"), ((i % cols) * 512, (i // cols) * 512))
    mont.save(HERE + "/previews_512.png")
    print("ok", len(finals), "icons")


if __name__ == "__main__":
    main()
