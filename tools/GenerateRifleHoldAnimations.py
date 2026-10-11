#!/usr/bin/env python3
"""Retarget the rifle hold clips so every playable body supports rifles like the male human.

A rifle hangs from the firing hand's weapon hook (`rhand`), so one rigid model can
sit correctly only for the hands it was fitted to. Every body plays the same
`xbowr` (idle), `xbowrdy` (ready) and `xbowshot` (attack) clips, and each body's own
arm lengths and hand meshes put its support hand somewhere else on the weapon: the
female human's lands about 4 cm low, the male half-orc's about 11 cm up the barrel.
Rifles are fitted to the male human (`pmh0`).

For each other body this writes a small animation bank, spliced between the body
root and the supermodel it inherited from, holding only those three clips: every
node and track of the clip the body resolved before, copied from the compiled data, with the
support arm re-solved by two-bone IK so the support palm reaches the male human's
contact on the weapon, keeping this body's own palm orientation. Bones are only
rotated; bone lengths stay the wearer's own.

The weapon hook keeps its inherited tracks. Robe bridges share one garment `rhand`
joint only while every robe family sees the same hook motion; keying it splits that
joint and changes every robe root's skeleton.

Banks are emitted as authored ASCII into `sw_cr_creature`; compile them with
`CompileModels.py --model <bank> --game-data <data> --apply`, then rerun this tool
with `--finalize` to restore the exact inherited part IDs and floats in every copied track. Compiled
body roots are re-pointed at their bank in place. ASCII roots (the stock large
phenotypes, `pmd0`, SWLOR's `pfd2`) are written against their original supermodel,
compiled in the same CompileModels.py run, then re-pointed by `--finalize`, so every
native part ID matches the original compile. Robe bridges carry their own copies of
these clips: refresh them with `GenerateRobeRgbModels.py --update-animation xbowr
--update-animation xbowrdy --update-animation xbowshot` afterwards.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import struct

import numpy as np

import CompileModels as mdl
import GenerateTintMapAssets as tint
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "pmh0"
CLIPS = ("xbowr", "xbowrdy", "xbowshot")
# Playable appearance families (racialtypes.2da PlayerRace rows), normal and large phenotype.
BODIES = ("pfh0", "pfh2", "pfe0", "pfe2", "pfd0", "pfd2", "pfo0", "pfo2", "pfz0",
          "pme0", "pme2", "pmd0", "pmd2", "pmo0", "pmo2", "pmz0")
BANK_PREFIX = "a_rfl_"
BANK_DIRECTORY = "sw_cr_creature"
ROOT_DIRECTORY = "sw_pt_root"
ARM = ("lbicep_g", "lforearm_g", "lhand_g")
HOOK = "rhand"
CHAIN_NODES = ("rootdummy", "torso_g", "rbicep_g", "rforearm_g", "rhand_g", HOOK) + ARM
CELL = 0.006


def bank_name(body: str) -> str:
    return BANK_PREFIX + body


def hand_shape(body: str) -> str:
    """Large-phenotype hand parts are placeholder boxes on the same skeleton; fit the normal hands."""
    return body[:3] + "0"


# ---------------------------------------------------------------- math
def quat_matrix(q) -> np.ndarray:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def matrix_quat(m) -> np.ndarray:
    t = np.trace(m)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def axis_angle(q) -> tuple[float, float, float, float]:
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / n, y / n, z / n, w / n
    s = math.sqrt(x * x + y * y + z * z)
    if s < 1e-12:
        return 0.0, 0.0, 0.0, 0.0
    return x / s, y / s, z / s, 2 * math.atan2(s, w)


def transform(rotation=None, translation=None) -> np.ndarray:
    m = np.eye(4)
    if rotation is not None:
        m[:3, :3] = rotation
    if translation is not None:
        m[:3, 3] = translation
    return m


def slerp(a, b, w):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        r = a + (b - a) * w
        return r / np.linalg.norm(r)
    angle = math.acos(min(1.0, d))
    return (math.sin((1 - w) * angle) * a + math.sin(w * angle) * b) / math.sin(angle)


def sample(controller, time, rotation=False):
    times, values = controller
    if time <= times[0]:
        return np.array(values[0], float)
    for index in range(1, len(times)):
        if time <= times[index]:
            w = (time - times[index - 1]) / max(1e-9, times[index] - times[index - 1])
            a, b = np.array(values[index - 1], float), np.array(values[index], float)
            return slerp(a, b, w) if rotation else a + (b - a) * w
    return np.array(values[-1], float)


def rotation_between(a, b) -> np.ndarray:
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -0.999999:
        raise ValueError("Opposed IK bone directions")
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def angle_degrees(m) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(m) - 1) / 2))))


# ---------------------------------------------------------------- models
class Library:
    """Compiled models in engine precedence: active HAK resources, then stock KEY/BIF."""

    def __init__(self, game_data: Path | None):
        self.active = tint.find_active_models()
        self.stock = tint.read_stock_key_models(game_data) if game_data else {}
        self.cache: dict[str, bytes | None] = {}

    def data(self, name: str) -> bytes | None:
        name = name.lower()
        if name not in self.cache:
            if name in self.active:
                self.cache[name] = self.active[name].read_bytes()
            elif name in self.stock:
                self.cache[name] = tint.extract_stock_bif_resource(*self.stock[name])
            else:
                self.cache[name] = None
        return self.cache[name]

    def model(self, name: str, clips=True) -> Model:
        data = self.data(name)
        if data is None or not mdl.binary(data):
            raise RuntimeError(f"Compiled model {name} is unavailable; supply --game-data for stock models")
        return Model(data, clips)

    def stock_owned(self, name: str) -> bool:
        return name.lower() not in self.active

    def inherited_parent(self, root: str) -> str:
        """The supermodel a root inherited before its rifle bank was spliced in."""
        parent = mdl.supermodel(self.data(root))
        if parent == bank_name(root):
            parent = mdl.supermodel(self.data(parent))
        return parent

    def clip(self, root: str, clip: str, start: str | None = None):
        """(owner, Model) of the first model in the chain that defines this clip."""
        name, seen = start if start is not None else mdl.supermodel(self.data(root)), set()
        while name and name not in seen:
            seen.add(name)
            data = self.data(name)
            if data is None:
                break
            model = Model(data)
            if clip in model.clips:
                return name, model
            name = model.parent
        raise RuntimeError(f"{root}: no model in the supermodel chain defines {clip}")


def animation_header(data: bytes, clip: str) -> dict:
    """Length, transition, animation root and events of a compiled clip."""
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    for index in range(uint(136)):
        offset = 12 + uint(12 + uint(132) + index * 4)
        if data[offset + 8:offset + 72].split(b"\0", 1)[0].decode().lower() != clip:
            continue
        length, transtime = struct.unpack_from("<2f", data, offset + 112)
        root = data[offset + 120:offset + 184].split(b"\0", 1)[0].decode()
        pointer, count = uint(offset + 184), uint(offset + 188)
        events = [(struct.unpack_from("<f", data, 12 + pointer + 36 * i)[0],
                   data[12 + pointer + 36 * i + 4:12 + pointer + 36 * i + 36].split(b"\0", 1)[0].decode())
                  for i in range(count)]
        return dict(length=length, transtime=transtime, root=root, events=events)
    raise KeyError(clip)


def hand_points(library: Library, prefix: str, side: str) -> np.ndarray:
    """Every mesh vertex of the default (001) hand part in its attachment frame."""
    name = f"{prefix}_hand{side}001"
    data = library.data(name)
    if data is None or not mdl.binary(data):
        raise RuntimeError(f"Missing compiled hand part {name}")
    base = 12
    raw = base + struct.unpack_from("<I", data, 4)[0]
    uint = lambda offset: struct.unpack_from("<I", data, base + offset)[0]
    model = Model(data, False)
    points = []

    def walk(pointer, parent_matrix):
        controllers = {}
        keys, key_count = uint(pointer + 0x54), uint(pointer + 0x58)
        values = uint(pointer + 0x60)
        for index in range(key_count):
            kind, rows, times, start, columns = struct.unpack_from("<IHHHB", data, base + keys + index * 12)
            if kind in (8, 20) and rows:
                controllers[kind] = struct.unpack_from(f"<{columns}f", data, base + values + start * 4)
        matrix = parent_matrix @ transform(quat_matrix(controllers.get(20, (0, 0, 0, 1))),
                                           np.array(controllers.get(8, (0, 0, 0)), float))
        if uint(pointer + 0x6C) & 0x20 and uint(pointer + 0xDC):
            count = struct.unpack_from("<H", data, base + pointer + 0x230)[0]
            vertices = np.array(struct.unpack_from(f"<{3 * count}f", data, raw + uint(pointer + 0x22C))).reshape(-1, 3)
            points.append((matrix[:3, :3] @ vertices.T).T + matrix[:3, 3])
        children, child_count = uint(pointer + 0x48), uint(pointer + 0x4C)
        for index in range(child_count):
            walk(uint(children + 4 * index), matrix)

    walk(uint(0x48), np.eye(4))
    if not points:
        raise RuntimeError(f"{name} has no rendered mesh")
    return np.vstack(points)


def skeleton_nodes(data: bytes) -> list[tuple[str, int | None, dict]]:
    """(name, parent index, rest controllers) of a compiled or ASCII model's geometry."""
    if mdl.binary(data):
        return [(name, parent, controllers) for name, parent, _, controllers, _ in Model(data, False).nodes]
    nodes, index = [], {}
    for _, name, props in mdl.parse_nodes(data.decode("latin1").split("endmodelgeom", 1)[0]):
        parent = props.get("parent", ["null"])[0].lower()
        controllers = {}
        if "position" in props:
            controllers[8] = ((0.0,), [tuple(float(v) for v in props["position"][:3])])
        if "orientation" in props:
            x, y, z, angle = (float(v) for v in props["orientation"][:4])
            length = math.sqrt(x * x + y * y + z * z)
            half = math.sin(angle / 2)
            controllers[20] = ((0.0,), [(0.0, 0.0, 0.0, 1.0) if length < 1e-12 else
                                        (x / length * half, y / length * half, z / length * half, math.cos(angle / 2))])
        index.setdefault(name, len(nodes))
        nodes.append((name, index.get(parent), controllers))
    return nodes


