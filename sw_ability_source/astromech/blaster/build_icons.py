"""Astromech Armaments - Blaster Mount ability-icon CENTER ARTWORK generator.

Follows sw_ability_source/ace/build_ace_icons.py: each icon is an illustrated SVG
(src/<resref>.svg, 256x256 viewBox), rasterized with ImageMagick at 256 px,
downsampled to 32x32 with LANCZOS, given light edge definition, the outer 3 px
ring flattened to plain dark background (the project icon tool stamps the frame
there), and written to out_png/<resref>.png and out_tga/<resref>.tga (32-bit,
bottom-left origin, descriptor 8). Also writes status_png/<resref>.png (256x256
RGB subjects for the status-effect import), contact_center.png, contact_32x8.png
and 512 px previews.

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


def arc_pts(cx, cy, r, a0, a1, n=24):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


# --------------------------------------------------------------- shared art
def background(s, seed, nebula=("#2a5a95", "#1a6a8a")):
    g = s.rad(128, 128, 190, [(0, "#142058"), (0.55, "#080e2c"), (1, "#02030b")])
    s.bg.append(f'<rect width="256" height="256" fill="{g}"/>')
    s.bg.append(f'<g filter="{s.blur(22)}" opacity="0.55">{ellipse(70, 62, 62, 34, nebula[0], 1, rot=-25)}</g>')
    s.bg.append(f'<g filter="{s.blur(22)}" opacity="0.5">{ellipse(190, 196, 58, 30, nebula[1], 1, rot=-20)}</g>')
    rnd = random.Random(seed)
    for _ in range(16):
        x, y = rnd.uniform(32, 224), rnd.uniform(32, 224)
        r = rnd.choice([1.5, 1.8, 2.2])
        c = rnd.choice(["#ffffff", "#cfe6ff", "#ffe9c8", "#9fc4ff"])
        s.bg.append(circle(x, y, r, c, rnd.uniform(0.35, 0.8)))


def bolt(s, x1, y1, x2, y2, w, glow="#ff4a14", mid="#ff9a3c", core="#fff6dc", gscale=2.7):
    g = s.lin(x1, y1, x2, y2, [(0, glow, 0), (0.5, glow, 0.75), (1, glow, 1)])
    s.glow(line(x1, y1, x2, y2, g, w * gscale), w * 0.9)
    m = s.lin(x1, y1, x2, y2, [(0, mid, 0), (0.45, mid, 1), (1, mid, 1)])
    s.add(line(x1, y1, x2, y2, m, w))
    c = s.lin(x1, y1, x2, y2, [(0, core, 0.1), (0.4, core, 1), (1, core, 1)])
    s.add(line(x1, y1, x2, y2, c, w * 0.45))


def slug(s, x1, y1, x2, y2, w, glow="#ff4a14", mid="#ff9a3c", core="#fff6dc"):
    """Short solid blaster bolt (capsule), bright both ends."""
    s.glow(line(x1, y1, x2, y2, glow, w * 2.2), w * 0.8, 0.95)
    s.add(line(x1, y1, x2, y2, mid, w))
    s.add(line(x1, y1, x2, y2, core, w * 0.48))


def flare(s, cx, cy, r, c1="#ffe2b0", c2="#ff6a2a"):
    g = s.rad(cx, cy, r, [(0, "#ffffff", 1), (0.22, "#ffffff", 0.95), (0.5, c1, 0.6),
                          (0.8, c2, 0.28), (1, c2, 0)])
    s.add(circle(cx, cy, r, g))


def starburst(s, cx, cy, R, r, rot=0, color="#ffffff", glowc="#ffc58a", gl=4):
    p = star4(cx, cy, R, r, rot)
    s.glow(poly(p, glowc), gl, 0.9)
    s.add(poly(p, color))


def sparks(s, cx, cy, items, color, w=3.2, op=1.0):
    for a, r0, r1 in items:
        ra = math.radians(a)
        s.add(line(cx + r0 * math.cos(ra), cy + r0 * math.sin(ra),
                   cx + r1 * math.cos(ra), cy + r1 * math.sin(ra), color, w, op))


def mote(s, x, y, r, glowc="#ffd29a", core="#fff1d0"):
    s.glow(circle(x, y, r * 1.8, glowc), 2.5)
    s.add(circle(x, y, r, core))


def chassis(s, cx, cy, w=120, sc=1.0):
    """Top of an astromech barrel body seen from the side: curved steel cap with a blue panel band
    and a dark hatch the mount bracket rises out of. (cx, cy) is the hatch centre."""
    k = sc
    hw = w / 2 * k
    body = s.lin(cx - hw, 0, cx + hw, 0, [(0, "#1a2232"), (0.25, "#8d9db6"), (0.5, "#e2ecf8"),
                                          (0.75, "#8495ae"), (1, "#1a2232")])
    top = cy - 6 * k
    d = (f"M{cx - hw},{cy + 70 * k} L{cx - hw},{top + 16 * k} Q{cx},{top - 14 * k} {cx + hw},{top + 16 * k} "
         f"L{cx + hw},{cy + 70 * k} Z")
    s.add(path(d, body, "#05070c", 3.2))
    band = s.lin(cx - hw, 0, cx + hw, 0, [(0, "#0b2a5a"), (0.5, "#3f8cf0"), (1, "#0b2a5a")])
    bd = (f"M{cx - hw + 3},{top + 30 * k} Q{cx},{top + 2 * k} {cx + hw - 3},{top + 30 * k} "
          f"L{cx + hw - 3},{top + 46 * k} Q{cx},{top + 18 * k} {cx - hw + 3},{top + 46 * k} Z")
    s.add(path(bd, band, "#05070c", 2))
    s.add(path(f"M{cx - hw + 8},{top + 14 * k} Q{cx},{top - 9 * k} {cx + hw - 8},{top + 14 * k}",
               "none", "#f4f9ff", 2.6, 0.75))
    # hatch
    s.add(ellipse(cx, cy + 2 * k, 22 * k, 7 * k, "#04060b", 1, "#3a4558", 2))


def mount(s, px, py, ang, sc=1.0, hot=0.0, base=True, flash=True):
    """Swivel blaster mount: yoke bracket + pivot hub (unrotated) and the barrel rotated to `ang` degrees
    (0 = pointing +x). Returns the muzzle point."""
    k = sc
    if base:
        yk = s.lin(px - 20 * k, 0, px + 20 * k, 0, [(0, "#2a3446"), (0.4, "#b4c4da"), (1, "#283244")])
        s.add(poly([(px - 22 * k, py), (px + 22 * k, py), (px + 16 * k, py + 34 * k), (px - 16 * k, py + 34 * k)],
                   yk, "#05070c", 3))
        s.add(line(px - 8 * k, py + 8 * k, px - 6 * k, py + 30 * k, "#e6eefa", 2.2, 0.7))
    # barrel (local frame)
    o = []
    sg = s.lin(0, -14, 0, 14, [(0, "#eef4fc"), (0.3, "#a8b8d0"), (0.75, "#46536a"), (1, "#182030")])
    dg = s.lin(0, -18, 0, 18, [(0, "#b8c6da"), (0.4, "#5e6e86"), (1, "#141a26")])
    # rear housing / power cell
    o.append(f'<rect x="-34" y="-19" width="40" height="38" rx="9" fill="{dg}" stroke="#05070c" stroke-width="3.2"/>')
    o.append(f'<rect x="-27" y="-11" width="13" height="22" rx="3" fill="#ffb23a" stroke="#3a1a04" stroke-width="1.8"/>')
    o.append(f'<rect x="-25" y="-9" width="4" height="18" fill="#fff0c0" opacity="0.85"/>')
    # main barrel
    o.append(f'<rect x="0" y="-12" width="66" height="24" rx="4" fill="{sg}" stroke="#05070c" stroke-width="3.2"/>')
    # cooling ring
    o.append(f'<rect x="22" y="-16" width="12" height="32" rx="3" fill="{dg}" stroke="#05070c" stroke-width="2.6"/>')
    o.append(line(25, -12, 25, 12, "#e6eefa", 1.8, 0.6))
    # muzzle shroud
    o.append(f'<rect x="56" y="-17" width="24" height="34" rx="5" fill="{dg}" stroke="#05070c" stroke-width="3.2"/>')
    o.append(f'<rect x="59" y="-14" width="18" height="5" rx="2" fill="#dde8f6" opacity="0.85"/>')
    o.append(line(5, -6, 20, -6, "#ffffff", 3, 0.8))
    o.append(line(37, -6, 53, -6, "#ffffff", 3, 0.8))
    if hot > 0:
        o.append(f'<rect x="70" y="-8" width="12" height="16" rx="3" fill="#ff8a30" opacity="{0.5 + 0.5 * hot}"/>')
    s.add(f'<g transform="translate({px} {py}) rotate({ang}) scale({k})">{"".join(o)}</g>')
    # pivot hub
    hub = s.rad(px, py, 17 * k, [(0, "#f2f8ff"), (0.45, "#8a9bb4"), (1, "#1a2232")],
                fx=px - 5 * k, fy=py - 5 * k)
    s.add(circle(px, py, 16 * k, hub, 1, "#05070c", 3))
    s.add(circle(px, py, 6 * k, "#3fa8ff", 1, "#05070c", 1.8))
    ra = math.radians(ang)
    mx, my = px + 82 * k * math.cos(ra), py + 82 * k * math.sin(ra)
    if flash:
        pass
    return mx, my


def chevron(cx, cy, w, h, t):
    """Downward chevron polygon centred at cx, cy (tip below)."""
    return [(cx - w, cy - h), (cx, cy), (cx + w, cy - h), (cx + w, cy - h + t), (cx, cy + t), (cx - w, cy - h + t)]


# ------------------------------------------------------- 1 Suppressive Burst
def suppressive_burst(rank):
    e = (rank - 1) / 3.0
    s = Svg()
    background(s, 1101, nebula=("#6a2a5a", "#1a4a8a"))
    chassis(s, 68, 198, w=136)
    ang = -35
    mx, my = mount(s, 68, 166, ang, 1.05, hot=0.4 + 0.6 * e)
    d = (math.cos(math.radians(ang)), math.sin(math.radians(ang)))
    # three bolts in a tight burst along the line of fire
    for da, t0, t1 in [(-24, 30, 76), (0, 24, 88), (24, 30, 76)]:
        ra = math.radians(ang + da)
        x1, y1 = mx + math.cos(ra) * t0, my + math.sin(ra) * t0
        x2, y2 = mx + math.cos(ra) * t1, my + math.sin(ra) * t1
        bolt(s, x1, y1, x2, y2, 9 + 1.2 * e, gscale=1.5)
    flare(s, mx + d[0] * 2, my + d[1] * 2, 20 + 5 * e)
    starburst(s, mx + d[0] * 2, my + d[1] * 2, 24 + 5 * e, 5, rot=ang + 90)
    # ejected heat sparks around the muzzle, more per rank
    sp = [(ang - 70, 18, 30), (ang + 70, 18, 30), (ang - 115, 18, 28), (ang + 115, 18, 28), (ang + 180, 22, 32)]
    sparks(s, mx, my, sp[:1 + rank], "#ffd7a0", 2.8)
    extra = [(206, 132, 2.6), (128, 60, 2.4), (210, 48, 2.2)]
    for i in range(rank - 1):
        mote(s, *extra[i])
    return s


# ------------------------------------------------------- 2 Tracking Servos
def gear(cx, cy, R, r, teeth, rot=0):
    p = []
    for i in range(teeth * 4):
        a = math.radians(rot + i * 360 / (teeth * 4))
        rr = R if (i % 4) in (1, 2) else r
        p.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return p


def tracking_servos():
    s = Svg()
    background(s, 1202, nebula=("#1a4a9a", "#2a7a6a"))
    cx, cy = 118, 140
    # motion arc with arrowheads (sweeping path of the servo)
    arc = arc_pts(cx, cy, 86, 200, 330, 30)
    s.glow(polyline(arc, "#3fd0ff", 9), 4, 0.8)
    s.add(polyline(arc, "#0a3a5a", 8))
    s.add(polyline(arc, "#7fe4ff", 4.5))
    for a0, a1 in ((200, 186), (330, 344)):
        tip = (cx + 86 * math.cos(math.radians(a1)), cy + 86 * math.sin(math.radians(a1)))
        b = (cx + 86 * math.cos(math.radians(a0)), cy + 86 * math.sin(math.radians(a0)))
        dx, dy = tip[0] - b[0], tip[1] - b[1]
        L = math.hypot(dx, dy)
        nx, ny = -dy / L, dx / L
        head = [tip, (b[0] + nx * 10, b[1] + ny * 10), (b[0] - nx * 10, b[1] - ny * 10)]
        s.glow(poly(head, "#3fd0ff"), 3)
        s.add(poly(head, "#bff2ff", "#0a3a5a", 2))
    # tick marks along the arc
    for a in range(215, 330, 23):
        ra = math.radians(a)
        s.add(line(cx + 74 * math.cos(ra), cy + 74 * math.sin(ra), cx + 80 * math.cos(ra), cy + 80 * math.sin(ra),
                   "#9fe8ff", 2.6, 0.8))
    # big servo gear
    gg = s.rad(cx, cy, 60, [(0, "#ffffff"), (0.4, "#c2d0e4"), (0.8, "#6a7a94"), (1, "#2a3446")],
               fx=cx - 18, fy=cy - 20)
    s.add(poly(gear(cx, cy, 60, 45, 9, 7), gg, "#05070c", 3.2))
    s.add(circle(cx, cy, 36, s.lin(cx - 36, cy - 36, cx + 36, cy + 36,
                                   [(0, "#3a4860"), (0.5, "#7a8aa4"), (1, "#222c3e")]), 1, "#05070c", 2.5))
    # small meshing gear
    sx, sy = 184, 186
    sg = s.rad(sx, sy, 30, [(0, "#ffe6b0"), (0.4, "#d39a3a"), (1, "#4a2a0a")], fx=sx - 8, fy=sy - 8)
    s.add(poly(gear(sx, sy, 27, 20, 8, 12), sg, "#05070c", 2.8))
    s.add(circle(sx, sy, 7, "#2a1a08", 1, "#05070c", 2))
    s.add(circle(sx - 2, sy - 2, 2.4, "#fff0c8"))
    # barrel stub riding on the servo hub, pointing up-right
    ang = -40
    o = []
    sgb = s.lin(0, -12, 0, 12, [(0, "#e8f0fb"), (0.3, "#9aabc4"), (0.75, "#3a465a"), (1, "#141a26")])
    o.append(f'<rect x="0" y="-14" width="72" height="28" rx="4" fill="{sgb}" stroke="#05070c" stroke-width="3.2"/>')
    o.append(f'<rect x="58" y="-18" width="22" height="36" rx="5" fill="#5e6e86" stroke="#05070c" stroke-width="3.2"/>')
    o.append(line(6, -5, 52, -5, "#ffffff", 2.4, 0.75))
    s.add(f'<g transform="translate({cx} {cy}) rotate({ang})">{"".join(o)}</g>')
    hub = s.rad(cx, cy, 22, [(0, "#f2f8ff"), (0.5, "#7d8ea8"), (1, "#151b28")], fx=cx - 7, fy=cy - 7)
    s.add(circle(cx, cy, 20, hub, 1, "#05070c", 3))
    s.glow(circle(cx, cy, 8, "#3fd0ff"), 3)
    s.add(circle(cx, cy, 6.5, "#9fe8ff", 1, "#05070c", 1.5))
    s.add(circle(cx - 2, cy - 2, 2.2, "#ffffff"))
    return s


# ------------------------------------------------------- 3 Locked Barrage
def locked_barrage():
    s = Svg()
    background(s, 1303, nebula=("#7a3a1a", "#2a4a8a"))
    tx, ty = 170, 128  # convergence point
    # three stacked bolts converging from the left
    for sx, sy in [(40, 64), (34, 128), (40, 192)]:
        dx, dy = tx - sx, ty - sy
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        bolt(s, sx, sy, tx - ux * 34, ty - uy * 34, 12, gscale=2.0)
    # steel clamp ring with inward teeth, locked on the point
    R = 38
    cg = s.lin(tx - R, ty - R, tx + R, ty + R, [(0, "#eef4fc"), (0.45, "#8a9bb4"), (1, "#222c3e")])
    s.add(circle(tx, ty, R, "none", 1, "#05070c", 15))
    s.add(circle(tx, ty, R, "none", 1, cg, 10))
    s.add(path(f"M{tx - 30},{ty - 22} A{R},{R} 0 0 1 {tx + 10},{ty - 37}", "none", "#ffffff", 2.6, 0.8))
    for a in (0, 90, 180, 270):
        ra = math.radians(a + 45)
        ca, sa = math.cos(ra), math.sin(ra)
        tooth = [(tx + (R + 4) * ca - 7 * sa, ty + (R + 4) * sa + 7 * ca),
                 (tx + (R + 4) * ca + 7 * sa, ty + (R + 4) * sa - 7 * ca),
                 (tx + (R - 18) * ca, ty + (R - 18) * sa)]
        s.add(poly(tooth, "#ffb23a", "#2a1004", 2.4))
    flare(s, tx, ty, 30)
    starburst(s, tx, ty, 24, 5.5, rot=45)
    starburst(s, tx, ty, 15, 4, rot=0)
    return s


# ------------------------------------------------------- 4 Hobbling Bolt
def hobbling_bolt(rank):
    e = (rank - 1) / 2.0
    s = Svg()
    background(s, 1404, nebula=("#6a1a3a", "#5a3a1a"))
    ix, iy = 164, 178  # impact on the floor
    # floor deck plate in perspective
    fp = [(36, 168), (224, 168), (224, 222), (36, 222)]
    fg = s.lin(0, 168, 0, 222, [(0, "#6c7c94"), (0.4, "#384458"), (1, "#141a26")])
    s.add(poly(fp, fg, "#05070c", 3))
    s.add(line(38, 170, 222, 170, "#c8d6ea", 2.6, 0.8))
    for x in (76, 120, 210):
        s.add(line(x, 172, x - 10, 222, "#05070c", 2.2, 0.7))
    # broken stride: a forward double-chevron snapped and sagging
    cg = s.lin(0, 70, 0, 140, [(0, "#ffffff"), (0.5, "#bcd0ea"), (1, "#5a7aa8")])
    c1 = [(54, 80), (82, 80), (108, 108), (82, 136), (54, 136), (80, 108)]
    s.glow(poly(c1, "#4fa8ff"), 3, 0.6)
    s.add(poly(c1, cg, "#05070c", 3))
    # second chevron broken into two drooping halves
    up = [(100, 80), (128, 80), (146, 100), (118, 100)]
    dn = [(122, 122), (150, 122), (132, 148), (104, 148)]
    s.add(poly(up, cg, "#05070c", 3))
    s.add(poly(dn, "#7f93b2", "#05070c", 3, 0.9))
    s.add(polyline([(118, 100), (126, 106), (122, 112), (130, 118)], "#ffb070", 2.4, 0.9))
    # mount firing down from the upper right
    ang = 112
    mx, my = mount(s, 196, 60, ang, 1.0, hot=0.5 + 0.5 * e, base=False)
    bolt(s, mx, my, ix, iy - 4, 12 + 1.5 * e)
    # shatter mark on the deck
    cracks = [[(ix, iy), (138, 196), (128, 212)], [(ix, iy), (184, 198), (200, 214)],
              [(ix, iy), (160, 204), (166, 220)], [(ix, iy), (194, 182)], [(ix, iy), (132, 180)]]
    for c in cracks:
        s.add(polyline(c, "#05070c", 6))
    for c in cracks:
        s.glow(polyline(c, "#ff6a2a", 4.5), 2.5)
        s.add(polyline(c, "#ffe2b0", 2))
    flare(s, ix, iy, 22 + 5 * e)
    starburst(s, ix, iy, 24 + 5 * e, 5, rot=0)
    sp = [(-150, 18, 32), (-30, 18, 32), (-110, 18, 30), (-70, 18, 30)]
    sparks(s, ix, iy, sp[:1 + rank], "#ffd7a0", 2.8)
    return s


# ------------------------------------------------------- 5 Target Analysis
def target_analysis():
    s = Svg()
    background(s, 1505, nebula=("#1a5a8a", "#1a7a6a"))
    # holographic readout panel
    px0, py0, px1, py1 = 50, 56, 206, 196
    hg = s.lin(0, py0, 0, py1, [(0, "#1a6aa8", 0.55), (1, "#0a2a5a", 0.45)])
    s.glow(f'<rect x="{px0}" y="{py0}" width="{px1 - px0}" height="{py1 - py0}" rx="8" fill="none" '
           f'stroke="#3fd0ff" stroke-width="8"/>', 4, 0.8)
    s.add(f'<rect x="{px0}" y="{py0}" width="{px1 - px0}" height="{py1 - py0}" rx="8" fill="{hg}" '
          f'stroke="#9fe8ff" stroke-width="3.5"/>')
    # scan lines
    for y in range(py0 + 12, py1 - 4, 12):
        s.add(line(px0 + 6, y, px1 - 6, y, "#7fdcff", 1.4, 0.25))
    # rising bar readout on the left
    for i, h in enumerate((24, 38, 54, 72)):
        x = 64 + i * 15
        bgc = s.lin(0, 182 - h, 0, 182, [(0, "#eaffff"), (1, "#2a9ad0")])
        s.add(f'<rect x="{x}" y="{182 - h}" width="10" height="{h}" rx="2" fill="{bgc}" stroke="#05213a" stroke-width="1.8"/>')
    # lens / photoreceptor locked in corner brackets on the right
    lx, ly = 160, 106
    lg = s.rad(lx, ly, 26, [(0, "#ffffff"), (0.25, "#ffe08a"), (0.6, "#ff9a2a"), (1, "#5a1a04")],
               fx=lx - 7, fy=ly - 8)
    s.add(circle(lx, ly, 30, s.rad(lx, ly, 30, [(0, "#9aabc4"), (1, "#1a2232")]), 1, "#05070c", 3))
    s.glow(circle(lx, ly, 20, "#ffb23a"), 4, 0.8)
    s.add(circle(lx, ly, 20, lg, 1, "#2a1004", 2.5))
    s.add(circle(lx - 7, ly - 7, 4.5, "#ffffff", 0.9))
    R = 42
    for a in (45, 135, 225, 315):
        ra = math.radians(a)
        cxk, cyk = lx + R * math.cos(ra), ly + R * math.sin(ra)
        sxn = 1 if math.cos(ra) < 0 else -1
        syn = 1 if math.sin(ra) < 0 else -1
        br = [(cxk + sxn * 14, cyk), (cxk, cyk), (cxk, cyk + syn * 14)]
        s.add(polyline(br, "#05213a", 8))
        s.add(polyline(br, "#e6fbff", 4.4))
    # readout ticks under the lens
    for i, w in enumerate((40, 28)):
        s.add(f'<rect x="{lx - 20}" y="{164 + i * 12}" width="{w}" height="6" rx="2" fill="#bff2ff" opacity="0.85"/>')
    return s


# ------------------------------------------------------- 6 Sweeping Fire
def sweeping_fire(rank):
    e = rank - 1
    s = Svg()
    background(s, 1606, nebula=("#5a2a8a", "#1a5a7a"))
    px, py = 128, 168
    # sweep arc trail
    arc = arc_pts(px, py, 124, 207, 333, 30)
    s.glow(polyline(arc, "#ff6a2a", 10), 5, 0.55)
    s.add(polyline(arc, "#ffb070", 3, 0.6))
    # fan of bolts
    angs = [-150, -120, -90, -60, -30]
    for a in angs:
        ra = math.radians(a)
        x1, y1 = px + 92 * math.cos(ra), py + 92 * math.sin(ra)
        x2, y2 = px + 116 * math.cos(ra), py + 116 * math.sin(ra)
        slug(s, x1, y1, x2, y2, 9 + 1.5 * e)
    chassis(s, px, 196, w=150)
    mx, my = mount(s, px, py, -90, 0.95, hot=0.6 + 0.4 * e)
    flare(s, mx, my, 18 + 4 * e)
    starburst(s, mx, my, 20 + 4 * e, 4.5)
    if rank >= 2:
        for a in (-135, -105, -75, -45):
            ra = math.radians(a)
            mote(s, px + 106 * math.cos(ra), py + 106 * math.sin(ra), 2.8)
    return s


# ------------------------------------------------------- 7 Suppression Protocol
def stance_ring(s, cx, cy, R, w=7.5, gap=None):
    rg = s.lin(cx - R, cy - R, cx + R, cy + R, [(0, "#fff0c8"), (0.35, "#ffb23a"), (0.7, "#c86a10"), (1, "#ffd27a")])
    if gap is None:
        s.glow(circle(cx, cy, R, "none", 1, "#ffa030", w + 2), 3.5, 0.9)
        s.add(circle(cx, cy, R, "none", 1, "#1a0c02", w + 3.5))
        s.add(circle(cx, cy, R, "none", 1, rg, w))
        s.add(circle(cx, cy, R - w * 1.1, "none", 0.85, "#ffe2a0", 1.6))
    else:
        a0, a1 = gap
        p = arc_pts(cx, cy, R, a1, a0 + 360, 60)
        s.glow(polyline(p, "#ffa030", w + 2), 3.5, 0.9)
        s.add(polyline(p, "#1a0c02", w + 3.5))
        s.add(polyline(p, rg, w))
        s.add(polyline(arc_pts(cx, cy, R - w * 1.1, a1, a0 + 360, 60), "#ffe2a0", 1.6, 0.85))


def suppression_protocol(status=False):
    s = Svg()
    background(s, 1707 if not status else 1717, nebula=("#7a4a1a", "#2a4a8a"))
    if not status:
        cx, cy, R = 128, 128, 88
        s.add(circle(cx, cy, R, s.rad(cx, cy, R, [(0, "#2a1a0a"), (0.7, "#140c06"), (1, "#0a0604")]), 0.85))
        stance_ring(s, cx, cy, R)
        for a in (0, 90, 180, 270):
            ra = math.radians(a)
            s.add(line(cx + (R - 6) * math.cos(ra), cy + (R - 6) * math.sin(ra), cx + (R + 8) * math.cos(ra),
                       cy + (R + 8) * math.sin(ra), "#fff0c8", 5.5))
        # downward chevrons left and right of the mount (pressing the enemy down)
        cg = s.lin(0, 80, 0, 170, [(0, "#fff4d8"), (1, "#ff9a2a")])
        for x in (76, 180):
            for i, y in enumerate((104, 138)):
                s.add(poly(chevron(x, y, 22, 17, 11), cg, "#1a0c02", 2.6, 1 - 0.25 * i))
        chassis(s, cx, 188, w=100, sc=0.8)
        mx, my = mount(s, cx, 156, -90, 0.95, hot=0.6)
        flare(s, mx, my, 14)
    else:
        # ring alone (gap at the lower right for the rank badge), smaller mount inside
        cx, cy, R = 116, 116, 76
        s.add(circle(cx, cy, R, s.rad(cx, cy, R, [(0, "#2a1a0a"), (0.7, "#140c06"), (1, "#0a0604")]), 0.85))
        stance_ring(s, cx, cy, R, w=8, gap=(20, 70))
        cg = s.lin(0, 80, 0, 170, [(0, "#fff4d8"), (1, "#ff9a2a")])
        for i, y in enumerate((98, 124)):
            s.add(poly(chevron(cx, y, 22, 16, 10), cg, "#1a0c02", 2.4, 1 - 0.25 * i))
        mx, my = mount(s, cx, 160, -90, 0.55, hot=0.6, base=False)
        flare(s, mx, my, 9)
        s.add(f'<rect x="{cx - 22}" y="{170}" width="44" height="10" rx="3" fill="#8495ae" stroke="#05070c" stroke-width="2"/>')
    return s


# ------------------------------------------------------- 8 Overwatch Subroutine
def overwatch_subroutine():
    s = Svg()
    background(s, 1808, nebula=("#1a3a8a", "#1a6a5a"))
    ex, ey = 128, 176
    # covered cone
    a0, a1 = -140, -40
    R = 140
    cone = [(ex, ey)] + arc_pts(ex, ey, R, a0, a1, 30)
    cg = s.rad(ex, ey, R, [(0, "#3fd0ff", 0.55), (0.7, "#1a7ac8", 0.25), (1, "#0a3a7a", 0.1)])
    s.add(poly(cone, cg))
    for a in (a0, a1):
        ra = math.radians(a)
        s.glow(line(ex, ey, ex + R * math.cos(ra), ey + R * math.sin(ra), "#3fd0ff", 6), 3, 0.8)
        s.add(line(ex, ey, ex + R * math.cos(ra), ey + R * math.sin(ra), "#bff2ff", 2.8))
    for r in (60, 96):
        s.add(polyline(arc_pts(ex, ey, r, a0, a1, 20), "#9fe8ff", 2.2, 0.7))
    s.add(line(ex, ey, ex, ey - 120, "#9fe8ff", 1.6, 0.45))
    # a suppressed contact blip inside the arc
    bx, by = 160, 82
    s.glow(circle(bx, by, 9, "#ff8a30"), 3.5)
    s.add(circle(bx, by, 5.5, "#ffe2b0"))
    s.add(circle(bx, by, 12, "none", 0.9, "#ffb070", 2.4))
    # dome sensor head
    dg = s.rad(ex, ey, 46, [(0, "#f0f6ff"), (0.35, "#aebfd6"), (0.75, "#4c5b74"), (1, "#18202e")],
               fx=ex - 14, fy=ey - 20)
    d = f"M{ex - 50},{ey + 30} L{ex - 50},{ey + 4} A50,46 0 0 1 {ex + 50},{ey + 4} L{ex + 50},{ey + 30} Z"
    s.add(path(d, dg, "#05070c", 3.5))
    s.add(path(f"M{ex - 46},{ey + 14} L{ex + 46},{ey + 14}", "none", "#2a6ad0", 5, 0.9))
    s.add(path(f"M{ex - 30},{ey - 26} Q{ex - 10},{ey - 40} {ex + 12},{ey - 38}", "none", "#f4f9ff", 3, 0.75))
    # the eye
    s.add(circle(ex, ey - 12, 14, "#0a0e18", 1, "#05070c", 2.5))
    s.glow(circle(ex, ey - 12, 10, "#3fd0ff"), 4)
    s.add(circle(ex, ey - 12, 8, s.rad(ex, ey - 12, 8, [(0, "#ffffff"), (0.4, "#9fe8ff"), (1, "#1a6ac8")])))
    s.add(circle(ex - 3, ey - 15, 2.4, "#ffffff"))
    # side panel lights
    for x, c in ((ex - 32, "#ff5a3c"), (ex + 30, "#3fd0ff")):
        s.add(f'<rect x="{x - 5}" y="{ey - 2}" width="10" height="8" rx="2" fill="{c}" stroke="#05070c" stroke-width="1.6"/>')
    return s


# ------------------------------------------------------- 9 Containment Pattern
def containment_pattern():
    s = Svg()
    background(s, 1909, nebula=("#7a3a1a", "#3a2a7a"))
    cx, cy = 128, 128
    R = 74
    nodes = [(cx + R * math.cos(math.radians(-90 + i * 60)), cy + R * math.sin(math.radians(-90 + i * 60)))
             for i in range(6)]
    # inner cage grid
    s.add(poly(nodes, s.rad(cx, cy, R, [(0, "#3a1a0a", 0.6), (1, "#1a0a04", 0.85)])))
    for i in range(3):
        a, b = nodes[i], nodes[i + 3]
        s.glow(line(a[0], a[1], b[0], b[1], "#ff8a30", 5), 2.5, 0.5)
        s.add(line(a[0], a[1], b[0], b[1], "#ffc890", 2, 0.65))
    # cage edges as bolts between impacts
    for i in range(6):
        a, b = nodes[i], nodes[(i + 1) % 6]
        bolt(s, a[0], a[1], b[0], b[1], 9, gscale=2.0)
    # contained target core, dimmed (deals less damage)
    cg = s.rad(cx, cy, 22, [(0, "#d8c0ff"), (0.5, "#6a4aa8"), (1, "#1a0a3a")], fx=cx - 6, fy=cy - 6)
    s.add(circle(cx, cy, 22, cg, 1, "#05070c", 3))
    s.add(poly(chevron(cx, cy + 10, 12, 10, 6), "#ffe2b0", "#2a0c02", 1.8))
    s.add(poly(chevron(cx, cy - 2, 12, 10, 6), "#ffe2b0", "#2a0c02", 1.8, 0.7))
    # impact bursts at the nodes
    for x, y in nodes:
        flare(s, x, y, 15)
        starburst(s, x, y, 15, 3.6, rot=45)
    return s


# ------------------------------------------------------- 10 Designate Target
def diamond_marker(s, dx, dy, R, cracked=False):
    dm = [(dx, dy - R), (dx + R, dy), (dx, dy + R), (dx - R, dy)]
    s.glow(poly(dm, "none", "#4aff8a", 10), 4, 0.9)
    s.add(poly(dm, "none", "#03200c", 10))
    s.add(poly(dm, "none", s.lin(dx - R, dy - R, dx + R, dy + R,
                                 [(0, "#eaffea"), (0.5, "#5aff8a"), (1, "#1aa84a")]), 6.5))
    inner = [(dx, dy - R * 0.45), (dx + R * 0.45, dy), (dx, dy + R * 0.45), (dx - R * 0.45, dy)]
    s.glow(poly(inner, "#4aff8a"), 3)
    s.add(poly(inner, "#d8ffe0", "#03200c", 2))
    # lock ticks on the four points
    for ux, uy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        s.add(line(dx + ux * (R + 6), dy + uy * (R + 6), dx + ux * (R + 16), dy + uy * (R + 16), "#bfffd0", 4.5))
    if cracked:
        c = [(dx - 4, dy - R * 0.45 - 2), (dx + 3, dy - 6), (dx - 3, dy + 4), (dx + 4, dy + R * 0.45 + 2)]
        s.add(polyline(c, "#03200c", 4))


def designate_target(status=False):
    s = Svg()
    if not status:
        background(s, 2010, nebula=("#1a5a3a", "#2a3a7a"))
        dx, dy = 158, 96
        # designator emitter (small mount) lower-left
        chassis(s, 64, 200, w=100, sc=0.8)
        ang = math.degrees(math.atan2(dy - 172, dx - 64))
        mx, my = mount(s, 64, 172, ang, 0.8, base=True)
        # beam
        s.glow(line(mx, my, dx, dy, "#4aff8a", 12), 5, 0.9)
        s.add(line(mx, my, dx, dy, "#7affa8", 5))
        s.add(line(mx, my, dx, dy, "#f4fff4", 2))
        flare(s, mx, my, 12, c1="#d8ffe0", c2="#2aff6a")
        diamond_marker(s, dx, dy, 34)
        # area pulse: the 8 m burst
        s.add(circle(dx, dy, 56, "none", 0.55, "#7affa8", 2.2))
        flare(s, dx, dy, 14, c1="#eaffea", c2="#2aff6a")
    else:
        background(s, 2020, nebula=("#1a5a3a", "#2a3a7a"))
        dx, dy = 112, 108
        # target bracket around the marker
        R = 72
        for a in (45, 135, 225):
            ra = math.radians(a)
            cxk, cyk = dx + R * math.cos(ra), dy + R * math.sin(ra)
            sxn = 1 if math.cos(ra) < 0 else -1
            syn = 1 if math.sin(ra) < 0 else -1
            br = [(cxk + sxn * 18, cyk), (cxk, cyk), (cxk, cyk + syn * 18)]
            s.add(polyline(br, "#03200c", 9))
            s.add(polyline(br, "#bfffd0", 5))
        diamond_marker(s, dx, dy, 44, cracked=True)
        # descending evasion chevrons beside it
        for i, y in enumerate((70, 92)):
            s.add(poly(chevron(186, y, 14, 11, 7), "#ffd27a", "#2a1a02", 2, 1 - 0.25 * i))
    return s


# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 5):
    JOBS.append((f"ife_am_supburst{r}", "supburst", lambda r=r: suppressive_burst(r)))
JOBS.append(("ife_am_trkservo", "trkservo", tracking_servos))
JOBS.append(("ife_am_lockbarr", "lockbarr", locked_barrage))
for r in range(1, 4):
    JOBS.append((f"ife_am_hobbolt{r}", "hobbolt", lambda r=r: hobbling_bolt(r)))
JOBS.append(("ife_am_tgtanal", "tgtanal", target_analysis))
for r in range(1, 3):
    JOBS.append((f"ife_am_swpfire{r}", "swpfire", lambda r=r: sweeping_fire(r)))
JOBS.append(("ife_am_supproto", "supproto", suppression_protocol))
JOBS.append(("ife_am_ovwsub", "ovwsub", overwatch_subroutine))
JOBS.append(("ife_am_contpat", "contpat", containment_pattern))
JOBS.append(("ife_am_desigtgt", "desigtgt", designate_target))

STATUS_JOBS = [("ief_am_supproto", lambda: suppression_protocol(status=True)),
               ("ief_am_desigtgt", lambda: designate_target(status=True))]


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
    statuses = {}
    for resref, fn in STATUS_JOBS:
        svg = fn()
        sp = f"{SRC}/{resref}.svg"
        open(sp, "w", encoding="utf-8").write(svg.render(256))
        rp = f"{RENDER}/{resref}.png"
        magick_render(sp, rp)
        im = Image.open(rp).convert("RGB")
        assert im.size == (256, 256)
        im.save(f"{STATUS}/{resref}.png")
        statuses[resref] = finalize(im)
    # uniqueness check across every final icon (and the status previews)
    allf = dict(finals)
    allf.update(statuses)
    keys = list(allf)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert list(allf[keys[i]].getdata()) != list(allf[keys[j]].getdata()), (keys[i], keys[j])
    # contact sheet (256 per cell, nearest from 32x32)
    rows = [[f"ife_am_supburst{i}" for i in range(1, 5)],
            ["ife_am_hobbolt1", "ife_am_hobbolt2", "ife_am_hobbolt3", "ife_am_trkservo"],
            ["ife_am_swpfire1", "ife_am_swpfire2", "ife_am_lockbarr", "ife_am_tgtanal"],
            ["ife_am_supproto", "ife_am_ovwsub", "ife_am_contpat", "ife_am_desigtgt"],
            ["ief_am_supproto", "ief_am_desigtgt"]]
    cell, pad = 256, 16
    W = pad + 4 * (cell + pad)
    H = pad + len(rows) * (cell + pad)
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    for ri, row in enumerate(rows):
        for ci, rr in enumerate(row):
            sheet.paste(allf[rr].resize((cell, cell), Image.NEAREST), (pad + ci * (cell + pad), pad + ri * (cell + pad)))
    sheet.save(HERE + "/contact_center.png")
    # 512 previews montage (first rank of each family + statuses)
    names = fams
    cols = 4
    mrows = (len(names) + 2 + cols - 1) // cols
    mont = Image.new("RGB", (384 * cols, 384 * mrows), (128, 128, 128))
    for i, f in enumerate(names):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB").resize((384, 384), Image.LANCZOS),
                   ((i % cols) * 384, (i // cols) * 384))
    for j, (resref, _) in enumerate(STATUS_JOBS):
        i = len(names) + j
        mont.paste(Image.open(f"{STATUS}/{resref}.png").resize((384, 384), Image.LANCZOS),
                   ((i % cols) * 384, (i // cols) * 384))
    mont.save(HERE + "/previews_512.png")
    print("ok", len(finals), "icons,", len(statuses), "status")


if __name__ == "__main__":
    main()
