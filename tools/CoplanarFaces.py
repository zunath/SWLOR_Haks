#!/usr/bin/env python3
"""Find faces in different mesh nodes of one model that lie on the same plane and overlap.

Two meshes drawn on the same surface z-fight: the depth test cannot order them, so the
visible surface swaps between them as the camera moves (the Blacksite Analysis Station
screen, plc_comppnl2, drew a duplicate metal panel on top of its display). Reads binary
and ASCII models, places every trimesh in model space through its parents' positions
and orientations, and reports overlapping pairs whose texture differs, since only a
change in what is drawn makes the swap visible.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import struct
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PLANE_TOLERANCE = 0.001
NORMAL_TOLERANCE = 0.9999
AREA_TOLERANCE = 1e-4
MESH_FLAG = 0x20


class Mesh:
    def __init__(self, name, bitmap, vertices, uvs, faces):
        self.name, self.bitmap = name, bitmap.lower()
        self.vertices, self.uvs, self.faces = vertices, uvs, faces


def rotate(quaternion, vectors):
    x, y, z, w = quaternion
    matrix = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return vectors @ matrix.T


def axis_angle(values):
    x, y, z, angle = values
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0 or angle == 0:
        return (0.0, 0.0, 0.0, 1.0)
    half = math.sin(angle / 2) / length
    return (x * half, y * half, z * half, math.cos(angle / 2))


def place(chain, vertices):
    """Move local vertices into model space through (position, quaternion) pairs, innermost first."""
    for position, quaternion in chain:
        vertices = rotate(quaternion, vertices) + position
    return vertices


def binary_meshes(data: bytes) -> list[Mesh]:
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    raw = 12 + uint(4)
    meshes = []

    def transform(node):
        keys, count, values = uint(node + 84), uint(node + 88), uint(node + 96)
        position, quaternion = np.zeros(3), (0.0, 0.0, 0.0, 1.0)
        for index in range(count):
            kind, _, _, start, columns = struct.unpack_from("<IHHHB", data, 12 + keys + index * 12)
            if kind == 8 and columns >= 3:
                position = np.array(struct.unpack_from("<3f", data, 12 + values + start * 4))
            elif kind == 20 and columns == 4:
                quaternion = struct.unpack_from("<4f", data, 12 + values + start * 4)
        return position, quaternion

    def walk(pointer, chain):
        node = 12 + pointer
        chain = [transform(node)] + chain
        flags = uint(node + 108)
        if flags & MESH_FLAG:
            mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
            count = struct.unpack_from("<H", data, mesh + 448)[0]
            stride = uint(mesh + 440)
            vertex_pointer, uv_pointer = uint(mesh + 444), uint(mesh + 452)
            if vertex_pointer != 0xFFFFFFFF and count:
                vertices = np.array([struct.unpack_from("<3f", data, raw + vertex_pointer + index * (stride or 12))
                                     for index in range(count)])
                uvs = (np.array([struct.unpack_from("<2f", data, raw + uv_pointer + index * (stride or 8))
                                 for index in range(count)]) if uv_pointer != 0xFFFFFFFF else np.zeros((count, 2)))
                faces = [struct.unpack_from("<3H", data, 12 + uint(mesh + 8) + index * 32 + 26)
                         for index in range(uint(mesh + 12))]
                render = uint(mesh + 108)
                name = data[node + 32:node + 64].split(b"\0", 1)[0].decode("latin1").lower()
                bitmap = data[mesh + 120:mesh + 184].split(b"\0", 1)[0].decode("latin1")
                if render:
                    meshes.append(Mesh(name, bitmap, place(chain, vertices), uvs, faces))
        children, count = uint(node + 72), uint(node + 76)
        for index in range(count):
            walk(uint(12 + children + index * 4), chain)

    walk(uint(84), [])
    return meshes


def ascii_meshes(text: str) -> list[Mesh]:
    lines = text.split("endmodelgeom", 1)[0].splitlines()
    nodes, order, current, index = {}, [], None, 0
    while index < len(lines):
        tokens = lines[index].split()
        index += 1
        if not tokens:
            continue
        key = tokens[0].lower()
        if key == "node" and len(tokens) >= 3:
            current = {"type": tokens[1].lower(), "name": tokens[2].lower(), "parent": None, "position": np.zeros(3),
                       "orientation": (0.0, 0.0, 0.0, 1.0), "bitmap": "", "render": True, "verts": None, "tverts": None,
                       "faces": []}
            nodes[current["name"]] = current
            order.append(current)
        elif current is None:
            continue
        elif key == "endnode":
            current = None
        elif key == "parent":
            current["parent"] = tokens[1].lower()
        elif key == "position":
            current["position"] = np.array([float(value) for value in tokens[1:4]])
        elif key == "orientation":
            current["orientation"] = axis_angle([float(value) for value in tokens[1:5]])
        elif key == "bitmap":
            current["bitmap"] = tokens[1] if len(tokens) > 1 else ""
        elif key == "render":
            current["render"] = tokens[1] != "0"
        elif key in {"verts", "tverts", "faces"}:
            rows = [lines[index + row].split() for row in range(int(tokens[1]))]
            index += len(rows)
            if key == "faces":
                current["faces"] = [tuple(int(value) for value in row[:3]) for row in rows]
                current["face_uvs"] = [tuple(int(value) for value in row[4:7]) for row in rows]
            else:
                current[key] = np.array([[float(value) for value in row[:3 if key == "verts" else 2]] for row in rows])
    meshes = []
    for node in order:
        if node["type"] not in {"trimesh", "danglymesh", "skin"} or node["verts"] is None or not node["render"]:
            continue
        chain, parent = [], node
        while parent is not None:
            chain.append((parent["position"], parent["orientation"]))
            parent = nodes.get(parent["parent"] or "")
        uvs = node["tverts"] if node["tverts"] is not None else np.zeros((len(node["verts"]), 2))
        # ASCII faces index UVs separately; expand to per-corner vertices so both formats match.
        corners, corner_uvs, faces = [], [], []
        for face, uv_face in zip(node["faces"], node.get("face_uvs", node["faces"])):
            faces.append(tuple(range(len(corners), len(corners) + 3)))
            corners.extend(node["verts"][vertex] for vertex in face)
            corner_uvs.extend(uvs[uv] if uv < len(uvs) else np.zeros(2) for uv in uv_face)
        meshes.append(Mesh(node["name"], node["bitmap"], place(chain, np.array(corners).reshape(-1, 3)),
                           np.array(corner_uvs).reshape(-1, 2), faces))
    return meshes


def read_meshes(path: Path) -> list[Mesh]:
    data = path.read_bytes()
    return binary_meshes(data) if data[:4] == bytes(4) else ascii_meshes(data.decode("latin1"))


def overlap_area(first: np.ndarray, second: np.ndarray, normal: np.ndarray) -> float:
    """Area shared by two coplanar triangles (convex polygon clipping in the plane)."""
    axis = np.argmax(np.abs(normal))
    keep = [index for index in range(3) if index != axis]
    subject = [tuple(point) for point in first[:, keep]]
    clip = [tuple(point) for point in second[:, keep]]

    def signed(polygon):
        return 0.5 * sum(polygon[i - 1][0] * polygon[i][1] - polygon[i][0] * polygon[i - 1][1]
                         for i in range(len(polygon)))

    if signed(subject) < 0:
        subject.reverse()
    if signed(clip) < 0:
        clip.reverse()
    for start, end in zip(clip, clip[1:] + clip[:1]):
        inside = lambda point: (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0]) >= 0
        output = []
        for current, previous in zip(subject, subject[-1:] + subject[:-1]):
            if inside(current):
                if not inside(previous):
                    output.append(intersection(previous, current, start, end))
                output.append(current)
            elif inside(previous):
                output.append(intersection(previous, current, start, end))
        subject = output
        if not subject:
            return 0.0
    # Scale the projected area back onto the plane.
    return abs(signed(subject)) / abs(normal[axis])


def intersection(p1, p2, q1, q2):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    ex, ey = q2[0] - q1[0], q2[1] - q1[1]
    denominator = dx * ey - dy * ex
    if denominator == 0:
        return p2
    t = ((q1[0] - p1[0]) * ey - (q1[1] - p1[1]) * ex) / denominator
    return (p1[0] + t * dx, p1[1] + t * dy)


def triangles(mesh: Mesh):
    for face in mesh.faces:
        corners = mesh.vertices[list(face)]
        normal = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        length = np.linalg.norm(normal)
        if length > 1e-12:
            yield corners, mesh.uvs[list(face)], normal / length


def conflicts(meshes: list[Mesh]) -> list[tuple[str, str, float]]:
    """Node pairs with coplanar, overlapping faces that draw different texels; area in square metres."""
    found = {}
    prepared = [(mesh, list(triangles(mesh))) for mesh in meshes]
    for first_index, (first, first_faces) in enumerate(prepared):
        for second, second_faces in prepared[first_index + 1:]:
            if first.name == second.name:
                continue
            for corners, uvs, normal in first_faces:
                low, high = corners.min(0) - PLANE_TOLERANCE, corners.max(0) + PLANE_TOLERANCE
                for other, other_uvs, other_normal in second_faces:
                    if np.any(other.max(0) < low) or np.any(other.min(0) > high):
                        continue
                    if abs(normal @ other_normal) < NORMAL_TOLERANCE:
                        continue
                    if np.max(np.abs((other - corners[0]) @ normal)) > PLANE_TOLERANCE:
                        continue
                    if first.bitmap == second.bitmap and np.allclose(sorted(map(tuple, np.round(uvs, 4))),
                                                                     sorted(map(tuple, np.round(other_uvs, 4)))):
                        continue
                    area = overlap_area(corners, other, normal)
                    if area > AREA_TOLERANCE:
                        key = (first.name, second.name)
                        found[key] = found.get(key, 0.0) + area
    return [(first, second, area) for (first, second), area in sorted(found.items())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", type=Path, help="Model files or folders to scan")
    args = parser.parse_args()
    paths = []
    for item in args.models:
        paths.extend(sorted(item.glob("*.mdl")) if item.is_dir() else [item])
    failures = 0
    for path in paths:
        try:
            found = conflicts(read_meshes(path))
        except (struct.error, ValueError, IndexError) as error:
            print(f"{path.name}: unreadable ({error})", file=sys.stderr)
            continue
        for first, second, area in found:
            failures += 1
            print(f"{path.name}: {first} and {second} overlap on one plane ({area:.4f} m2)")
    print(f"{len(paths)} models scanned; {failures} overlapping node pairs.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