def animation_scale(data: bytes) -> float:
    """The wearer's animation scale; the engine multiplies clip position keys by it."""
    if mdl.binary(data):
        return struct.unpack_from("<f", data, 176)[0]
    match = re.search(r"(?im)^\s*setanimationscale\s+(\S+)", data.decode("latin1"))
    return float(match[1]) if match else 1.0


class Rig:
    """A body skeleton posed by a clip. Tracks follow node names, which the chain shares."""

    def __init__(self, skeleton: list, clip, scale: float = 1.0):
        self.nodes = skeleton
        self.scale = scale
        self.index = {}
        for i, node in enumerate(self.nodes):
            self.index.setdefault(node[0], i)
        self.tracks = {}
        for name, _, _, controllers, _ in clip[1]:
            self.tracks.setdefault(name, controllers)

    def local(self, name, time, override=None):
        track = self.tracks.get(name, {})
        controllers = {**self.nodes[self.index[name]][2], **track}
        position = sample(controllers[8], time) if 8 in controllers else np.zeros(3)
        if 8 in track:
            position = position * self.scale
        rotation = sample(controllers[20], time, True) if 20 in controllers else np.array([0, 0, 0, 1.0])
        if override and name in override:
            position = override[name].get("position", position)
            rotation = override[name].get("rotation", rotation)
        return transform(quat_matrix(rotation), position)

    def world(self, name, time, override=None, cache=None):
        cache = {} if cache is None else cache
        if name not in cache:
            parent = self.nodes[self.index[name]][1]
            matrix = self.local(name, time, override)
            if parent is not None:
                matrix = self.world(self.nodes[parent][0], time, override, cache) @ matrix
            cache[name] = matrix
        return cache[name]

    def hand_frames(self, time, override=None):
        cache = {}
        weapon = np.linalg.inv(self.world(HOOK, time, override, cache))
        return (weapon @ self.world("rhand_g", time, override, cache),
                weapon @ self.world("lhand_g", time, override, cache))


