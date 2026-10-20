#!/usr/bin/env python3
"""Bake the missing horn texture of male Zabrak head 266.

pmh0_head266's horn mesh binds the horns_zab1 material, whose texture0
(horns_zab1) was never committed, so the client drew the horns with its
missing-texture fallback. The horn UVs are unique to this mesh, so no other
Zabrak horn artwork fits them. This bakes an ivory horn texture from the mesh
itself: each texel takes its position along its horn (scalp to tip) and gets a
darker base, ivory tip and growth ridges, matching the other Zabrak horns.

Run: python -B tools/GenerateZabrakHornTexture.py [--check]
"""
import argparse
import hashlib
import math
import struct
from pathlib import Path

import numpy as np

from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "sw_pt_head/pmh0_head266.mdl"
OUTPUT = ROOT / "sw_pt_head/horns_zab1.tga"
MESH = "horns_zab1"
SIZE = 256
BASE = np.array([104.0, 86.0, 64.0])
TIP = np.array([234.0, 224.0, 198.0])
HEAD_CENTRE = np.array([0.0, 0.0, 0.06])


def horn_mesh():
    data = MODEL.read_bytes()
    model = Model(data, False)
    raw = 12 + model.uint(4)
    for name, _, _, _, node in model.nodes:
        if name != MESH:
            continue
        flags = model.uint(node + 108)
        mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
        count = struct.unpack_from("<H", data, mesh + 448)[0]
        stride = model.uint(mesh + 440)
        def array(pointer, columns):
            return np.array([struct.unpack_from(f"<{columns}f", data, raw + pointer + i * (stride or 4 * columns))
                             for i in range(count)])
        verts = array(model.uint(mesh + 444), 3)
        uvs = array(model.uint(mesh + 452), 2)
        faces = np.array([struct.unpack_from("<3H", data, 12 + model.uint(mesh + 8) + i * 32 + 26)
                          for i in range(model.uint(mesh + 12))])
        return verts, uvs, faces
    raise ValueError(f"{MODEL.name} has no {MESH} mesh")


def components(count, faces):
    parent = list(range(count))
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, c in faces:
        for x, y in ((a, b), (b, c)):
            parent[find(x)] = find(y)
    return [find(i) for i in range(count)]


def bake():
    verts, uvs, faces = horn_mesh()
    owner = components(len(verts), faces)
    radius = np.linalg.norm(verts - HEAD_CENTRE, axis=1)
    along = np.zeros(len(verts))
    for root in set(owner):
        members = [i for i, o in enumerate(owner) if o == root]
        low, high = radius[members].min(), radius[members].max()
        along[members] = (radius[members] - low) / max(high - low, 1e-6)
    image = np.zeros((SIZE, SIZE, 3))
    filled = np.zeros((SIZE, SIZE), dtype=bool)
    for face in faces:
        tri = uvs[face] * SIZE
        x0, y0 = np.floor(tri.min(axis=0)).astype(int)
        x1, y1 = np.ceil(tri.max(axis=0)).astype(int)
        (ax, ay), (bx, by), (cx, cy) = tri
        area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(area) < 1e-9:
            continue
        for y in range(max(y0, 0), min(y1 + 1, SIZE)):
            for x in range(max(x0, 0), min(x1 + 1, SIZE)):
                px, py = x + 0.5, y + 0.5
                w0 = ((bx - px) * (cy - py) - (cx - px) * (by - py)) / area
                w1 = ((cx - px) * (ay - py) - (ax - px) * (cy - py)) / area
                w2 = 1 - w0 - w1
                if min(w0, w1, w2) < -0.02:
                    continue
                t = min(max(float(np.dot([w0, w1, w2], along[face])), 0.0), 1.0)
                ridge = 1 - 0.07 * (0.5 + 0.5 * math.sin(t * 2 * math.pi * 6))
                image[y, x] = (BASE + (TIP - BASE) * t ** 0.8) * ridge
                filled[y, x] = True
    # Dilate into unused texels so mip filtering never reaches black.
    for _ in range(SIZE):
        if filled.all():
            break
        grown = filled.copy()
        result = image.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted = np.roll(filled, (dy, dx), axis=(0, 1))
            colours = np.roll(image, (dy, dx), axis=(0, 1))
            take = shifted & ~grown
            result[take] = colours[take]
            grown |= take
        image, filled = result, grown
    # Row r is sampled at v = r / SIZE; a bottom-left TGA stores that row first.
    pixels = np.clip(np.rint(image), 0, 255).astype(np.uint8)[:, :, ::-1]
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, SIZE, SIZE, 24, 0)
    return header + pixels.tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = bake()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != data:
            raise SystemExit(f"{OUTPUT.relative_to(ROOT)} is stale; rerun without --check")
        print(f"{OUTPUT.relative_to(ROOT).as_posix()} is current.")
        return
    OUTPUT.write_bytes(data)
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()} sha256={hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
