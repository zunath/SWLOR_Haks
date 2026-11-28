"""Astromech Utility Systems - Repair ability-icon CENTER ARTWORK generator.

Same pipeline as ../../ace/build_ace_icons.py: illustrated SVG (src/), ImageMagick
render, 32x32 LANCZOS downsample, outer 3 px ring flattened to the dark background,
out_png/, out_tga/ (32-bit, bottom-left origin, descriptor 8), contact sheets,
512 px previews, and status_png/ subjects for the status-effect import.

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
for d in (SRC, RENDER, OUT_PNG, OUT_TGA, PREV):
    os.makedirs(d, exist_ok=True)

STATUS_PNG = HERE + "/status_png"
os.makedirs(STATUS_PNG, exist_ok=True)
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


def bg(s, seed, nebula):
    background(s, seed, nebula=nebula)


def outline_glow(s, shape, color, std, op=0.6):
    s.glow(shape, std, op)


def arc_path(cx, cy, r, a0, a1):
    """SVG arc from angle a0 to a1 (degrees, clockwise positive)."""
    x0, y0 = cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0))
    x1, y1 = cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1))
    large = 1 if (a1 - a0) % 360 > 180 else 0
    return f"M{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f}"


def gear_points(cx, cy, ro, ri, n, rot=0, tw=0.42):
    p = []
    for i in range(n):
        base = math.radians(rot) + i * 2 * math.pi / n
        step = 2 * math.pi / n
        for f, r in ((0, ri), (0.5 - tw / 2 - 0.06, ri), (0.5 - tw / 2, ro), (0.5 + tw / 2, ro),
                     (0.5 + tw / 2 + 0.06, ri)):
            a = base + f * step
            p.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return p


def zig(x1, y1, x2, y2, n, amp, seed):
    rnd = random.Random(seed)
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    nx, ny = -dy / L, dx / L
    p = [(x1, y1)]
    for i in range(1, n):
        t = i / n
        o = amp * (1 if i % 2 else -1) * rnd.uniform(0.6, 1.0)
        p.append((x1 + dx * t + nx * o, y1 + dy * t + ny * o))
    p.append((x2, y2))
    return p


def arc_bolt(s, p, glowc="#3aa8ff", mid="#9ae8ff", w=3.0):
    s.glow(polyline(p, glowc, w * 2.6), w * 1.2)
    s.add(polyline(p, mid, w))
    s.add(polyline(p, "#ffffff", w * 0.42, 0.95))


def mote(s, x, y, r, c="#7dffc8", core="#f2fff8"):
    s.glow(circle(x, y, r * 2.0, c), r * 0.9, 0.9)
    s.add(circle(x, y, r, c))
    s.add(circle(x - r * 0.25, y - r * 0.25, r * 0.5, core))


GREEN_NEB = ("#1a6a5a", "#1a4a8a")


# ------------------------------------------------------------- Field Repair
def field_repair(rank):
    e = (rank - 1) / 3.0
    s = Svg()
    bg(s, 611, ("#2a5a8a", "#6a3a1a"))
    # hull plate, lower half, slight tilt
    plate = [(40, 132), (206, 108), (220, 200), (52, 222)]
    s.add(poly([(x + 4, y + 6) for x, y in plate], "#02040a", op=0.6))
    pg = s.lin(40, 108, 220, 222, [(0, "#c3d2e6"), (0.4, "#6c7c96"), (1, "#1e2636")])
    s.add(poly(plate, pg, "#05070c", 4))
    s.add(line(44, 136, 202, 113, "#eef5ff", 2.6, 0.85))
    # seam across the plate: dark gap to the right, glowing weld bead to the left
    seam_l, weld, seam_r = (48, 178), (134, 166), (214, 154)
    s.add(line(*weld, *seam_r, "#05070c", 7))
    s.add(line(*weld, *seam_r, "#2a3240", 2.2, 0.8))
    s.glow(line(*seam_l, *weld, "#ff7a20", 12), 4, 0.9)
    s.add(line(*seam_l, *weld, "#ff9a3c", 8))
    for i in range(7):  # weld ripples
        t = (i + 0.5) / 7
        x, y = seam_l[0] + (weld[0] - seam_l[0]) * t, seam_l[1] + (weld[1] - seam_l[1]) * t
        s.add(ellipse(x, y, 4.5, 3.2, "#ffe0a0", 0.85, rot=-8))
    for rx, ry in [(60, 144), (192, 122), (68, 206), (200, 188)]:
        s.add(circle(rx, ry, 4.2, "#8a98ae", 1, "#05070c", 1.5))
        s.add(circle(rx - 1, ry - 1.2, 1.5, "#f0f6ff"))
    # torch arm: joint at top right, body, nozzle
    jx, jy = 202, 52
    d = (-0.512, 0.859)
    L = math.hypot(*d)
    d = (d[0] / L, d[1] / L)

    def at(t):
        return (jx + d[0] * t, jy + d[1] * t)

    a, b = at(4), at(78)
    bodyg = s.lin(a[0] + 14, a[1] + 10, a[0] - 14, a[1] - 10,
                  [(0, "#20283a"), (0.45, "#b9c8dc"), (0.7, "#6a7a94"), (1, "#151b28")])
    s.add(poly(rect_line(a[0], a[1], b[0], b[1], 38), bodyg, "#05070c", 3.5))
    for t in (26, 54):
        p0, p1 = at(t), at(t + 8)
        s.add(poly(rect_line(p0[0], p0[1], p1[0], p1[1], 42), "#d89a3a", "#05070c", 2.5))
        s.add(line(*at(t + 2), *at(t + 2), "#ffe2a8", 1))
    a2, b2 = at(16), at(74)
    s.add(line(a2[0] - 11, a2[1] - 7, b2[0] - 11, b2[1] - 7, "#f2f8ff", 3, 0.75))
    n0, n1 = at(78), at(110)
    s.add(poly(rect_line(n0[0], n0[1], n1[0], n1[1], 19), "#3c475a", "#05070c", 3))
    s.add(line(n0[0] - 4, n0[1] - 3, n1[0] - 4, n1[1] - 3, "#c8d6ea", 2, 0.8))
    tip = at(112)
    jg = s.rad(jx - 6, jy - 6, 26, [(0, "#eef5ff"), (0.5, "#7e8ea8"), (1, "#1a2130")])
    s.add(circle(jx, jy, 23, jg, 1, "#05070c", 3.5))
    s.add(circle(jx, jy, 8, "#2a3344", 1, "#05070c", 2))
    s.add(circle(jx, jy, 3.5, "#8fe3ff"))
    # flame cone tip -> weld point
    fl = [(tip[0] - 6, tip[1] - 2), (tip[0] + 5, tip[1] + 3), (weld[0] + 1, weld[1] - 1)]
    s.glow(poly(fl, "#4fa8ff"), 3, 0.9)
    s.add(poly(fl, "#bfe8ff"))
    # weld point
    flare(s, weld[0], weld[1], 26 + 6 * e, c1="#fff0c0", c2="#ff7a20")
    starburst(s, weld[0], weld[1], 26 + 5 * e, 5, rot=10, color="#ffffff", glowc="#ffb060")
    sp = [(-100, 22, 44), (-60, 22, 40), (-140, 20, 40), (-20, 22, 36), (-165, 20, 34), (-80, 30, 56),
          (-120, 30, 52)]
    sparks(s, weld[0], weld[1], sp[:3 + rank], "#ffd28a", 3.2)
    extra = [(96, 118, 3), (150, 116, 2.6), (84, 146, 2.6), (118, 98, 2.4)]
    for i in range(rank):
        x, y, r = extra[i]
        s.glow(circle(x, y, r * 1.8, "#ffb060"), 2.5)
        s.add(circle(x, y, r, "#fff1d0"))
    return s


# ------------------------------------------------------------- System Purge
def system_purge(rank):
    e = (rank - 1) / 2.0
    s = Svg()
    bg(s, 622, ("#1a5a7a", "#4a2a7a"))
    cx, cy, h = 128, 130, 60
    if rank >= 3:  # resistance shell
        hexp = [(cx + 92 * math.cos(math.radians(60 * i + 30)), cy + 92 * math.sin(math.radians(60 * i + 30)))
                for i in range(6)]
        s.glow(poly(hexp, "none", "#4fd8ff", 7), 3.5, 0.8)
        s.add(poly(hexp, "none", "#bff2ff", 3))
    # pins
    for i in range(5):
        o = -40 + i * 20
        for (x1, y1, x2, y2) in [(cx + o, cy - h - 16, cx + o, cy - h), (cx + o, cy + h, cx + o, cy + h + 16),
                                 (cx - h - 16, cy + o, cx - h, cy + o), (cx + h, cy + o, cx + h + 16, cy + o)]:
            s.add(line(x1, y1, x2, y2, "#05070c", 10, cap="butt"))
            s.add(line(x1, y1, x2, y2, "#c9a24a", 6, cap="butt"))
    # body
    body = s.lin(cx - h, cy - h, cx + h, cy + h, [(0, "#4a566c"), (0.5, "#1e2636"), (1, "#0c111c")])
    s.add(f'<rect x="{cx - h}" y="{cy - h}" width="{2 * h}" height="{2 * h}" rx="8" fill="{body}" '
          f'stroke="#05070c" stroke-width="4"/>')
    s.add(f'<rect x="{cx - h + 5}" y="{cy - h + 5}" width="{2 * h - 10}" height="{2 * h - 10}" rx="5" '
          f'fill="none" stroke="#7d8ea8" stroke-width="1.6" opacity="0.7"/>')
    # die with traces; the sweep line runs at x = sx (left side still dirty)
    sx = 116
    traces = [[(cx - 36, cy - 28), (cx - 14, cy - 28), (cx - 6, cy - 18), (cx + 34, cy - 18)],
              [(cx - 36, cy), (cx + 36, cy)],
              [(cx - 36, cy + 26), (cx + 4, cy + 26), (cx + 14, cy + 16), (cx + 34, cy + 16)],
              [(cx + 20, cy - 38), (cx + 20, cy - 18)], [(cx - 20, cy + 26), (cx - 20, cy + 40)]]
    clip_r = s.uid("c")
    clip_l = s.uid("c")
    s.defs.append(f'<clipPath id="{clip_r}"><rect x="{sx}" y="0" width="256" height="256"/></clipPath>')
    s.defs.append(f'<clipPath id="{clip_l}"><rect x="0" y="0" width="{sx}" height="256"/></clipPath>')
    clean = "".join(polyline(t, "#9ff0ff", 4.6) for t in traces)
    dirty = "".join(polyline(t, "#4a5a3a", 4.6) for t in traces)
    s.add(f'<g clip-path="url(#{clip_r})"><g filter="{s.blur(2.5)}" opacity="{0.8 + 0.2 * e}">'
          f'{"".join(polyline(t, "#2ac8ff", 9) for t in traces)}</g>{clean}</g>')
    s.add(f'<g clip-path="url(#{clip_l})">{dirty}</g>')
    for x, y in [(cx + 34, cy - 18), (cx + 36, cy), (cx + 34, cy + 16), (cx + 20, cy - 38)]:
        s.add(circle(x, y, 4, "#e8fdff"))
    # contamination blots on the dirty side
    for x, y, r, c in [(cx - 30, cy - 14, 9, "#7ad83a"), (cx - 18, cy + 14, 7, "#b04ad8"),
                       (cx - 36, cy + 36, 6, "#7ad83a"), (cx - 26, cy - 38, 5, "#b04ad8")]:
        s.add(circle(x, y, r + 1.5, "#05070c", 0.7))
        s.add(circle(x, y, r, c, 0.95))
        s.add(circle(x - r * 0.3, y - r * 0.35, r * 0.35, "#f0ffe0", 0.8))
    # flushed droplets leaving to the left
    for x, y, r in [(52, 112, 5.5), (40, 140, 4.5), (56, 170, 4)]:
        s.add(circle(x, y, r, "#8ad84a" if r > 4.4 else "#b04ad8", 0.9))
    # sweep beam
    sw = s.lin(sx - 18, 0, sx + 6, 0, [(0, "#4fd8ff", 0), (0.7, "#7fe6ff", 0.55), (1, "#ffffff", 1)])
    s.add(f'<rect x="{sx - 26}" y="{cy - h - 22}" width="30" height="{2 * h + 44}" fill="{sw}"/>')
    s.glow(line(sx, cy - h - 24, sx, cy + h + 24, "#7fe6ff", 9), 4, 0.95)
    s.add(line(sx, cy - h - 24, sx, cy + h + 24, "#ffffff", 4))
    for y in (cy - h - 24, cy + h + 24):
        starburst(s, sx, y, 13 + 3 * e, 3.5, color="#ffffff", glowc="#7fe6ff")
    # wake streaks behind the beam
    for y, L in [(cy - 36, 30), (cy + 6, 40), (cy + 44, 26)][:1 + rank]:
        s.add(line(sx + 10, y, sx + 10 + L, y, "#dffaff", 2.2, 0.55))
    return s


# ------------------------------------------------------------- Precision Tooling
def precision_tooling():
    s = Svg()
    bg(s, 633, ("#2a4a8a", "#1a6a7a"))
    cx, cy = 128, 128
    hexp = [(cx + 94 * math.cos(math.radians(60 * i)), cy + 94 * math.sin(math.radians(60 * i)))
            for i in range(6)]
    hg = s.lin(40, 40, 216, 216, [(0, "#5a6a84"), (0.5, "#232c3e"), (1, "#0e131e")])
    s.add(poly(hexp, hg, "#05070c", 4))
    hexi = [(cx + 84 * math.cos(math.radians(60 * i)), cy + 84 * math.sin(math.radians(60 * i)))
            for i in range(6)]
    s.add(poly(hexi, "none", "#c9a24a", 3, 0.9))
    s.add(path(f"M{hexi[3][0]:.1f},{hexi[3][1]:.1f} L{hexi[4][0]:.1f},{hexi[4][1]:.1f} "
               f"L{hexi[5][0]:.1f},{hexi[5][1]:.1f}", "none", "#ffe2a0", 2, 0.7))
    # tool head hub upper right; long probe to lower left
    hx, hy = 156, 98
    tx, ty = 78, 178
    # secondary bits
    s.add(poly(rect_line(hx, hy, hx - 30, hy - 30, 11), "#7a889e", "#05070c", 2.5))
    s.add(poly([(hx - 36, hy - 24), (hx - 24, hy - 36), (hx - 40, hy - 42)], "#c8d6ea", "#05070c", 2))
    s.add(poly(rect_line(hx, hy, hx + 34, hy + 22, 11), "#7a889e", "#05070c", 2.5))
    s.add(path(f"M{hx + 32},{hy + 20} q12,4 8,16 q-4,6 -10,2", "none", "#05070c", 7))
    s.add(path(f"M{hx + 32},{hy + 20} q12,4 8,16 q-4,6 -10,2", "none", "#c8d6ea", 3.5))
    # main probe shaft (tapered)
    ang = math.atan2(ty - hy, tx - hx)
    nx, ny = -math.sin(ang), math.cos(ang)
    w0 = 16
    shaft = [(hx + nx * w0, hy + ny * w0), (hx - nx * w0, hy - ny * w0), (tx, ty)]
    sg = s.lin(hx + nx * 14, hy + ny * 14, hx - nx * 14, hy - ny * 14,
               [(0, "#1a2130"), (0.4, "#e4eefa"), (0.65, "#8a9ab4"), (1, "#1a2130")])
    s.add(poly(shaft, sg, "#05070c", 3))
    for t in (0.22, 0.34):
        px, py = hx + (tx - hx) * t, hy + (ty - hy) * t
        ww = w0 * (1 - t) + 3
        s.add(line(px + nx * ww, py + ny * ww, px - nx * ww, py - ny * ww, "#c9a24a", 4))
    # hub
    hub = s.rad(hx - 8, hy - 8, 34, [(0, "#f2f8ff"), (0.4, "#8ea0bc"), (1, "#1a2130")])
    s.add(circle(hx, hy, 27, hub, 1, "#05070c", 4))
    s.add(circle(hx, hy, 17, "none", 1, "#2a3344", 3))
    for k in range(6):
        a = math.radians(k * 60 + 15)
        s.add(circle(hx + 21.5 * math.cos(a), hy + 21.5 * math.sin(a), 2.2, "#1a2130"))
    s.glow(circle(hx, hy, 8, "#4fd8ff"), 3, 0.9)
    s.add(circle(hx, hy, 6, "#bff2ff"))
    # reticle around the tip
    s.glow(circle(tx, ty, 22, "none", 1, "#4fd8ff", 4), 2.5, 0.8)
    s.add(circle(tx, ty, 22, "none", 1, "#bff2ff", 2.4))
    for a in (0, 90, 180, 270):
        ra = math.radians(a)
        s.add(line(tx + 15 * math.cos(ra), ty + 15 * math.sin(ra), tx + 29 * math.cos(ra),
                   ty + 29 * math.sin(ra), "#eafcff", 3))
    s.glow(circle(tx, ty, 7, "#7fe6ff"), 3)
    s.add(poly(star4(tx, ty, 11, 2.4), "#ffffff"))
    return s


# ------------------------------------------------------------- Sealant Foam
def sealant_foam():
    s = Svg()
    bg(s, 644, ("#3a5a2a", "#2a4a7a"))
    # dark hull plate behind with a hot crack
    plate = [(44, 128), (214, 112), (214, 214), (44, 214)]
    pg = s.lin(44, 112, 214, 214, [(0, "#4a566c"), (0.6, "#222a3a"), (1, "#10151f")])
    s.add(poly(plate, pg, "#05070c", 4))
    s.add(line(48, 131, 210, 116, "#aab8cc", 2.2, 0.7))
    crack = [(52, 182), (76, 170), (92, 184), (118, 164), (142, 180), (166, 160), (186, 174), (208, 150)]
    s.add(polyline(crack, "#05070c", 9))
    s.glow(polyline(crack, "#ff4a1a", 6), 3, 0.95)
    s.add(polyline(crack, "#ffb070", 2.4))
    # foam mass over the middle of the crack
    blobs = [(108, 172, 17), (130, 166, 19), (152, 172, 17), (120, 186, 14), (142, 188, 15), (98, 186, 11),
             (166, 182, 12), (126, 150, 12), (148, 152, 11), (174, 166, 10)]
    for x, y, r in blobs:
        s.add(circle(x + 1.5, y + 3, r + 2.5, "#05070c", 0.7))
    fg = s.rad(124, 150, 70, [(0, "#ffffff"), (0.45, "#f4ecd2"), (1, "#b8ad88")])
    for x, y, r in blobs:
        s.add(circle(x, y, r, fg, 1, "#6a6248", 1.2))
    for x, y, r in blobs:
        s.add(circle(x - r * 0.35, y - r * 0.4, r * 0.3, "#ffffff", 0.9))
    # canister upper left with nozzle aiming at the foam
    d = (0.64, 0.77)
    L = math.hypot(*d)
    d = (d[0] / L, d[1] / L)
    ox, oy = 62, 50

    def at(t):
        return (ox + d[0] * t, oy + d[1] * t)

    a, b = at(0), at(62)
    cg = s.lin(a[0] + 22, a[1] - 16, a[0] - 22, a[1] + 16,
               [(0, "#14202a"), (0.4, "#e8fbff"), (0.7, "#6ab0c0"), (1, "#0e1a24")])
    s.add(poly(rect_line(a[0], a[1], b[0], b[1], 42), cg, "#05070c", 3.5))
    p0, p1 = at(22), at(36)
    s.add(poly(rect_line(p0[0], p0[1], p1[0], p1[1], 44), "#3ad88a", "#05070c", 2.5))
    s.add(ellipse(a[0], a[1], 21, 8, "#c8f0ff", 1, "#05070c", 3, rot=math.degrees(math.atan2(d[1], d[0])) + 90))
    n0, n1 = at(62), at(84)
    s.add(poly(rect_line(n0[0], n0[1], n1[0], n1[1], 18), "#5a6a80", "#05070c", 3))
    m0, m1 = at(84), at(98)
    s.add(poly(rect_line(m0[0], m0[1], m1[0], m1[1], 10), "#2a3446", "#05070c", 2.5))
    tip = at(100)
    # spray
    for t, off, r in [(112, 0, 6), (124, -6, 7), (122, 8, 5.5), (136, 2, 8), (134, -12, 5)]:
        x, y = ox + d[0] * t - d[1] * off, oy + d[1] * t + d[0] * off
        s.add(circle(x, y, r, "#f8f2dc", 0.95, "#6a6248", 1))
    s.glow(circle(tip[0], tip[1], 6, "#ffffff"), 2.5, 0.8)
    return s


# ------------------------------------------------------------- Nanite Cloud
def nanite_core(s, cx, cy, sc, rank, seed):
    e = rank - 1
    R = 84 * sc
    s.add(circle(cx, cy, R, s.rad(cx, cy, R, [(0, "#3affc0", 0.38 + 0.1 * e), (0.6, "#1ab08a", 0.22),
                                              (1, "#0a6a5a", 0)])))
    # orbit rings
    for rot in (-28, 32):
        s.add(ellipse(cx, cy, 70 * sc, 26 * sc, "none", 0.8, "#8affd8", 3.4, rot=rot))
    # motes
    rnd = random.Random(seed)
    n = 20 + 8 * e
    for i in range(n):
        a = rnd.uniform(0, 2 * math.pi)
        rr = (34 + rnd.random() * 46) * sc
        x, y = cx + rr * math.cos(a), cy + rr * math.sin(a) * 0.85
        r = rnd.choice([3.8, 4.5, 5.2, 6.0]) * (0.9 + 0.1 * sc)
        mote(s, x, y, r, rnd.choice(["#6affc0", "#7fe6ff", "#aaffdd"]))
    # orbiting bright motes on the rings
    for rot, t in [(-28, 30), (-28, 200), (32, 120), (32, 290)]:
        a = math.radians(t)
        x0, y0 = 70 * sc * math.cos(a), 26 * sc * math.sin(a)
        ra = math.radians(rot)
        x, y = cx + x0 * math.cos(ra) - y0 * math.sin(ra), cy + x0 * math.sin(ra) + y0 * math.cos(ra)
        mote(s, x, y, 6.2 * sc + 1, "#c8fff0")
    # core emitter
    s.glow(circle(cx, cy, 30 * sc, "#3affc0"), 8 * sc, 0.9)
    core = s.rad(cx - 6 * sc, cy - 6 * sc, 26 * sc, [(0, "#ffffff"), (0.35, "#d8fff0"), (0.7, "#3ad8a0"),
                                                       (1, "#0a5a4a")])
    hexp = [(cx + 22 * sc * math.cos(math.radians(60 * i + 30)), cy + 22 * sc * math.sin(math.radians(60 * i + 30)))
            for i in range(6)]
    s.add(poly(hexp, core, "#05211a", 3))
    s.add(poly(star4(cx, cy, 14 * sc, 3 * sc), "#ffffff"))


def nanite_cloud(rank):
    s = Svg()
    bg(s, 655, GREEN_NEB)
    nanite_core(s, 128, 128, 1.0, rank, 77)
    return s


def nanite_status():
    s = Svg()
    bg(s, 656, GREEN_NEB)
    nanite_core(s, 112, 116, 0.86, 1, 78)
    # shield + check accent, upper right (bottom-right stays free for the rank badge)
    x, y = 190, 64
    sh = f"M{x},{y - 30} L{x + 26},{y - 20} L{x + 24},{y + 6} C{x + 20},{y + 22} {x},{y + 32} {x},{y + 32} " \
         f"C{x},{y + 32} {x - 20},{y + 22} {x - 24},{y + 6} L{x - 26},{y - 20} Z"
    g = s.lin(x - 26, y - 30, x + 26, y + 32, [(0, "#e8fff6"), (0.5, "#3ad8a0"), (1, "#0a5a4a")])
    s.glow(path(sh, "#3affc0"), 4, 0.8)
    s.add(path(sh, g, "#05211a", 3.5))
    s.add(polyline([(x - 12, y + 1), (x - 3, y + 11), (x + 13, y - 10)], "#05211a", 9))
    s.add(polyline([(x - 12, y + 1), (x - 3, y + 11), (x + 13, y - 10)], "#ffffff", 5))
    return s


# ------------------------------------------------------------- Jump Start
def jump_start(rank):
    e = rank - 1
    s = Svg()
    bg(s, 666, ("#2a3a8a", "#6a5a1a"))
    x0, x1, y0, y1 = 90, 166, 96, 210
    # cables from the clamps down the sides
    for sx, col in ((-1, "#c82a2a"), (1, "#2a2e38")):
        cxp = 128 + sx * 22
        d = f"M{cxp + sx * 10},{82} C{128 + sx * 70},{70} {128 + sx * 86},{140} {128 + sx * 80},{214}"
        s.add(path(d, "none", "#05070c", 11))
        s.add(path(d, "none", col, 7))
        s.add(path(d, "none", "#ffffff", 1.6, 0.35))
    # cell body
    s.add(f'<rect x="{x0 + 5}" y="{y0 + 6}" width="{x1 - x0}" height="{y1 - y0}" rx="12" fill="#02040a" '
          f'opacity="0.55"/>')
    cg = s.lin(x0, 0, x1, 0, [(0, "#1a2232"), (0.3, "#c8d6ea"), (0.55, "#6a7c98"), (1, "#141a26")])
    s.add(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="12" fill="{cg}" '
          f'stroke="#05070c" stroke-width="4"/>')
    for yy in (y0 + 14, y1 - 14):
        s.add(f'<rect x="{x0 - 3}" y="{yy - 5}" width="{x1 - x0 + 6}" height="10" rx="3" fill="#d89a3a" '
              f'stroke="#05070c" stroke-width="2.5"/>')
    # charge window
    wx0, wx1, wy0, wy1 = 108, 148, y0 + 30, y1 - 28
    s.add(f'<rect x="{wx0}" y="{wy0}" width="{wx1 - wx0}" height="{wy1 - wy0}" rx="4" fill="#06120c" '
          f'stroke="#05070c" stroke-width="3"/>')
    nb = 4
    lit = 2 + e * 2
    bh = (wy1 - wy0 - 8) / nb
    for i in range(nb):
        yb = wy1 - 4 - (i + 1) * bh + 2
        if i < lit:
            s.glow(f'<rect x="{wx0 + 5}" y="{yb:.1f}" width="{wx1 - wx0 - 10}" height="{bh - 4:.1f}" '
                   f'fill="#5aff7a"/>', 2.5, 0.8)
            s.add(f'<rect x="{wx0 + 5}" y="{yb:.1f}" width="{wx1 - wx0 - 10}" height="{bh - 4:.1f}" rx="1.5" '
                  f'fill="#c8ffb0"/>')
        else:
            s.add(f'<rect x="{wx0 + 5}" y="{yb:.1f}" width="{wx1 - wx0 - 10}" height="{bh - 4:.1f}" rx="1.5" '
                  f'fill="#1a3a22"/>')
    # terminals and clamps
    for sx, col in ((-1, "#e03a3a"), (1, "#3a3f4c")):
        tx = 128 + sx * 22
        s.add(f'<rect x="{tx - 8}" y="{y0 - 12}" width="16" height="14" fill="#9aa8bc" stroke="#05070c" '
              f'stroke-width="2.5"/>')
        jaw = [(tx - 13, y0 - 6), (tx + 13, y0 - 6), (tx + 9, y0 - 26), (tx + sx * 16, y0 - 34),
               (tx - 9, y0 - 26)]
        s.add(poly(jaw, col, "#05070c", 3))
        s.add(line(tx - 9, y0 - 8, tx + 9, y0 - 8, "#05070c", 2.2))
        s.add(line(tx - 7, y0 - 24, tx + 5, y0 - 28, "#ffffff", 2, 0.5))
    # arc across the clamps
    p = zig(106, 64, 150, 64, 6, 9, 5)
    p = [(x, y - 14 * math.sin(math.pi * i / (len(p) - 1))) for i, (x, y) in enumerate(p)]
    arc_bolt(s, p, w=3.4 + 0.6 * e)
    flare(s, 128, 50, 16 + 4 * e, c1="#c8f4ff", c2="#3aa8ff")
    # discharge arcs into the cell
    arcs = [zig(84, 128, 60, 112, 3, 6, 11), zig(172, 150, 196, 132, 3, 6, 12)]
    if rank >= 2:
        arcs += [zig(84, 176, 58, 184, 3, 5, 13), zig(172, 104, 198, 92, 3, 5, 14)]
    for a in arcs:
        arc_bolt(s, a, w=2.4)
    return s


# ------------------------------------------------------------- Maintenance Routine
def maintenance(rank):
    e = rank - 1
    s = Svg()
    bg(s, 677, ("#1a6a5a", "#3a3a8a"))
    cx, cy = 128, 130
    # service loop arrow
    R = 84
    s.glow(path(arc_path(cx, cy, R, -60, 220), "none", "#3affa0", 10), 4, 0.8 + 0.2 * e)
    s.add(path(arc_path(cx, cy, R, -60, 220), "none", "#05211a", 13))
    lg = s.lin(40, 40, 216, 216, [(0, "#d8fff0"), (0.5, "#3ad88a"), (1, "#9affd0")])
    s.add(path(arc_path(cx, cy, R, -60, 220), "none", lg, 8))
    a = math.radians(220)
    hx, hy = cx + R * math.cos(a), cy + R * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)  # clockwise tangent
    head = [(hx + tx * 22, hy + ty * 22), (hx - ty * 16 * -1 + 0, hy + tx * 16 * -1),
            (hx + ty * 16 * -1, hy - tx * 16 * -1)]
    head = [(hx + tx * 20, hy + ty * 20), (hx + math.cos(a) * 16, hy + math.sin(a) * 16),
            (hx - math.cos(a) * 16, hy - math.sin(a) * 16)]
    s.add(poly(head, "#d8fff0", "#05211a", 3))
    # gear
    gp = gear_points(cx, cy, 58, 46, 10, rot=8)
    s.add(poly([(x + 3, y + 5) for x, y in gp], "#02040a", op=0.6))
    gg = s.rad(cx - 18, cy - 20, 76, [(0, "#eef5ff"), (0.4, "#8a9ab4"), (1, "#1a2130")])
    s.add(poly(gp, gg, "#05070c", 3.5))
    s.add(circle(cx, cy, 34, "none", 1, "#2a3344", 3))
    s.add(circle(cx, cy, 16, "#0c111c", 1, "#05070c", 3))
    s.add(path(arc_path(cx, cy, 52, 200, 260), "none", "#ffffff", 2.6, 0.7))
    # wrench across the gear, lower-left to upper-right
    ang = math.radians(-45)
    ux, uy = math.cos(ang), math.sin(ang)

    def at(t, o=0.0):
        return (cx + ux * t - uy * o, cy + uy * t + ux * o)

    a0, a1 = at(-74), at(54)
    wg = s.lin(*at(0, -10), *at(0, 10), [(0, "#f4f9ff"), (0.5, "#9aaac2"), (1, "#2a3446")])
    s.add(poly(rect_line(a0[0], a0[1], a1[0], a1[1], 18), wg, "#05070c", 3.5))
    # open jaw head
    hc = at(70)
    s.add(circle(hc[0], hc[1], 25, wg, 1, "#05070c", 3.5))
    jaw = [at(70, -11), at(100, -11), at(100, 11), at(70, 11)]
    s.add(poly(jaw, "#0c111c"))
    s.add(path(f"M{at(48, 16)[0]:.1f},{at(48, 16)[1]:.1f} L{at(56, 22)[0]:.1f},{at(56, 22)[1]:.1f}",
               "none", "#05070c", 2))
    # ring end
    rc = at(-84)
    s.add(circle(rc[0], rc[1], 18, wg, 1, "#05070c", 3.5))
    s.add(circle(rc[0], rc[1], 8, "#0c111c", 1, "#05070c", 2))
    s.add(line(*at(-66, -5), *at(46, -5), "#ffffff", 2.4, 0.7))
    # glint
    for x, y, r in [(170, 70, 3), (72, 186, 2.6)][:rank]:
        s.glow(circle(x, y, r * 2, "#7affc8"), 2.5)
        s.add(poly(star4(x, y, r * 3.6, r * 0.8), "#ffffff"))
    return s


# ------------------------------------------------------------- Emergency Override
def emergency_override():
    s = Svg()
    bg(s, 688, ("#8a2a1a", "#6a5a1a"))
    x0, x1, y0, y1 = 58, 198, 54, 206
    pid = s.uid("p")
    s.defs.append(f'<pattern id="{pid}" patternUnits="userSpaceOnUse" width="24" height="24" '
                  f'patternTransform="rotate(45)"><rect width="24" height="24" fill="#f2c21a"/>'
                  f'<rect width="12" height="24" fill="#16120a"/></pattern>')
    s.add(f'<rect x="{x0 + 5}" y="{y0 + 7}" width="{x1 - x0}" height="{y1 - y0}" rx="10" fill="#02040a" '
          f'opacity="0.6"/>')
    s.add(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="10" fill="url(#{pid})" '
          f'stroke="#05070c" stroke-width="4"/>')
    ig = s.lin(0, y0, 0, y1, [(0, "#4a566c"), (0.5, "#222a3a"), (1, "#0e131e")])
    ix0, ix1, iy0, iy1 = x0 + 16, x1 - 16, y0 + 16, y1 - 16
    s.add(f'<rect x="{ix0}" y="{iy0}" width="{ix1 - ix0}" height="{iy1 - iy0}" rx="5" fill="{ig}" '
          f'stroke="#05070c" stroke-width="3"/>')
    s.add(line(ix0 + 4, iy0 + 3, ix1 - 4, iy0 + 3, "#aab8cc", 2, 0.6))
    # lever slot
    s.add(f'<rect x="120" y="{iy0 + 14}" width="16" height="{iy1 - iy0 - 28}" rx="8" fill="#05070c"/>')
    # warning light (thrown = lit)
    for lx in (ix0 + 14, ix1 - 14):
        s.glow(circle(lx, iy0 + 14, 12, "#ff2a1a"), 4, 0.95)
        s.add(circle(lx, iy0 + 14, 8, "#ff6a4a", 1, "#05070c", 2))
        s.add(circle(lx - 2.5, iy0 + 11.5, 2.6, "#ffe0d0"))
    # lever: pivot low, handle thrown up
    px, py = 128, 158
    hx, hy = 128, 104
    s.add(circle(px, py, 18, s.rad(px - 5, py - 5, 22, [(0, "#eef5ff"), (0.5, "#7a8aa4"), (1, "#1a2130")]),
                 1, "#05070c", 3.5))
    s.add(poly(rect_line(px, py, hx, hy, 18), s.lin(120, 0, 136, 0, [(0, "#2a3446"), (0.5, "#dce8f8"),
                                                                       (1, "#2a3446")]), "#05070c", 3))
    hg = s.lin(100, 70, 156, 100, [(0, "#ff8a6a"), (0.5, "#d81a1a"), (1, "#5a0606")])
    s.add(f'<rect x="98" y="{hy - 16}" width="60" height="30" rx="13" fill="{hg}" stroke="#05070c" '
          f'stroke-width="3.5"/>')
    s.add(line(106, hy - 8, 150, hy - 8, "#ffd0c8", 3.2, 0.8))
    # surge arcs from the pivot outward
    for i, (ex, ey) in enumerate([(84, 186), (172, 186), (82, 140), (174, 136)]):
        arc_bolt(s, zig(px, py, ex, ey, 4, 6, 30 + i), glowc="#ffa21a", mid="#ffe08a", w=2.8)
    flare(s, px, py, 20, c1="#fff0b8", c2="#ffa21a")
    return s


# ------------------------------------------------------------------ build
JOBS = []
for r in range(1, 5):
    JOBS.append((f"ife_am_fldrep{r}", "fieldrepair", lambda r=r: field_repair(r)))
for r in range(1, 4):
    JOBS.append((f"ife_am_syspurge{r}", "systempurge", lambda r=r: system_purge(r)))
JOBS.append(("ife_am_prectool", "prectool", precision_tooling))
JOBS.append(("ife_am_sealfoam", "sealfoam", sealant_foam))
for r in range(1, 3):
    JOBS.append((f"ife_am_nanite{r}", "nanite", lambda r=r: nanite_cloud(r)))
for r in range(1, 3):
    JOBS.append((f"ife_am_jumpstrt{r}", "jumpstart", lambda r=r: jump_start(r)))
for r in range(1, 3):
    JOBS.append((f"ife_am_maint{r}", "maint", lambda r=r: maintenance(r)))
JOBS.append(("ife_am_emrgovr", "override", emergency_override))
STATUS = [("ief_am_nanite", nanite_status)]

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
    firsts = []
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
        if fam not in firsts:
            firsts.append(fam)
            op = f"{PREV}/{fam}.svg"
            open(op, "w", encoding="utf-8").write(svg.render(512))
            magick_render(op, f"{PREV}/{fam}.png")
            os.remove(op)
    for resref, fn in STATUS:
        svg = fn()
        sp = f"{SRC}/{resref}.svg"
        open(sp, "w", encoding="utf-8").write(svg.render(256))
        rp = f"{RENDER}/{resref}.png"
        magick_render(sp, rp)
        Image.open(rp).convert("RGB").save(f"{STATUS_PNG}/{resref}.png")
    keys = list(finals)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert list(finals[keys[i]].getdata()) != list(finals[keys[j]].getdata()), (keys[i], keys[j])
    # contact sheet (32x32 finals enlarged 8x nearest), one family per row
    rows = []
    for resref, fam, _ in JOBS:
        if not rows or rows[-1][0] != fam:
            rows.append((fam, []))
        rows[-1][1].append(resref)
    cell, pad = 256, 16
    W = pad + 4 * (cell + pad)
    H = pad + len(rows) * (cell + pad)
    sheet = Image.new("RGB", (W, H), (128, 128, 128))
    for ri, (_, row) in enumerate(rows):
        for ci, rr in enumerate(row):
            sheet.paste(finals[rr].resize((cell, cell), Image.NEAREST),
                        (pad + ci * (cell + pad), pad + ri * (cell + pad)))
    sheet.save(HERE + "/contact_center.png")
    # 32x32 at 4x, all icons on one compact sheet plus the status subject
    small = Image.new("RGB", (6 * 136 + 8, 3 * 136 + 8), (128, 128, 128))
    for i, rr in enumerate(keys):
        small.paste(finals[rr].resize((128, 128), Image.NEAREST), (8 + (i % 6) * 136, 8 + (i // 6) * 136))
    st = Image.open(f"{STATUS_PNG}/ief_am_nanite.png").resize((128, 128), Image.LANCZOS)
    small.paste(st, (8 + 5 * 136, 8 + 2 * 136))
    small.save(HERE + "/contact_32px.png")
    mont = Image.new("RGB", (512 * 4, 512 * 2), (128, 128, 128))
    for i, f in enumerate(firsts):
        mont.paste(Image.open(f"{PREV}/{f}.png").convert("RGB"), ((i % 4) * 512, (i // 4) * 512))
    mont.save(HERE + "/previews_512.png")
    print("ok", len(finals), "icons,", len(STATUS), "status")


if __name__ == "__main__":
    main()