# ---------------------------------------------------------------- solving
def translate_fit(points, target, iterations=30) -> np.ndarray:
    """Weapon-space translation that best places one hand surface on another."""
    shift = target.mean(0) - points.mean(0)
    for _ in range(iterations):
        moved = points + shift
        distances = ((moved[:, None, :] - target[None, :, :]) ** 2).sum(-1)
        step = 0.5 * ((target[distances.argmin(1)] - moved).mean(0) + (target - moved[distances.argmin(0)]).mean(0))
        shift = shift + step
        if np.linalg.norm(step) < 1e-6:
            break
    return shift


def top_offset(reference, points) -> float:
    """Height that puts a support palm's upper contact surface on the reference's."""
    def tops(values):
        cells = {}
        for p in values:
            key = (round(p[0] / CELL), round(p[2] / CELL))
            cells[key] = max(cells.get(key, -1e9), p[1])
        return cells
    a, b = tops(reference), tops(points)
    shared = [a[key] - b[key] for key in a if key in b]
    return float(np.median(shared)) if shared else 0.0


def key_times(*clips) -> list[float]:
    times = set()
    for clip in clips:
        for name, _, _, controllers, _ in clip[1]:
            if name in CHAIN_NODES:
                for kind in (8, 20):
                    if kind in controllers:
                        times.update(round(t, 6) for t in controllers[kind][0])
    return sorted(times) or [0.0]


