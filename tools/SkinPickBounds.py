"""Fit skin-mesh bounding boxes to the creature's standing poses.

The client picks a creature under the cursor with each skin mesh's stored
bounding box, which the compiler takes from the bind pose. Some creature rigs
are bound far from how they stand (a Behir is bound as a straight body floating
3 m up, then curled onto the ground by its animations), so the clickable area
floats above or around the visible model. This tool replaces each skin node's
box with the box of its skinned vertices in the first frame of its idle
animation. A box smaller than a rearing or walking pose is harmless, because
rigid head and limb meshes stay clickable; a larger one blocks clicks. Only the box and radius change; geometry,
weights, inverse binds and animations stay byte-identical, so the model looks
and moves the same.

    python tools/SkinPickBounds.py --apply --game-data "<NWN>/data"   # fit every listed model
    python tools/SkinPickBounds.py --check                            # packaging guard
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import struct
import tempfile

import CleanModels
import GenerateTintMapAssets as tint

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "tools/SkinPickBounds.json"
# Idle animations, in the order the engine prefers them for creatures.
IDLE = ("cpause1", "pause1")
SKIN_FLAG = 0x40
MESH_FLAG = 0x20


# Binary model layout -------------------------------------------------------

def mesh_nodes(data: bytes):
    """Yield (name, flags, node offset) for every node of a compiled model."""
    if data[:4] != bytes(4):
        raise ValueError("ASCII model; compile it first with CompileModels.py --model <resref> --apply")
    base = 12
    pending = [struct.unpack_from("<I", data, base + 72)[0]]
    while pending:
        node = base + pending.pop()
        name = data[node + 32:node + 64].split(b"\0", 1)[0].decode("latin-1").lower()
        flags = struct.unpack_from("<I", data, node + 108)[0]
        children, count = struct.unpack_from("<II", data, node + 72)
        yield name, flags, node
        pending.extend(reversed(struct.unpack_from(f"<{count}I", data, base + children)))


def skin_boxes(data: bytes) -> dict[str, tuple[tuple[float, ...], tuple[float, ...], float]]:
    return {name: (struct.unpack_from("<3f", data, node + 132), struct.unpack_from("<3f", data, node + 144),
                   struct.unpack_from("<f", data, node + 156)[0])
            for name, flags, node in mesh_nodes(data) if flags & MESH_FLAG and flags & SKIN_FLAG}


def patch(data: bytes, boxes: dict[str, tuple[list[float], list[float]]]) -> bytes:
    """Write each skin node's box and a radius that keeps every corner inside its sphere."""
    out = bytearray(data)
    for name, flags, node in mesh_nodes(data):
        if not (flags & MESH_FLAG and flags & SKIN_FLAG) or name not in boxes:
            continue
        low, high = boxes[name]
        centre = struct.unpack_from("<3f", data, node + 160)
        radius = max(math.dist(centre, corner) for corner in itertools.product(*zip(low, high)))
        struct.pack_into("<7f", out, node + 132, *low, *high, radius)
    return bytes(out)


# Pose evaluation on decompiled ASCII ----------------------------------------

def quaternion(x, y, z, angle):
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-9 or abs(angle) < 1e-9:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle / 2) / length
    return (math.cos(angle / 2), x * s, y * s, z * s)


def multiply(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)


def rotate(q, v):
    r = multiply(multiply(q, (0.0, *v)), (q[0], -q[1], -q[2], -q[3]))
    return r[1:]


class Transform:
    def __init__(self, rotation=(1.0, 0.0, 0.0, 0.0), translation=(0.0, 0.0, 0.0)):
        self.rotation, self.translation = rotation, translation

    def __mul__(self, other):
        moved = rotate(self.rotation, other.translation)
        return Transform(multiply(self.rotation, other.rotation),
                         tuple(m + t for m, t in zip(moved, self.translation)))

    def apply(self, point):
        return tuple(r + t for r, t in zip(rotate(self.rotation, point), self.translation))

    def inverse(self):
        rotation = (self.rotation[0], -self.rotation[1], -self.rotation[2], -self.rotation[3])
        return Transform(rotation, tuple(-v for v in rotate(rotation, self.translation)))


