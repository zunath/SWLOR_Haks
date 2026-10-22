#!/usr/bin/env python3
"""Drape robe cloth helpers in emote clips that carry no cloth tracks.

Coat panels, skirt bones and belt flaps animate only from tracks authored for
stock clips. SWLOR's custom emote slots (crouch, kneel, squat and the rest) have
none, so the engine latches every helper in its previous pose and a kneeling
wearer's coat juts out from the hips like a board. For each such clip this
drapes the helpers from the wearer's own pose: each cloth chain hangs from its
hip anchor, runs over the knee on its side only while that knee reaches out the
chain's side (front cloth forward, back cloth backward), and lies along the
floor below a small clearance (toward the feet when the wearer lies down). Body
tracks, authored cloth tracks, every other clip and the skeleton are unchanged.

`--check/--apply` repairs the installed banks in place, like
NumberRobeGarmentParts.py; GenerateRobeRgbModels applies the same tracks to
newly compiled bridges.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct

import RobeAnimationBanks as banks
import RobePoseAudit as poses

EMOTE = re.compile(r"custom\d+(?:start|lp|end)")
# Bridges of the playable body families (human, elf, half-orc, dwarf).
PLAYABLE = re.compile(r"p[fm][heod]_ra\d{3}")
REFERENCE_CLIP = "pause1"
# Drape geometry, in metres: a zero-offset child moves with its parent, a joint
# dropping less than DROP to its child is a container that turns with the pelvis,
# a chain's last joint hangs LEAF further, cloth over a knee sits LIFT above it,
# cloth stops GROUND above the floor, and anchors within CENTRE of the pelvis
# hang between both knees. A pelvis whose up axis has a vertical component
# below UPRIGHT is lying down.
TWIN, DROP, LEAF, LIFT, GROUND, CENTRE, UPRIGHT = .01, .03, .4, .1, .04, .05, .5
# Garment helpers that ride the arms, torso or head (and the tail) already
# follow their wearer bone.
UPPER_BODY = re.compile(r"rg_(?:[lr]hand|[lr]forearm|[lr]shoulder_g|impact|head|wings|tail|"
                        r"handconjure|headconjure)(?:_\d+)?")
ORIENTATION = 20


def _uint(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


class Node:
    """One dummy node of a compiled clip; key and value bytes are kept verbatim."""

    def __init__(self, header, keys=b"", values=b"", children=None):
        self.header, self.keys, self.values = header, keys, values
        self.children = children or []

    @property
    def name(self):
        return self.header[32:64].split(b"\0", 1)[0].decode("ascii").lower()


def read_clip(component):
    data = component.data
    base = component.start
    def node(offset):
        offset -= base
        keys, key_count = _uint(data, offset + 84), _uint(data, offset + 88)
        values, value_count = _uint(data, offset + 96), _uint(data, offset + 100)
        children = _uint(data, offset + 72) - base
        return Node(bytes(data[offset:offset + 112]),
                    bytes(data[keys - base:keys - base + key_count * 12]) if key_count else b"",
                    bytes(data[values - base:values - base + value_count * 4]) if value_count else b"",
                    [node(_uint(data, children + index * 4)) for index in range(_uint(data, offset + 76))])
    events = _uint(data, 188)
    event_bytes = bytes(data[196:196 + events * 36])
    return bytes(data[:196]), event_bytes, node(_uint(data, 72))


def write_clip(header, events, root, model_name):
    """CleanModels layout: header, events, then per node header/keys/values/children."""
    out = bytearray(header)
    pointers, names, count = [72], [], 0
    if header[120:184].split(b"\0", 1)[0].decode("ascii") == model_name:
        names.append((120, 64))
    struct.pack_into("<3I", out, 184, 196 if events else 0, len(events) // 36, len(events) // 36)
    if events:
        pointers.append(184)
        out.extend(events)
    def emit(node, parent):
        nonlocal count
        count += 1
        start = len(out)
        out.extend(node.header)
        if node.header[32:64].split(b"\0", 1)[0].decode("ascii") == model_name:
            names.append((start + 32, 32))
        struct.pack_into("<I", out, start + 68, parent)
        if parent:
            pointers.append(start + 68)
        for field, raw, stride in ((84, node.keys, 12), (96, node.values, 4)):
            struct.pack_into("<3I", out, start + field, len(out) if raw else 0, len(raw) // stride, len(raw) // stride)
            if raw:
                pointers.append(start + field)
                out.extend(raw)
        table = len(out)
        struct.pack_into("<3I", out, start + 72, table if node.children else 0, len(node.children), len(node.children))
        if node.children:
            pointers.append(start + 72)
        out.extend(bytes(4 * len(node.children)))
        for index, child in enumerate(node.children):
            pointers.append(table + index * 4)
            struct.pack_into("<I", out, table + index * 4, len(out))
            emit(child, start)
    struct.pack_into("<I", out, 72, len(out))
    emit(root, 0)
    struct.pack_into("<I", out, 76, count)
    return banks.Component(0, bytes(out), tuple(pointers), tuple(names))


class Rig:
    """Cloth chains of one bridge, measured in its idle (pause1) pose."""

    def __init__(self, data):
        self.model = poses.Model(data)
        nodes = self.model.nodes
        self.children = {index: [] for index in range(len(nodes))}
        for index, (_, parent, _, _, _) in enumerate(nodes):
            if parent is not None:
                self.children[parent].append(index)
        # pose() keys repeated node names as <name>_copy<index>.
        seen, self.keys = set(), []
        for index, (name, _, _, _, _) in enumerate(nodes):
            self.keys.append(name if name not in seen else f"{name}_copy{index}")
            seen.add(name)
        reference = self.model.clips.get(REFERENCE_CLIP)
        idle = {part for _, _, part, controls, _ in reference[1] if part >= 0 and ORIENTATION in controls} \
            if reference else set()
        self.cloth = [index for index, (name, _, part, _, _) in enumerate(nodes)
                      if name.startswith("rg_") and part in idle and not UPPER_BODY.fullmatch(name)]
        self.reference = self.model.pose(reference, 0) if self.cloth else None
        self.roles = {}
        cloth = set(self.cloth)
        for index in self.cloth:  # Geometry order: parents first.
            position = self._ref(index)[0]
            kids = [child for child, node in enumerate(nodes) if node[1] == index and child in cloth]
            twin = lambda child: _distance(self._ref(child)[0], position) < TWIN
            parent = nodes[index][1]
            if parent in cloth and _distance(self._ref(parent)[0], position) < TWIN:
                self.roles[index] = ("twin",)
                continue
            hanging = [child for child in kids if not twin(child)]
            if len(hanging) > 1 or (hanging and self._ref(hanging[0])[0][2] > position[2] - DROP):
                self.roles[index] = ("pelvis",)
                continue
            if hanging:
                target = self._ref(hanging[0])[0]
                length = _distance(target, position)
                direction = _unit(_sub(target, position))
            else:
                if self.roles.get(parent, ("",))[0] != "chain":
                    raise ValueError(f"{self.model.parent}: cloth helper {nodes[index][0]} hangs from nothing")
                length, direction = LEAF, self.roles[parent][3]
            if self.roles.get(parent, ("",))[0] == "chain":
                top, start = self.roles[parent][1], self.roles[parent][2] + _distance(position, self._ref(parent)[0])
            else:
                top, start = index, 0.0
            self.roles[index] = ("chain", top, start, direction, length)
        self.chains = {index: self._classify(index) for index, role in self.roles.items()
                       if role[0] == "chain" and role[1] == index}

    def _ref(self, index):
        return self.reference[self.keys[index]]

    def _classify(self, top):
        """Front or back of the pelvis, and the knee (or both) it hangs beside."""
        anchor = self._ref(top)[0]
        pelvis = self.reference["pelvis_g"][0]
        sides = {side: self.reference[f"{side}thigh_g"][0] for side in "lr"}
        if anchor[0] < pelvis[0] - CENTRE:
            legs = ("l",)
        elif anchor[0] > pelvis[0] + CENTRE:
            legs = ("r",)
        else:
            legs = ("l", "r")
        lateral = anchor[0] - sum(sides[side][0] for side in legs) / len(legs)
        return 1 if anchor[1] > pelvis[1] else -1, legs, lateral

    def missing(self, clip):
        """Cloth helpers an emote clip leaves without tracks (authored ones are kept)."""
        if not EMOTE.fullmatch(clip):
            return set()
        tracked = {part for _, _, part, controls, _ in self.model.clips[clip][1] if controls}
        return {index for index in self.cloth if self.model.nodes[index][2] not in tracked}

    def _path(self, top, anchor, pose, turn):
        """Polyline from the anchor: over the knee when the leg reaches out that side, down, along the floor."""
        facing, legs, lateral = self.chains[top]
        knees = tuple(sum(pose[f"{side}shin_g"][0][axis] for side in "lr") / 2 for axis in range(3))
        if poses.rotate(turn, (0, 0, 1))[2] > UPRIGHT:
            # Front cloth falls ahead of the wearer and back cloth behind.
            heading = tuple(facing * value for value in _horizontal(poses.rotate(turn, (0, 1, 0))))
        else:
            # Lying down, all cloth trails toward the feet.
            heading = (_horizontal(_sub(knees, pose["pelvis_g"][0])) or
                       _horizontal(poses.rotate(turn, (0, 0, -1))) or (0.0, 1.0, 0.0))
        knee = tuple(sum(pose[f"{side}shin_g"][0][axis] for side in legs) / len(legs) for axis in range(3))
        offset = poses.rotate(turn, (lateral, 0, 0))
        knee = (knee[0] + offset[0], knee[1] + offset[1], max(GROUND, knee[2] + LIFT))
        points = [anchor]
        if _dot(_sub(knee, anchor), heading) > 0 and knee[2] < anchor[2]:
            points.append(knee)
        last = points[-1]
        if last[2] > GROUND:
            points.append((last[0], last[1], GROUND))
        return points, heading

    @staticmethod
    def _along(path, distance):
        points, heading = path
        for start, end in zip(points, points[1:]):
            length = _distance(start, end)
            if distance <= length:
                return tuple(a + (b - a) * distance / length for a, b in zip(start, end)) if length else start
            distance -= length
        return tuple(a + b * distance for a, b in zip(points[-1], heading))

    def tracks(self, clip, missing):
        """Return {node index: [(time, quaternion)]} for the missing cloth helpers."""
        length, nodes = self.model.clips[clip]
        times = sorted({round(time, 6) for _, _, _, controls, _ in nodes for stamps, _ in controls.values()
                        for time in stamps} | {0.0, round(length, 6)})
        result = {index: [] for index in missing}
        for time in times:
            pose = self.model.pose(self.model.clips[clip], time)
            turn = poses.multiply(pose["pelvis_g"][1], _conj(self.reference["pelvis_g"][1]))
            world, paths = {}, {}
            for index in self.cloth:
                parent = self.model.nodes[index][1]
                parent_ref = self._ref(parent)
                parent_now = world[parent] if parent in world else pose[self.keys[parent]]
                ref = self._ref(index)
                offset = poses.rotate(_conj(parent_ref[1]), _sub(ref[0], parent_ref[0]))
                position = _add(parent_now[0], poses.rotate(parent_now[1], offset))
                role = self.roles[index]
                if index not in missing:
                    # Authored tracks stay; their children hang from the authored pose.
                    world[index] = pose[self.keys[index]][:2]
                    if role[0] == "chain" and role[1] == index:
                        paths[index] = self._path(index, world[index][0], pose, turn)
                    continue
                if role[0] == "pelvis":
                    rotation = poses.multiply(turn, ref[1])
                elif role[0] == "twin":
                    rotation = poses.multiply(poses.multiply(parent_now[1], _conj(parent_ref[1])), ref[1])
                else:
                    _, top, start, direction, segment = role
                    if top == index:
                        paths[top] = self._path(top, position, pose, turn)
                    target = self._along(paths[top], start + segment)
                    wanted = _unit(_sub(target, position)) if _distance(target, position) > 1e-4 else direction
                    rotation = poses.multiply(_arc(direction, wanted), ref[1])
                world[index] = (position, rotation)
                local = poses.multiply(_conj(parent_now[1]), rotation)
                previous = result[index][-1][1] if result[index] else None
                if previous is not None and _dot(local, previous) < 0:
                    local = tuple(-value for value in local)
                result[index].append((time, local))
        return result


def _conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _distance(a, b):
    return math.sqrt(_dot(_sub(a, b), _sub(a, b)))


def _unit(v):
    length = math.sqrt(_dot(v, v))
    return tuple(x / length for x in v)


def _horizontal(v):
    length = math.hypot(v[0], v[1])
    return (v[0] / length, v[1] / length, 0.0) if length > .3 else None


def _arc(a, b):
    """Shortest rotation taking unit vector a onto unit vector b."""
    cross = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    w = 1 + _dot(a, b)
    if w < 1e-6:
        axis = _unit((-a[1], a[0], 0) if abs(a[2]) < .9 else (0, -a[2], a[1]))
        return (*axis, 0.0)
    length = math.sqrt(_dot(cross, cross) + w * w)
    return (*(value / length for value in cross), w / length)


def _orientation_node(header, track):
    times = [time for time, _ in track]
    keys = struct.pack("<IHHHB", ORIENTATION, len(track), 0, len(track), 4) + b"\0"
    values = struct.pack(f"<{len(times)}f", *times) + b"".join(struct.pack("<4f", *quaternion) for _, quaternion in track)
    return Node(header, keys, values)


def _header(name, part):
    header = bytearray(112)
    struct.pack_into("<i", header, 28, part)
    header[32:64] = name.encode("ascii").ljust(32, b"\0")
    struct.pack_into("<I", header, 108, 1)
    return bytes(header)


def add_tracks(rig, component, model_name, tracks):
    header, events, root = read_clip(component)
    nodes = rig.model.nodes
    order = {index: position for position, index in enumerate(range(len(nodes)))}
    def path(index):
        result = []
        while nodes[index][1] is not None:
            result.append(index)
            index = nodes[index][1]
        return result[::-1]
    def ensure(parent_node, index):
        name = nodes[index][0]
        for child in parent_node.children:
            if child.name == name:
                return child
        child = Node(_header(name, nodes[index][2]))
        siblings = rig.children[nodes[index][1]]
        rank = {nodes[sibling][0]: siblings.index(sibling) for sibling in siblings}
        position = sum(1 for other in parent_node.children if rank.get(other.name, -1) < siblings.index(index))
        parent_node.children.insert(position, child)
        return child
    for index in sorted(tracks, key=order.get):
        current = root
        for step in path(index):
            current = ensure(current, step)
        if current.keys:
            raise ValueError(f"{nodes[index][0]}: emote clip already animates this helper")
        replacement = _orientation_node(current.header, tracks[index])
        current.keys, current.values = replacement.keys, replacement.values
    return write_clip(header, events, root, model_name)


def fill(data):
    """Return the bridge with cloth tracks added to every emote clip lacking them, and those clips."""
    model = banks.CompiledBridge(data)
    if not PLAYABLE.fullmatch(model.name):
        return data, []
    rig = Rig(data)
    clips, changed = [], []
    for name, component in zip(model.clip_names, model.clips):
        missing = rig.missing(name)
        if missing:
            component = add_tracks(rig, component, model.name, rig.tracks(name, missing))
            changed.append(name)
        clips.append(component)
    if not changed:
        return data, []
    result = model.pack(model.name, model.parent, clips)
    banks.validate_motion_revision(data, result, changed)
    return result, changed


def missing_motion(data):
    rig = Rig(data)
    return [name for name in rig.model.clips if rig.missing(name)]


def main():
    import GenerateRobeRgbModels as generator
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = generator.ROOT
    manifest_path = root / "tools" / "RobeRgbModels.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    active = generator.fresh_active_models()
    outputs, records, pending = {}, {}, 0
    for head, record in sorted(manifest["animation_bank_sets"].items()):
        if not PLAYABLE.fullmatch(head):
            continue
        original = generator.owned_animation_bytes(head, manifest, active)
        patched, changed = fill(original)
        if not changed:
            continue
        pending += len(changed)
        parts = banks.split(patched)
        if list(parts) != record["parts"]:
            raise ValueError(f"{head}: emote tracks changed the bank layout")
        for name, part in parts.items():
            path = root / generator.model_directory(name) / f"{name}.mdl"
            if path.read_bytes() != part:
                outputs[path] = (path.read_bytes(), part)
        records[head] = {**record, "source_sha256": hashlib.sha256(patched).hexdigest()}
        print(f"{head}: cloth helper tracks for {len(changed)} emote clips", flush=True)
    if args.check and pending:
        raise ValueError(f"{pending} robe emote clips leave cloth helpers latched; run FillRobeEmoteMotion.py --apply")
    if args.apply:
        if manifest_path.read_bytes() != manifest_bytes or any(
                path.read_bytes() != before for path, (before, _) in outputs.items()):
            raise ValueError("Robe assets changed during validation")
        for path, (_, after) in outputs.items():
            path.write_bytes(after)
            manifest["files"][path.relative_to(root).as_posix()] = hashlib.sha256(after).hexdigest()
        manifest["animation_bank_sets"].update(records)
        for name in ("FillRobeEmoteMotion.py", "GenerateRobeRgbModels.py"):
            relative = f"tools/{name}"
            manifest["files"][relative] = generator.file_digest(root / relative)
            if relative in manifest.get("validation_tool_files", {}):
                manifest["validation_tool_files"][relative] = manifest["files"][relative]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{'Applied' if args.apply else 'Validated'} {pending} emote clips in {len(records)} bridges")


if __name__ == "__main__":
    main()