def placed(frame, points):
    return (frame[:3, :3] @ points.T).T + frame[:3, 3]


def solve(library: Library, body: str, clip: str) -> dict:
    reference = body_rig(library, REFERENCE, clip)
    owner, source = library.clip(body, clip, library.inherited_parent(body))
    rig = Rig(skeleton_nodes(library.data(body)), source.clips[clip], animation_scale(library.data(body)))
    reference_owner = library.clip(REFERENCE, clip)[1]
    HR0, LR0 = reference.hand_frames(0.0)
    HB0, LB0 = rig.hand_frames(0.0)
    reference_fist = placed(HR0, hand_points(library, REFERENCE, "r"))
    reference_palm = placed(LR0, hand_points(library, REFERENCE, "l"))
    # Reported only: the firing hand keeps the inherited hook (see module docstring).
    hook_shift = translate_fit(placed(HB0, hand_points(library, hand_shape(body), "r")), reference_fist)
    palm = placed(LB0, hand_points(library, hand_shape(body), "l"))
    palm_shift = translate_fit(palm, reference_palm)
    palm_shift[1] += top_offset(reference_palm, palm + palm_shift)
    tracks = {name: [] for name in ARM}
    report = []
    override = None
    for time in key_times(reference_owner.clips[clip], source.clips[clip]):
        _, LR = reference.hand_frames(time)
        _, LB = rig.hand_frames(time)
        cache = {}
        weapon = rig.world(HOOK, time, override, cache)
        want = LB.copy()
        want[:3, 3] = LR[:3, 3] + LB0[:3, 3] + palm_shift - LR0[:3, 3]
        target = weapon @ want
        W1, W2, W3 = (rig.world(name, time, override, cache) for name in ARM)
        torso = rig.world(rig.nodes[rig.nodes[rig.index[ARM[0]]][1]][0], time, override, cache)
        S, E0, P0, P = W1[:3, 3], W2[:3, 3], W3[:3, 3], target[:3, 3]
        upper, lower = np.linalg.norm(E0 - S), np.linalg.norm(P0 - E0)
        reach = P - S
        distance = np.linalg.norm(reach)
        clamped = min(max(distance, abs(upper - lower) + 1e-4), upper + lower - 1e-4)
        direction = reach / distance
        along = (upper * upper - lower * lower + clamped * clamped) / (2 * clamped)
        radius = math.sqrt(max(0.0, upper * upper - along * along))
        pole = (E0 - S) - direction * np.dot(E0 - S, direction)
        pole /= np.linalg.norm(pole)
        E = S + direction * along + pole * radius
        D1 = rotation_between(E0 - S, E - S)
        R1 = D1 @ W1[:3, :3]
        D2 = rotation_between(D1 @ (P0 - E0), S + direction * clamped - E)
        R2 = D2 @ D1 @ W2[:3, :3]
        R3 = target[:3, :3]
        tracks["lbicep_g"].append((time, None, matrix_quat(torso[:3, :3].T @ R1)))
        tracks["lforearm_g"].append((time, None, matrix_quat(R1.T @ R2)))
        tracks["lhand_g"].append((time, None, matrix_quat(R2.T @ R3)))
        report.append(dict(time=time, short_mm=(distance - clamped) * 1000,
                           shoulder=angle_degrees(D1), elbow=angle_degrees(D2)))
    return dict(body=body, clip=clip, owner=owner, source=source, tracks=tracks, report=report,
                hook_shift=hook_shift, palm_shift=palm_shift)