def parse_geometry(lines):
    """Geometry nodes, supermodel name and animation scale of a decompiled model."""
    nodes, supermodel, scale = {}, None, 1.0
    index = 0
    while index < len(lines):
        words = lines[index].split()
        if words[:1] and words[0].lower() == "setsupermodel" and len(words) > 2:
            supermodel = words[2].lower()
        if words[:1] and words[0].lower() == "setanimationscale" and len(words) > 1:
            scale = float(words[1])
        if words[:1] and words[0].lower() == "newanim":
            break
        if len(words) >= 3 and words[0].lower() == "node":
            node = {"type": words[1].lower(), "parent": None, "position": (0.0, 0.0, 0.0),
                    "orientation": (1.0, 0.0, 0.0, 0.0), "verts": [], "weights": []}
            index += 1
            while lines[index].split()[:1] != ["endnode"]:
                field = lines[index].split()
                key = field[0].lower() if field else ""
                if key == "parent":
                    node["parent"] = field[1].lower()
                elif key == "position" and len(field) >= 4:
                    node["position"] = tuple(map(float, field[1:4]))
                elif key == "orientation" and len(field) >= 5:
                    node["orientation"] = quaternion(*map(float, field[1:5]))
                elif key in ("verts", "weights") and len(field) >= 2:
                    rows = lines[index + 1:index + 1 + int(field[1])]
                    if key == "verts":
                        node["verts"] = [tuple(map(float, row.split()[:3])) for row in rows]
                    else:
                        node["weights"] = [[(pair[0].lower(), float(pair[1])) for pair in zip(row.split()[::2], row.split()[1::2])]
                                           for row in rows]
                    index += len(rows)
                index += 1
            nodes[words[2].lower()] = node
        index += 1
    return nodes, supermodel, scale


def first_frame(lines, animation):
    """Node transforms at time 0 of `animation`, or None when the model lacks it."""
    start = next((i for i, line in enumerate(lines)
                  if line.split()[:2] and [w.lower() for w in line.split()[:2]] == ["newanim", animation]), None)
    if start is None:
        return None
    frame, node = {}, None
    for index in range(start + 1, len(lines)):
        words = lines[index].split()
        key = words[0].lower() if words else ""
        if key == "doneanim":
            break
        if key == "node" and len(words) >= 3:
            node = frame.setdefault(words[2].lower(), {})
        elif node is not None and key in ("positionkey", "orientationkey"):
            values = list(map(float, lines[index + 1].split()[1:]))
            if key == "positionkey":
                node["position"] = tuple(values[:3])
            else:
                node["orientation"] = quaternion(*values[:4])
        elif node is not None and key == "position" and len(words) >= 4:
            node.setdefault("position", tuple(map(float, words[1:4])))
        elif node is not None and key == "orientation" and len(words) >= 5:
            node.setdefault("orientation", quaternion(*map(float, words[1:5])))
    return frame


def world(nodes, local):
    cache = {}

    def resolve(name):
        if name not in cache:
            node = nodes[name]
            own = local(name, node)
            parent = node["parent"]
            cache[name] = resolve(parent) * own if parent in nodes else own
        return cache[name]
    return resolve


def pose_boxes(model_lines, chain):
    """Skin-node-local box of every skin mesh in the first frame of the idle animation.

    `chain` is the decompiled supermodel chain; the model's own animations win.
    Animation positions are scaled by the model's animation scale, as in game."""
    nodes, _, scale = parse_geometry(model_lines)
    frame = next((f for animation in IDLE for lines in [model_lines, *chain]
                  if (f := first_frame(lines, animation))), None)
    if frame is None:
        raise ValueError("no idle animation found")

    def posed_local(name, node):
        keys = frame.get(name, {})
        position = tuple(v * scale for v in keys["position"]) if "position" in keys else node["position"]
        return Transform(keys.get("orientation", node["orientation"]), position)

    rest = world(nodes, lambda name, node: Transform(node["orientation"], node["position"]))
    posed = world(nodes, posed_local)
    boxes = {}
    for name, node in nodes.items():
        if node["type"] != "skin" or not node["verts"]:
            continue
        to_local = rest(name).inverse()
        points = []
        for vertex, weights in zip(node["verts"], node["weights"] or [[]] * len(node["verts"])):
            bound = rest(name).apply(vertex)
            total, moved = 0.0, [0.0, 0.0, 0.0]
            for bone, weight in weights:
                if bone in nodes:
                    point = (posed(bone) * rest(bone).inverse()).apply(bound)
                    moved = [m + p * weight for m, p in zip(moved, point)]
                    total += weight
            points.append(to_local.apply(tuple(m / total for m in moved) if total else bound))
        boxes[name] = ([min(p[axis] for p in points) for axis in range(3)],
                       [max(p[axis] for p in points) for axis in range(3)])
    return boxes


