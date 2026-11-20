"""Astromech Utility Systems - Augments ability-icon CENTER ARTWORK generator.

Follows sw_ability_source/ace/build_ace_icons.py: each icon is an illustrated
SVG (src/<resref>.svg, 256x256 viewBox) rasterized with ImageMagick at 256 px,
downsampled to 32x32 (LANCZOS), lightly sharpened, the outer 3 px ring flattened
to the plain dark background (the icon tool stamps the semantic frame there),
written to out_png/<resref>.png and out_tga/<resref>.tga (32-bit, bottom-left
origin, descriptor 8, opaque), plus contact_center.png, contact_32x8.png and
512 px previews. Status-effect subjects go to status_png/<resref>.png (256 RGB).

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
STATUS = HERE + "/status_png"
PREV = HERE + "/previews"
for d in (SRC, RENDER, OUT_PNG, OUT_TGA, STATUS, PREV):
    os.makedirs(d, exist_ok=True)

BG_RING = (4, 7, 20)
CLIP = (27, 27, 202, 202)  # nothing may reach the outer 3 px (24 units) ring
INK = "#05070c"


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

    def clip(self, shape):
        i = self.uid("c")
        self.defs.append(f'<clipPath id="{i}">{shape}</clipPath>')
        return f"url(#{i})"

    def add(self, s):
        self.body.append(s)

    def glow(self, s, std, op=1.0):
        self.body.append(f'<g filter="{self.blur(std)}" opacity="{op}">{s}</g>')

    def render(self, size=256, status=False):
        clip = (f'<clipPath id="clip"><rect x="{CLIP[0]}" y="{CLIP[1]}" width="{CLIP[2]}" '
                f'height="{CLIP[3]}"/></clipPath>')
        body = "".join(self.body)
        if status:
            # subject shrunk toward the upper left so the bottom-right badge corner stays free,
            # plus a small beneficial shield-and-check accent in the upper right
            body = f'<g transform="translate(36 36) scale(0.78) translate(-36 -36)">{body}</g>' + status_accent(self)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
                f'viewBox="0 0 256 256"><defs>{clip}{"".join(self.defs)}</defs>'
                f'{"".join(self.bg)}<g clip-path="url(#clip)">{body}</g></svg>')


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


def rect(x, y, w, h, fill, rx=0, stroke="none", sw=0, op=1):
    return (f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}" opacity="{op}"/>')


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


def bez(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


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


def flare(s, cx, cy, r, c1="#ffe2b0", c2="#ff6a2a"):
    g = s.rad(cx, cy, r, [(0, "#ffffff", 1), (0.22, "#ffffff", 0.95), (0.5, c1, 0.6),
                          (0.8, c2, 0.28), (1, c2, 0)])
    s.add(circle(cx, cy, r, g))


def sparkle(s, x, y, r, glowc="#9fe8ff"):
    s.glow(circle(x, y, r * 1.9, glowc), 2.5)
    s.add(poly(star4(x, y, r * 1.6, r * 0.45), "#ffffff"))


def steel(s, x1, y1, x2, y2):
    return s.lin(x1, y1, x2, y2, [(0, "#1c2433"), (0.22, "#7d8ea8"), (0.45, "#dbe6f5"), (0.7, "#8597b2"),
                                  (1, "#222a3a")])


def rivet(s, x, y, r=3.4):
    s.add(circle(x, y, r, "#5d6b82", 1, INK, 1.2))
    s.add(circle(x - r * 0.3, y - r * 0.35, r * 0.4, "#eef5ff", 0.9))


def shift(s, dx=0, dy=0, sc=1.0):
    """Move and scale the whole subject about the icon centre."""
    s.body = [f'<g transform="translate({128 + dx} {128 + dy}) scale({sc}) translate(-128 -128)">'
              + "".join(s.body) + '</g>']


def status_accent(s):
    """Small green shield with a white check, upper right (beneficial status marker)."""
    cx, cy = 202, 60
    sh = (f"M{cx},{cy - 26} L{cx + 22},{cy - 17} L{cx + 20},{cy + 6} C{cx + 17},{cy + 18} {cx + 8},{cy + 24} "
          f"{cx},{cy + 28} C{cx - 8},{cy + 24} {cx - 17},{cy + 18} {cx - 20},{cy + 6} L{cx - 22},{cy - 17} Z")
    g = s.lin(cx - 20, cy - 26, cx + 20, cy + 28, [(0, "#b8ffd0"), (0.45, "#2fc46a"), (1, "#0d5a2a")])
    o = [f'<g filter="{s.blur(4)}" opacity="0.8">{path(sh, "#3cff8a")}</g>',
         path(sh, g, INK, 3.5),
         polyline([(cx - 10, cy + 1), (cx - 2, cy + 10), (cx + 12, cy - 9)], INK, 9),
         polyline([(cx - 10, cy + 1), (cx - 2, cy + 10), (cx + 12, cy - 9)], "#ffffff", 5)]
    return "".join(o)


# ------------------------------------------------------- Power Transfer
def power_transfer(rank):
    e = (rank - 1) / 2.0
    s = Svg()
    background(s, 611, nebula=("#8a5a1a", "#1a5a8a"))
    # cable from the cell's top terminal arcing over into the socket on the right
    p0, p1, p2, p3 = (86, 62), (90, 18), (176, 22), (178, 112)
    d = f"M{p0[0]},{p0[1]} C{p1[0]},{p1[1]} {p2[0]},{p2[1]} {p3[0]},{p3[1]}"
    s.glow(path(d, "none", "#ffb43a", 20), 6, 0.55 + 0.2 * e)
    s.add(path(d, "none", INK, 17))
    s.add(path(d, "none", "#3a3f4c", 13))
    s.add(path(d, "none", "#ffcf6a", 5.5))
    s.add(path(d, "none", "#fff6d8", 2.2, 0.9))
    # energy pulses travelling along the cable
    ts = [0.3, 0.62] + ([0.46] if rank >= 2 else []) + ([0.8] if rank >= 3 else [])
    for t in ts:
        x, y = bez(p0, p1, p2, p3, t)
        s.glow(circle(x, y, 11, "#ffb43a"), 4)
        s.add(circle(x, y, 6.5, "#fff1c4"))
    # socket block (right)
    sg = steel(s, 148, 0, 214, 0)
    s.add(rect(148, 124, 64, 72, sg, 10, INK, 3.5))
    s.add(rect(160, 136, 40, 40, "#141a26", 6, INK, 2.5))
    s.glow(rect(166, 142, 28, 28, "#ffb43a", 4), 5, 0.6 + 0.3 * e)
    s.add(rect(168, 146, 7, 18, "#fff1c4", 2))
    s.add(rect(185, 146, 7, 18, "#fff1c4", 2))
    for x, y in [(155, 131), (205, 131), (155, 189), (205, 189)]:
        rivet(s, x, y, 2.8)
    # plug head entering the socket
    pg = steel(s, 164, 0, 192, 0)
    s.add(rect(164, 96, 28, 34, pg, 5, INK, 3))
    s.add(rect(168, 104, 20, 4, "#2a3040", 1))
    s.add(rect(168, 112, 20, 4, "#2a3040", 1))
    # power cell (left)
    cg = steel(s, 52, 0, 120, 0)
    s.add(ellipse(86, 206, 40, 9, "#000000", 0.5))
    s.add(rect(52, 74, 68, 128, cg, 14, INK, 3.5))
    s.add(rect(74, 56, 24, 22, steel(s, 74, 0, 98, 0), 4, INK, 3))
    win = s.lin(0, 92, 0, 186, [(0, "#fff3c8"), (0.4, "#ffb43a"), (1, "#a8460c")])
    s.glow(rect(62, 92, 48, 94, "#ff9a2a", 8), 6, 0.55 + 0.25 * e)
    s.add(rect(62, 92, 48, 94, win, 8, INK, 3))
    for y in (114, 136, 158):
        s.add(line(64, y, 108, y, "#5a2206", 3, 0.75))
    s.add(rect(68, 96, 9, 84, "#ffffff", 4, op=0.45))
    for y in (80, 194):
        s.add(line(56, y, 116, y, "#0b1018", 2.2, 0.8))
    # lightning-bolt power glyph on the cell window
    s.add(poly([(92, 104), (78, 140), (88, 140), (82, 172), (100, 128), (90, 128), (98, 104)], INK, op=0.85))
    s.add(poly([(92, 106), (80, 138), (90, 138), (85, 166), (98, 130), (88, 130), (96, 106)], "#fffbe8"))
    if rank >= 2:
        sparkle(s, 140, 46, 4.5, "#ffc870")
    if rank >= 3:
        sparkle(s, 212, 92, 4, "#ffc870")
        sparkle(s, 134, 100, 3.5, "#ffc870")
    shift(s, 2, 8)
    return s


# ------------------------------------------------------- Deflector Field
def deflector_field(rank):
    e = (rank - 1) / 2.0
    s = Svg()
    background(s, 622, nebula=("#1a4a9a", "#1a8aaa"))
    cx, cy, R = 128, 168, 88
    dome = f"M{cx - R},{cy} A{R},{R} 0 0 1 {cx + R},{cy} Z"
    s.glow(path(dome, "#3ab8ff"), 9, 0.45 + 0.15 * e)
    fill = s.rad(cx, cy - 10, R, [(0, "#0c2a6a", 0.55), (0.7, "#1f6ad0", 0.5), (1, "#7fdcff", 0.9)])
    s.add(path(dome, fill))
    # hex lattice clipped to the dome
    cp = s.clip(path(dome, "#fff"))
    hexes = []
    r = 21
    lit_rng = random.Random(9)
    for row in range(-1, 8):
        for col in range(-1, 10):
            hx = 40 + col * r * math.sqrt(3) + (row % 2) * r * math.sqrt(3) / 2
            hy = cy - row * r * 1.5
            hp = [(hx + r * math.cos(math.radians(60 * k + 30)), hy + r * math.sin(math.radians(60 * k + 30)))
                  for k in range(6)]
            lit = lit_rng.random() < 0.06 * rank
            hexes.append(poly(hp, "#7fd8ff" if lit else "none", "#d8f8ff", 3.6, 0.35 if lit else 0.9))
    s.add(f'<g clip-path="{cp}">{"".join(hexes)}</g>')
    # dome rim
    rim = f"M{cx - R},{cy} A{R},{R} 0 0 1 {cx + R},{cy}"
    s.glow(path(rim, "none", "#6fd8ff", 10), 4)
    s.add(path(rim, "none", "#e8fbff", 4.5))
    s.add(path(f"M{cx - 70},{cy - 50} A{R - 12},{R - 12} 0 0 1 {cx - 20},{cy - 80}", "none", "#ffffff", 4, 0.7))
    # projector emitter at the base
    s.add(ellipse(cx, 216, 70, 9, "#000000", 0.55))
    body = steel(s, 72, 0, 184, 0)
    s.add(poly([(74, 208), (182, 208), (164, 166), (92, 166)], body, INK, 4))
    s.add(rect(62, 200, 132, 16, steel(s, 62, 0, 194, 0), 6, INK, 3.5))
    for x in (100, 128, 156):
        s.add(line(x, 176, x + (x - 128) * 0.25, 198, "#0b1018", 3, 0.75))
    s.glow(ellipse(cx, 166, 40, 12, "#8ff0ff"), 6)
    s.add(ellipse(cx, 166, 34, 9, "#eaffff", 1, INK, 3))
    # projection rays from the lens
    for a in (-60, -90, -120):
        ra = math.radians(a)
        s.add(line(cx, 162, cx + 52 * math.cos(ra), 162 + 52 * math.sin(ra), "#dffaff", 3, 0.55))
    if rank >= 2:
        sparkle(s, 194, 104, 5)
    if rank >= 3:
        sparkle(s, 62, 112, 4.5)
        s.glow(path(rim, "none", "#ffffff", 6), 3, 0.9)
    return s


# ------------------------------------------------------- Hardened Buffers
def hardened_buffers():
    s = Svg()
    background(s, 633, nebula=("#3a4a6a", "#5a3a1a"))
    # 3/4 view armored capacitor block
    fx0, fy0, fx1, fy1, dx, dy = 52, 100, 172, 210, 36, -30
    s.add(ellipse(126, 214, 82, 10, "#000000", 0.55))
    top = s.lin(fx0, fy0 + dy, fx1 + dx, fy0, [(0, "#eef4ff"), (0.6, "#a2b2c8"), (1, "#5e6c84")])
    side = s.lin(fx1, 0, fx1 + dx, 0, [(0, "#3a4458"), (1, "#151b27")])
    front = s.lin(fx0, fy0, fx1, fy1, [(0, "#b8c6da"), (0.45, "#6c7c96"), (1, "#283244")])
    s.add(poly([(fx0, fy0), (fx0 + dx, fy0 + dy), (fx1 + dx, fy0 + dy), (fx1, fy0)], top, INK, 3.5))
    s.add(poly([(fx1, fy0), (fx1 + dx, fy0 + dy), (fx1 + dx, fy1 + dy), (fx1, fy1)], side, INK, 3.5))
    s.add(poly([(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1)], front, INK, 3.5))
    # bevelled armor plate on the front
    s.add(poly([(fx0 + 10, fy0 + 10), (fx1 - 10, fy0 + 10), (fx1 - 10, fy1 - 10), (fx0 + 10, fy1 - 10)],
               s.lin(0, fy0, 0, fy1, [(0, "#d6e2f2"), (0.5, "#7a8aa4"), (1, "#3a465a")]), INK, 2.5))
    # diagonal hazard band
    band = s.clip(rect(fx0 + 10, fy0 + 10, fx1 - fx0 - 20, fy1 - fy0 - 20, "#fff"))
    st = []
    for i in range(-4, 8):
        x = fx0 + i * 22
        st.append(poly([(x, fy0 + 62), (x + 11, fy0 + 62), (x + 25, fy0 + 82), (x + 14, fy0 + 82)], "#1a1408"))
    s.add(f'<g clip-path="{band}">{rect(fx0, fy0 + 62, 140, 20, "#f0b030")}{"".join(st)}</g>')
    s.add(line(fx0 + 10, fy0 + 62, fx1 - 10, fy0 + 62, INK, 2))
    s.add(line(fx0 + 10, fy0 + 82, fx1 - 10, fy0 + 82, INK, 2))
    # charge window slit with cyan cells
    s.add(rect(fx0 + 22, fy0 + 22, 76, 28, "#0a1220", 5, INK, 2.5))
    for i in range(4):
        x = fx0 + 28 + i * 17
        s.glow(rect(x, fy0 + 27, 12, 18, "#4fd8ff", 2), 3, 0.8)
        s.add(rect(x, fy0 + 27, 12, 18, "#c8f6ff", 2))
    # rivets
    for x, y in [(fx0 + 16, fy0 + 16), (fx1 - 16, fy0 + 16), (fx0 + 16, fy1 - 16), (fx1 - 16, fy1 - 16),
                 (fx0 + 16, fy0 + 95), (fx1 - 16, fy0 + 95)]:
        rivet(s, x, y, 4)
    for y in (fy0 + 14, fy1 - 22):
        rivet(s, fx1 + dx / 2, y + dy / 2, 3.2)
    s.add(line(fx0 + 2, fy0 - 1, fx0 + dx, fy0 + dy + 2, "#ffffff", 2.4, 0.7))
    s.add(line(fx0 + 2, fy0 + 3, fx1 - 2, fy0 + 3, "#ffffff", 2, 0.55))
    # two terminals on top
    for tx, col in [(fx0 + 44, "#ffb43a"), (fx0 + 112, "#4fd8ff")]:
        ty = fy0 + dy / 2 + 2
        s.add(rect(tx - 12, ty - 26, 24, 26, steel(s, tx - 12, 0, tx + 12, 0), 3, INK, 2.5))
        s.add(ellipse(tx, ty, 12, 4.5, "#3a4458", 1, INK, 2))
        s.glow(ellipse(tx, ty - 27, 12, 5, col), 3.5)
        s.add(ellipse(tx, ty - 27, 11, 4.5, col, 1, INK, 2))
        s.add(ellipse(tx - 3, ty - 28, 4, 1.6, "#ffffff", 0.9))
    return s


# ------------------------------------------------------- Damper Field
def damper_field(rank):
    e = rank - 1
    s = Svg()
    background(s, 644, nebula=("#2a3a9a", "#2a8a9a"))
    cx, cy = 128, 128
    # incoming jagged red spike (left) flattened into a calm cyan line (right)
    spike = [(34, 128), (46, 128), (54, 84), (64, 170), (74, 96), (84, 154), (92, 128), (100, 128)]
    s.glow(polyline(spike, "#ff3a2a", 12), 4, 0.9)
    s.add(polyline(spike, "#5a0a0a", 9))
    s.add(polyline(spike, "#ff6a4a", 6))
    s.add(polyline(spike, "#ffe0d0", 2.4))
    calm = [(156, 128), (174, 124), (190, 132), (206, 126), (222, 128)]
    s.glow(polyline(calm, "#3ac8ff", 12), 4, 0.9)
    s.add(polyline(calm, "#08304a", 9))
    s.add(polyline(calm, "#6fe0ff", 6))
    s.add(polyline(calm, "#f0fdff", 2.4))
    # damping wave arcs (open at left and right where the signal passes)
    for i, R in enumerate([52, 72, 92] if rank == 1 else [50, 66, 82, 97]):
        op = 1.0 - i * 0.15
        for a0, a1 in [(-150, -30), (30, 150)]:
            x0, y0 = cx + R * math.cos(math.radians(a0)), cy + R * math.sin(math.radians(a0))
            x1, y1 = cx + R * math.cos(math.radians(a1)), cy + R * math.sin(math.radians(a1))
            d = f"M{x0:.2f},{y0:.2f} A{R},{R} 0 0 1 {x1:.2f},{y1:.2f}"
            s.glow(path(d, "none", "#4fc3ff", 11), 3.5, op)
            s.add(path(d, "none", "#d8f6ff", 5, op))
    # emitter coil disc
    s.add(circle(cx, cy + 4, 36, "#000000", 0.5))
    s.add(circle(cx, cy, 34, s.rad(cx, cy, 34, [(0, "#eef4ff"), (0.5, "#7a8aa4"), (1, "#1c2433")],
                                     fx=cx - 12, fy=cy - 14), 1, INK, 3.5))
    for k in range(8):
        a = math.radians(k * 45 + 22.5)
        s.add(line(cx + 24 * math.cos(a), cy + 24 * math.sin(a), cx + 32 * math.cos(a), cy + 32 * math.sin(a),
                   "#141a26", 3, 0.85))
    s.glow(circle(cx, cy, 18 + 2 * e, "#5fd8ff"), 5)
    s.add(circle(cx, cy, 17, s.rad(cx, cy, 17, [(0, "#ffffff"), (0.45, "#9ff0ff"), (1, "#1a7ad0")]), 1, INK, 2.5))
    s.add(circle(cx - 5, cy - 6, 4.5, "#ffffff", 0.9))
    if rank >= 2:
        sparkle(s, 186, 74, 4.5)
        sparkle(s, 70, 186, 4)
    return s


# ------------------------------------------------------- Filter Flush
def filter_flush():
    s = Svg()
    background(s, 655, nebula=("#1a5a9a", "#5a1a7a"))
    rot = -38
    o = []
    # clean stream running through the cartridge
    sg = s.lin(-112, 0, 112, 0, [(0, "#2a7aff", 0), (0.25, "#4fb8ff", 0.9), (1, "#e8fbff", 1)])
    o.append(f'<g filter="{s.blur(4)}" opacity="0.8">{line(-104, 0, 104, 0, "#4fc3ff", 30)}</g>')
    o.append(line(-104, 0, 104, 0, sg, 16))
    o.append(path("M-96,-3 C-60,-12 -30,8 0,-2 S60,-10 98,-2", "none", "#ffffff", 3, 0.8))
    # cartridge
    body = s.lin(0, -36, 0, 36, [(0, "#1c2433"), (0.2, "#8fa0ba"), (0.42, "#e4eefb"), (0.7, "#6e809c"),
                                 (1, "#1a2130")])
    o.append(rect(-64, -36, 128, 72, body, 10, INK, 3.5))
    # glass mid window showing a pleated filter with clean flow
    gl = s.lin(0, -26, 0, 26, [(0, "#bff2ff"), (0.5, "#3aa0e0"), (1, "#0c3a7a")])
    o.append(rect(-40, -26, 80, 52, gl, 6, INK, 2.5))
    for x in range(-34, 40, 9):
        o.append(polyline([(x, -24), (x + 4, -16), (x, -8), (x + 4, 0), (x, 8), (x + 4, 16), (x, 24)],
                          "#e8fbff", 2, 0.7))
    o.append(rect(-36, -22, 72, 7, "#ffffff", 3, op=0.45))
    # end caps with threaded rings
    for sx in (-1, 1):
        x = sx * 64
        o.append(rect(x - 9 if sx > 0 else x - 9, -30, 18, 60, s.lin(0, -30, 0, 30, [(0, "#3a4458"), (0.4, "#c4d2e6"),
                                                                                    (1, "#1a2130")]), 4, INK, 3))
        for dy_ in (-18, -6, 6, 18):
            o.append(line(x - 7, dy_, x + 7, dy_, "#0b1018", 1.8, 0.7))
    s.add(f'<g transform="translate(124 132) rotate({rot})">{"".join(o)}</g>')
    # nozzle spray of flushed toxin droplets at the downstream end (upper right)
    ex, ey = 124 + 104 * math.cos(math.radians(rot)), 132 + 104 * math.sin(math.radians(rot))
    for dx_, dy_, r in [(-6, -14, 9), (16, -6, 7), (6, 10, 6.5), (-20, -30, 5.5), (24, -26, 5)]:
        x, y = ex + dx_ - 8, ey + dy_ + 6
        s.glow(circle(x, y, r + 3, "#b04aff"), 3, 0.8)
        s.add(path(f"M{x},{y - r * 1.5} C{x + r},{y - r * 0.2} {x + r},{y + r} {x},{y + r} "
                   f"C{x - r},{y + r} {x - r},{y - r * 0.2} {x},{y - r * 1.5} Z",
                   s.rad(x - r * 0.3, y, r * 1.3, [(0, "#e8b0ff"), (0.6, "#8a2ad0"), (1, "#3a0a6a")]), INK, 2))
    # intake splash at the upstream end
    ix, iy = 124 - 104 * math.cos(math.radians(rot)), 132 - 104 * math.sin(math.radians(rot))
    sparkle(s, ix + 4, iy - 18, 4.5)
    sparkle(s, ix + 22, iy + 6, 3.8)
    shift(s, 4, -2, 0.84)
    return s


# ------------------------------------------------------- Efficient Dispenser
def efficient_dispenser():
    s = Svg()
    background(s, 666, nebula=("#2a5a8a", "#7a5a1a"))
    # canister
    s.add(ellipse(128, 214, 54, 8, "#000000", 0.55))
    cg = steel(s, 78, 0, 178, 0)
    s.add(rect(78, 56, 100, 136, cg, 22, INK, 3.5))
    s.add(rect(90, 40, 76, 22, steel(s, 90, 0, 166, 0), 8, INK, 3))
    s.add(rect(110, 32, 36, 12, "#2a3242", 4, INK, 2.5))
    s.add(line(84, 82, 172, 82, "#0b1018", 2.4, 0.8))
    s.add(line(84, 172, 172, 172, "#0b1018", 2.4, 0.8))
    s.add(rect(88, 64, 10, 120, "#ffffff", 5, op=0.35))
    # metering dial
    dx, dy, R = 128, 128, 32
    s.add(circle(dx, dy + 2, R + 6, "#000000", 0.4))
    s.add(circle(dx, dy, R + 5, steel(s, dx - R, 0, dx + R, 0), 1, INK, 3))
    face = s.rad(dx, dy, R, [(0, "#ffffff"), (0.7, "#e2ecf6"), (1, "#9aaac0")], fx=dx - 10, fy=dy - 12)
    s.add(circle(dx, dy, R, face, 1, INK, 2.5))
    # green economy arc on the dial
    a0, a1 = math.radians(200), math.radians(260)
    s.add(path(f"M{dx + 24 * math.cos(a0):.2f},{dy + 24 * math.sin(a0):.2f} A24,24 0 0 1 "
               f"{dx + 24 * math.cos(a1):.2f},{dy + 24 * math.sin(a1):.2f}", "none", "#2fbf5a", 6))
    a2 = math.radians(330)
    s.add(path(f"M{dx + 24 * math.cos(math.radians(290)):.2f},{dy + 24 * math.sin(math.radians(290)):.2f} A24,24 0 0 1 "
               f"{dx + 24 * math.cos(a2):.2f},{dy + 24 * math.sin(a2):.2f}", "none", "#e04a2a", 6))
    for k in range(9):
        a = math.radians(150 + k * 30)
        s.add(line(dx + 20 * math.cos(a), dy + 20 * math.sin(a), dx + 28 * math.cos(a), dy + 28 * math.sin(a),
                   "#1a2130", 2.4))
    na = math.radians(228)
    s.add(line(dx, dy, dx + 26 * math.cos(na), dy + 26 * math.sin(na), INK, 7))
    s.add(line(dx, dy, dx + 26 * math.cos(na), dy + 26 * math.sin(na), "#ff9a2a", 4))
    s.add(circle(dx, dy, 5.5, "#2a3242", 1, INK, 2))
    # dispensing nozzle and one capsule
    s.add(poly([(112, 190), (144, 190), (136, 204), (120, 204)], steel(s, 112, 0, 144, 0), INK, 3))
    cx, cy = 128, 218
    s.glow(ellipse(cx, cy, 15, 9, "#ffb43a"), 3.5)
    s.add(f'<g transform="rotate(0 {cx} {cy})">'
          f'{rect(cx - 14, cy - 7, 28, 14, s.lin(cx - 14, 0, cx + 14, 0, [(0, "#ffd070"), (0.5, "#ff9a2a"), (0.5, "#e8f2ff"), (1, "#8aa0bc")]), 7, INK, 2.2)}'
          f'{rect(cx - 10, cy - 5, 18, 3, "#ffffff", 1.5, op=0.8)}</g>')
    sparkle(s, 194, 70, 4.5, "#ffd070")
    shift(s, 0, -8)
    return s


# ------------------------------------------------------- Targeting Uplink
def targeting_uplink(rank):
    e = rank - 1
    s = Svg()
    background(s, 677, nebula=("#1a3a8a", "#8a2a3a"))
    tx, ty = 170, 84  # reticle centre
    fx, fy = 107, 145  # dish feed horn
    # beam cone from the feed to the reticle
    ang = math.atan2(ty - fy, tx - fx)
    nx, ny = -math.sin(ang), math.cos(ang)
    cone = [(fx + nx * 5, fy + ny * 5), (tx + nx * 22, ty + ny * 22), (tx - nx * 22, ty - ny * 22),
            (fx - nx * 5, fy - ny * 5)]
    bg_ = s.lin(fx, fy, tx, ty, [(0, "#9ff0ff", 0.95), (1, "#3ab8ff", 0.25)])
    s.glow(poly(cone, "#3ab8ff"), 6, 0.45 + 0.2 * e)
    s.add(poly(cone, bg_))
    s.add(line(fx, fy, tx - 18 * math.cos(ang), ty - 18 * math.sin(ang), "#ffffff", 3, 0.85))
    for t in (0.35, 0.6):
        x, y = fx + (tx - fx) * t, fy + (ty - fy) * t
        s.add(line(x + nx * (8 + 10 * t), y + ny * (8 + 10 * t), x - nx * (8 + 10 * t), y - ny * (8 + 10 * t),
                   "#e8fbff", 2.4, 0.7))
    # reticle
    Rr = 34
    s.glow(circle(tx, ty, Rr, "none", 1, "#ff5a3a", 8), 3.5, 0.8 + 0.2 * e)
    s.add(circle(tx, ty, Rr, "none", 1, INK, 8))
    s.add(circle(tx, ty, Rr, "none", 1, "#ff8a5a", 4.5))
    s.add(circle(tx, ty, Rr, "none", 0.9, "#fff0e0", 1.5))
    for a in (0, 90, 180, 270):
        ra = math.radians(a)
        x0, y0 = tx + (Rr - 12) * math.cos(ra), ty + (Rr - 12) * math.sin(ra)
        x1, y1 = tx + (Rr + 10) * math.cos(ra), ty + (Rr + 10) * math.sin(ra)
        s.add(line(x0, y0, x1, y1, INK, 7))
        s.add(line(x0, y0, x1, y1, "#ffd0b8", 3.6))
    if rank >= 2:
        s.add(circle(tx, ty, 16, "none", 1, "#ffb08a", 2.6))
        sparkle(s, 214, 132, 4)
    s.glow(circle(tx, ty, 8, "#ff4a2a"), 3)
    s.add(circle(tx, ty, 4.5, "#ffffff"))
    # parabolic dish facing up-right, on a pedestal
    s.add(ellipse(78, 214, 46, 8, "#000000", 0.55))
    s.add(poly([(62, 210), (96, 210), (88, 182), (70, 182)], steel(s, 62, 0, 96, 0), INK, 3))
    s.add(rect(48, 204, 62, 12, steel(s, 48, 0, 110, 0), 4, INK, 3))
    dish = s.lin(40, 120, 110, 200, [(0, "#eef4ff"), (0.45, "#8a9ab4"), (1, "#202838")])
    s.add(f'<g transform="translate(80 172) rotate(-45) scale(1.25)">'
          f'{path("M-50,0 C-50,32 50,32 50,0 Z", dish, INK, 3.5)}'
          f'{path("M-50,0 C-50,32 50,32 50,0", "none", "#ffffff", 2.4, 0.5)}'
          f'{ellipse(0, 0, 50, 9, "#2a3448", 1, INK, 3)}'
          f'{ellipse(0, 0, 40, 6, "#4a5872", 0.9)}'
          f'{line(-34, 0, 0, -30, "#9aaac0", 3.4)}{line(34, 0, 0, -30, "#9aaac0", 3.4)}'
          f'{line(0, 0, 0, -30, "#c8d6ea", 4)}</g>')
    s.glow(circle(fx, fy, 9, "#9ff0ff"), 3.5)
    s.add(circle(fx, fy, 6, "#ffffff", 1, INK, 2))
    return s


# ------------------------------------------------------- Full Override
def full_override():
    s = Svg()
    background(s, 688, nebula=("#9a3a1a", "#2a4a9a"))
    cx, cy = 128, 132
    # surge arcs radiating out
    rnd = random.Random(31)
    for k in range(8):
        a = math.radians(k * 45 + 10)
        p = [(cx + 46 * math.cos(a), cy + 46 * math.sin(a))]
        for r in (62, 76, 90):
            j = math.radians(rnd.uniform(-9, 9))
            p.append((cx + r * math.cos(a + j), cy + r * math.sin(a + j)))
        s.glow(polyline(p, "#ff9a2a", 11), 3.5)
        s.add(polyline(p, "#ffd27a", 5.5))
        s.add(polyline(p, "#ffffff", 2.2, 0.9))
    # octagonal armored housing
    oc = [(cx + 58 * math.cos(math.radians(22.5 + 45 * k)), cy + 58 * math.sin(math.radians(22.5 + 45 * k)))
          for k in range(8)]
    s.add(poly([(x + 2, y + 5) for x, y in oc], "#000000", op=0.5))
    s.add(poly(oc, s.lin(cx - 58, cy - 58, cx + 58, cy + 58, [(0, "#dbe6f5"), (0.45, "#7d8ea8"), (1, "#1c2433")]),
               INK, 3.5))
    for x, y in oc:
        rivet(s, cx + (x - cx) * 0.85, cy + (y - cy) * 0.85, 3)
    # maxed gauge ring: segments all lit, the last ones red-hot
    for k in range(10):
        a0, a1 = math.radians(135 + k * 27 + 3), math.radians(135 + (k + 1) * 27 - 3)
        col = "#ffd040" if k < 6 else ("#ff7a2a" if k < 8 else "#ff2a2a")
        d = (f"M{cx + 40 * math.cos(a0):.2f},{cy + 40 * math.sin(a0):.2f} A40,40 0 0 1 "
             f"{cx + 40 * math.cos(a1):.2f},{cy + 40 * math.sin(a1):.2f}")
        if k < 10 and 135 + (k + 1) * 27 <= 135 + 270:
            s.add(path(d, "none", INK, 11))
            s.glow(path(d, "none", col, 8), 2.5, 0.8)
            s.add(path(d, "none", col, 7))
    # white-hot core
    s.glow(circle(cx, cy, 30, "#ffb43a"), 7)
    s.add(circle(cx, cy, 26, s.rad(cx, cy, 26, [(0, "#ffffff"), (0.45, "#fff2c0"), (0.8, "#ffa030"),
                                                 (1, "#a03a0a")]), 1, INK, 3))
    flare(s, cx, cy, 30, c1="#fff6d0", c2="#ffb43a")
    s.add(poly(star4(cx, cy, 40, 5), "#ffffff", op=0.95))
    sparkle(s, 196, 60, 4.5, "#ffc870")
    sparkle(s, 60, 200, 4, "#ffc870")
    return s


# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 4):
    JOBS.append((f"ife_am_pwrxfer{r}", "pwrxfer", lambda r=r: power_transfer(r)))
for r in range(1, 4):
    JOBS.append((f"ife_am_deflfld{r}", "deflfld", lambda r=r: deflector_field(r)))
JOBS.append(("ife_am_hardbuf", "hardbuf", hardened_buffers))
for r in range(1, 3):
    JOBS.append((f"ife_am_damper{r}", "damper", lambda r=r: damper_field(r)))
JOBS.append(("ife_am_fltflush", "fltflush", filter_flush))
JOBS.append(("ife_am_effdisp", "effdisp", efficient_dispenser))
for r in range(1, 3):
    JOBS.append((f"ife_am_tgtuplnk{r}", "tgtuplnk", lambda r=r: targeting_uplink(r)))
JOBS.append(("ife_am_fullovr", "fullovr", full_override))

STATUS_JOBS = [
    (["ief_am_pwrxfer"], lambda: power_transfer(2)),
    (["ief_am_deflfld1", "ief_am_deflfld2", "ief_am_deflfld3"], lambda: deflector_field(2)),
    (["ief_am_damper1", "ief_am_damper2"], lambda: damper_field(1)),
    (["ief_am_tgtuplnk1", "ief_am_tgtuplnk2"], lambda: targeting_uplink(1)),
    (["ief_am_fullovr"], full_override),
    (["ief_am_fltflush"], filter_flush),
]


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
    prev_fams = []
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
        assert list(chk.convert("RGB").get_flattened_data()) == list(im.get_flattened_data()), resref
        assert all(a == 255 for _, _, _, a in chk.get_flattened_data())
        finals[resref] = im
        if fam not in prev_fams:
            prev_fams.append(fam)
            op = f"{PREV}/{fam}_{resref}.svg"
            open(op, "w", encoding="utf-8").write(svg.render(512))
            magick_render(op, f"{PREV}/{fam}.png")
            os.remove(op)
    keys = list(finals)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert list(finals[keys[i]].get_flattened_data()) != list(finals[keys[j]].get_flattened_data()), (keys[i], keys[j])
    # status subjects
    status = {}
    for resrefs, fn in STATUS_JOBS:
        svg = fn()
        sp = f"{RENDER}/_status.svg"
        open(sp, "w", encoding="utf-8").write(svg.render(256, status=True))
        rp = f"{RENDER}/_status.png"
        magick_render(sp, rp)
        im = Image.open(rp).convert("RGB")
        assert im.size == (256, 256)
        for rr in resrefs:
            im.save(f"{STATUS}/{rr}.png")
            status[rr] = im
        os.remove(sp)
        os.remove(rp)
    # contact sheets
    rows = [["ife_am_pwrxfer%d" % i for i in range(1, 4)], ["ife_am_deflfld%d" % i for i in range(1, 4)],
            ["ife_am_damper1", "ife_am_damper2", "ife_am_hardbuf"],
            ["ife_am_tgtuplnk1", "ife_am_tgtuplnk2", "ife_am_fltflush"],
            ["ife_am_effdisp", "ife_am_fullovr"]]
    for name, cell in (("contact_center.png", 256),):
        pad = 16
        W = pad + 3 * (cell + pad)
        H = pad + len(rows) * (cell + pad)
        sheet = Image.new("RGB", (W, H), (128, 128, 128))
        for ri, row in enumerate(rows):
            for ci, rr in enumerate(row):
                sheet.paste(finals[rr].resize((cell, cell), Image.NEAREST),
                            (pad + ci * (cell + pad), pad + ri * (cell + pad)))
        sheet.save(HERE + "/" + name)
    # previews montage (512) of each base artwork
    mont = Image.new("RGB", (512 * 4, 512 * 2), (128, 128, 128))
    for i, f in enumerate(prev_fams):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB"), ((i % 4) * 512, (i // 4) * 512))
    mont.save(HERE + "/previews_512.png")
    # status sheet
    sk = ["ief_am_pwrxfer", "ief_am_deflfld1", "ief_am_damper1", "ief_am_tgtuplnk1", "ief_am_fullovr",
          "ief_am_fltflush"]
    ss = Image.new("RGB", (16 + 3 * 272, 16 + 2 * 272), (128, 128, 128))
    for i, k in enumerate(sk):
        ss.paste(status[k], (16 + (i % 3) * 272, 16 + (i // 3) * 272))
    ss.save(HERE + "/contact_status.png")
    print("ok", len(finals), "icons,", len(status), "status")


if __name__ == "__main__":
    main()