def body_rig(library: Library, body: str, clip: str) -> Rig:
    """The body posed by the clip it resolves now (its rifle bank once spliced)."""
    _, owner = library.clip(body, clip)
    data = library.data(body)
    return Rig(skeleton_nodes(data), owner.clips[clip], animation_scale(data))


def placement_error(library: Library, body: str, clip: str) -> tuple[np.ndarray, np.ndarray]:
    """Remaining (firing fist, support palm) weapon-space offsets from the reference at clip start."""
    reference = body_rig(library, REFERENCE, clip)
    rig = body_rig(library, body, clip)
    HR, LR = reference.hand_frames(0.0)
    HB, LB = rig.hand_frames(0.0)
    reference_palm = placed(LR, hand_points(library, REFERENCE, "l"))
    palm = placed(LB, hand_points(library, hand_shape(body), "l"))
    palm_shift = translate_fit(palm, reference_palm)
    palm_shift[1] = top_offset(reference_palm, palm)
    fist_shift = translate_fit(placed(HB, hand_points(library, hand_shape(body), "r")),
                               placed(HR, hand_points(library, REFERENCE, "r")))
    return fist_shift, palm_shift


# ---------------------------------------------------------------- emission
def fmt(value) -> str:
    return format(float(value), ".17g")


def key_lines(kind, rows, indent="    "):
    return [f"{indent}{kind}key {len(rows)}"] + [indent + "  " + " ".join(fmt(v) for v in row) for row in rows]


def bank_geometry(supermodel: Model, results: list[dict]) -> list[tuple[str, int | None, dict]]:
    """The supermodel's skeleton plus every node the inherited clips animate beyond it.

    A clip can only name its own model's geometry nodes; without these the bank would
    drop the inherited cloak, belt and flap tracks.
    """
    nodes = [(name, parent, controllers) for name, parent, _, controllers, _ in supermodel.nodes]
    index = {name: i for i, (name, _, _) in enumerate(nodes)}
    for result in results:
        clip_nodes = result["source"].clips[result["clip"]][1]
        owner = {name: controllers for name, _, _, controllers, _ in result["source"].nodes}
        for name, parent, _, _, _ in clip_nodes[1:]:
            if name not in index:
                index[name] = len(nodes)
                nodes.append((name, 0 if parent == 0 else index[clip_nodes[parent][0]], owner.get(name, {})))
    # The compiler stores geometry depth first; keep the source in that order.
    children = {}
    for i, (_, parent, _) in enumerate(nodes):
        children.setdefault(parent, []).append(i)
    order, pending = [], [0]
    while pending:
        current = pending.pop()
        order.append(current)
        pending.extend(reversed(children.get(current, [])))
    position = {old: new for new, old in enumerate(order)}
    return [(nodes[i][0], None if nodes[i][1] is None else position[nodes[i][1]], nodes[i][2]) for i in order]


def geometry_lines(nodes: list[tuple[str, int | None, dict]], bank: str) -> list[str]:
    lines = [f"node dummy {bank}", "  parent NULL", "endnode"]
    for name, parent, controllers in nodes[1:]:
        parent_name = bank if parent == 0 else nodes[parent][0]
        lines += [f"node dummy {name}", f"  parent {parent_name}"]
        position = controllers.get(8, ((0,), [(0, 0, 0)]))[1][0]
        rotation = controllers.get(20, ((0,), [(0, 0, 0, 1)]))[1][0]
        lines.append("  position " + " ".join(fmt(v) for v in position))
        lines.append("  orientation " + " ".join(fmt(v) for v in axis_angle(rotation)))
        lines.append("endnode")
    return lines