# Resource resolution ---------------------------------------------------------

def model_source(name, game_data, active, stock):
    if name in active:
        return active[name].read_bytes()
    if game_data is not None and name in stock:
        return tint.extract_stock_bif_resource(*stock[name])
    raise FileNotFoundError(f"Model {name} is in neither the HAK sources nor the game data")


def decompiled_chain(name, data, game_data, active, stock, staging, compiler):
    """ASCII lines of the model and of its supermodel chain."""
    chain, seen = [], set()
    current = data
    while True:
        source = staging / f"{name}.mdl"
        source.write_bytes(current)
        if current[:4] == bytes(4):
            output = staging / "ascii"
            output.mkdir(exist_ok=True)
            CleanModels.run(compiler, staging, "decompile", [source], output, f"{name}.log")
            text = (output / source.name).read_text(encoding="latin-1")
        else:
            text = current.decode("latin-1")
        lines = text.splitlines()
        chain.append(lines)
        seen.add(name)
        _, name, _ = parse_geometry(lines)
        if not name or name == "null" or name in seen:
            return chain
        current = model_source(name, game_data, active, stock)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fit(root, entries, game_data, compiler):
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(game_data) if game_data is not None else {}
    results = {}
    with tempfile.TemporaryDirectory(prefix="skin-pick-") as directory:
        for name, relative in entries.items():
            path = root / relative
            data = path.read_bytes() if path.is_file() else model_source(name, game_data, {}, stock)
            staging = Path(directory) / name
            staging.mkdir()
            model, *chain = decompiled_chain(name, data, game_data, active, stock, staging, compiler)
            results[name] = (path, data, patch(data, pose_boxes(model, chain)))
    return results


def load_manifest(root):
    return json.loads((root / MANIFEST).read_text())


def check(root=ROOT):
    manifest = load_manifest(root)
    stale = [name for name, entry in manifest["models"].items()
             if not (root / entry["path"]).is_file() or digest((root / entry["path"]).read_bytes()) != entry["sha256"]]
    if stale:
        raise ValueError(f"Skin pick bounds are stale for {stale}. Run SkinPickBounds.py --apply --game-data <NWN/data>.")
    print(f"Skin pick bounds: {len(manifest['models'])} creature models fit their standing poses.")


def apply(root, game_data, compiler):
    manifest = load_manifest(root)
    entries = {name: entry["path"] for name, entry in manifest["models"].items()}
    for name, (path, before, after) in fit(root, entries, game_data, compiler).items():
        if skin_boxes(before).keys() != skin_boxes(after).keys():
            raise ValueError(f"{name}: skin node set changed while fitting")
        path.write_bytes(after)
        CleanModels.validate_binary(path)
        manifest["models"][name]["sha256"] = digest(after)
        print(f"{name}: {len(skin_boxes(after))} skin boxes fitted")
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Fit and rewrite every model in the manifest")
    parser.add_argument("--check", action="store_true", help="Fail when a listed model changed since it was fitted")
    parser.add_argument("--game-data", type=Path, help="Installed EE data directory, for stock models and supermodels")
    args = parser.parse_args()
    if args.apply:
        apply(ROOT, args.game_data, CleanModels.find_compiler())
    else:
        check(ROOT)


if __name__ == "__main__":
    main()