def clip_lines(result: dict, bank: str) -> list[str]:
    clip = result["clip"]
    header = animation_header(result["source"].data, clip)
    nodes = result["source"].clips[clip][1]
    names = [node[0] for node in nodes[1:]]
    if len(set(names)) != len(names):
        raise ValueError(f"{result['owner']}/{clip}: repeated node names cannot be copied by name")
    lines = [f"newanim {clip} {bank}", f"  length {fmt(header['length'])}",
             f"  transtime {fmt(header['transtime'])}", f"  animroot {header['root']}"]
    lines += [f"  event {fmt(time)} {name}" for time, name in header["events"]]
    lines += [f"  node dummy {bank}", "    parent NULL", "  endnode"]
    emitted = []
    for index, (name, parent, _, controllers, _) in enumerate(nodes):
        if index == 0:
            continue
        unknown = set(controllers) - {8, 20}
        if unknown:
            raise ValueError(f"{result['owner']}/{clip}/{name}: unsupported controllers {sorted(unknown)}")
        emitted.append(name)
        parent_name = bank if parent == 0 else nodes[parent][0]
        lines += [f"  node dummy {name}", f"    parent {parent_name}"]
        if name in result["tracks"]:
            rows = result["tracks"][name]
            if rows[0][1] is not None:
                lines += key_lines("position", [(t, *p) for t, p, _ in rows])
            lines += key_lines("orientation", [(t, *axis_angle(q)) for t, _, q in rows])
        else:
            if 8 in controllers:
                times, values = controllers[8]
                lines += key_lines("position", [(t, *v) for t, v in zip(times, values)])
            if 20 in controllers:
                times, values = controllers[20]
                lines += key_lines("orientation", [(t, *axis_angle(v)) for t, v in zip(times, values)])
        lines.append("  endnode")
    missing = [name for name in result["tracks"] if name not in emitted]
    if missing:
        raise ValueError(f"{result['owner']}/{clip}: clip omits retargeted nodes {missing}")
    lines.append(f"doneanim {clip} {bank}")
    return lines


def bank_source(library: Library, body: str, results: list[dict]) -> str:
    bank = bank_name(body)
    parent = library.inherited_parent(body)
    lines = [f"# SWLOR authored animations for {bank}", f"newmodel {bank}", f"setsupermodel {bank} {parent}",
             "classification character", "setanimationscale 1", f"beginmodelgeom {bank}"]
    lines += geometry_lines(bank_geometry(library.model(parent, False), results), bank)
    lines.append(f"endmodelgeom {bank}")
    for result in results:
        lines += clip_lines(result, bank)
    lines.append(f"donemodel {bank}")
    return "\n".join(lines) + "\n"


def spliced_root(library: Library, body: str) -> bytes:
    """The body root for this run.

    A compiled root changes only its supermodel name, keeping every native part ID.
    An ASCII root is written against its inherited supermodel: the compiler numbers
    joints its parent lacks by chain depth, so compiling under the bank would renumber
    them. `--finalize` splices it after CompileModels.py compiles it.
    """
    data = library.data(body)
    if mdl.binary(data):
        result = bytearray(data)
        result[180:244] = bank_name(body).encode("ascii").ljust(64, b"\0")
        return bytes(result)
    text, count = re.subn(r"(?im)^(\s*setsupermodel\s+\S+\s+)\S+", lambda m: m[1] + library.inherited_parent(body),
                          data.decode("latin1"), count=1)
    if count != 1:
        raise ValueError(f"{body}: ASCII root has no supermodel line")
    return text.encode("latin1")


def generate(library: Library) -> dict[Path, bytes]:
    outputs = {}
    for body in BODIES:
        results = [solve(library, body, clip) for clip in CLIPS]
        for result in results:
            worst = max(result["report"], key=lambda r: r["short_mm"])
            print(f"{body} {result['clip']:8s} from {result['owner']:16s} support "
                  f"{np.round(result['palm_shift'] * 1000, 1)} mm, firing fist offset kept "
                  f"{np.round(result['hook_shift'] * 1000, 1)} mm, "
                  f"max reach shortfall {worst['short_mm']:.1f} mm", flush=True)
        outputs[ROOT / BANK_DIRECTORY / f"{bank_name(body)}.mdl"] = bank_source(library, body, results).encode("ascii")
        outputs[ROOT / ROOT_DIRECTORY / f"{body}.mdl"] = spliced_root(library, body)
    return outputs


def raw_controllers(data: bytes, node: int) -> list[tuple[int, int, int, int, int, int]]:
    """(kind, rows, time offset, value offset, columns, values base) of a compiled node's controllers."""
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    keys, count, values = uint(node + 84), uint(node + 88), 12 + uint(node + 96)
    result = []
    for index in range(count):
        kind, rows, times, start, columns = struct.unpack_from("<IHHHB", data, 12 + keys + index * 12)
        result.append((kind, rows, values + times * 4, values + start * 4, columns, values))
    return result


def restore_inherited_tracks(library: Library, body: str) -> bytes:
    """The compiled bank repeating every inherited clip node, part ID and float bit for bit.

    The engine maps clip tracks to the wearer's joints by native part ID, and the
    compiler numbers this bank's joints against its own parent, which differs from
    the clip owner's for cloak joints on large phenotypes. The legacy compiler's
    axis-angle conversion also moves some quaternions by one float ULP, and robe
    bridges hash copied garment tracks exactly.
    """
    data = bytearray(library.data(bank_name(body)))
    bank = Model(bytes(data))
    for clip in CLIPS:
        _, source = library.clip(body, clip, library.inherited_parent(body))
        ours_nodes, their_nodes = bank.clips[clip][1][1:], source.clips[clip][1][1:]
        if [(n[0], n[1]) for n in ours_nodes] != [(n[0], n[1]) for n in their_nodes]:
            raise ValueError(f"{bank_name(body)}/{clip}: compiled nodes differ from the inherited clip")
        for (name, _, _, _, offset), (_, _, part, _, source_offset) in zip(ours_nodes, their_nodes):
            struct.pack_into("<i", data, offset + 28, part)
            if name in ARM:
                continue
            ours = raw_controllers(data, offset)
            theirs = raw_controllers(source.data, source_offset)
            if [(k, r, c) for k, r, _, _, c, _ in ours] != [(k, r, c) for k, r, _, _, c, _ in theirs]:
                raise ValueError(f"{bank_name(body)}/{clip}/{name}: compiled controllers differ from the inherited clip")
            for (_, rows, times, values, columns, _), (_, _, source_times, source_values, _, _) in zip(ours, theirs):
                data[times:times + rows * 4] = source.data[source_times:source_times + rows * 4]
                data[values:values + rows * columns * 4] = source.data[source_values:source_values + rows * columns * 4]
    return bytes(data)


def finalize(library: Library) -> None:
    import StripInheritedBoneTracks as sources
    for body in BODIES:
        path = ROOT / BANK_DIRECTORY / f"{bank_name(body)}.mdl"
        if not mdl.binary(path.read_bytes()):
            raise RuntimeError(f"Compile {path.name} with CompileModels.py --apply before finalizing")
        path.write_bytes(restore_inherited_tracks(library, body))
        library.cache.pop(bank_name(body), None)
        sources.update_source(ROOT, path, check_only=False)
        root = ROOT / ROOT_DIRECTORY / f"{body}.mdl"
        if not mdl.binary(root.read_bytes()):
            raise RuntimeError(f"Compile {root.name} with CompileModels.py --apply before finalizing")
        root.write_bytes(spliced_root(library, body))
        library.cache.pop(body, None)
        print("finalized", path.name, root.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-data", type=Path, required=True, help="Installed EE data directory for stock roots and banks")
    parser.add_argument("--output", type=Path, help="Write into this staging directory instead of the HAK folders")
    parser.add_argument("--finalize", action="store_true",
                        help="After CompileModels.py --apply: restore exact inherited part IDs and tracks in the compiled banks")
    args = parser.parse_args()
    library = Library(args.game_data)
    if args.finalize:
        finalize(library)
        return
    outputs = generate(library)
    for path, data in outputs.items():
        destination = args.output / path.relative_to(ROOT) if args.output else path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        print("wrote", destination)


if __name__ == "__main__":
    main()
