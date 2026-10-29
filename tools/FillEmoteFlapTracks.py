"""Hold the belt flaps at rest in the custom emotes (EmoteFlapTracks.json).

SWLOR's custom emotes (customN start/loop/end clips in the non-combat emote libraries,
/paraderest among them) carry no tracks for the body's back belt flap (fb1_g01, fb2_g01,
fb4_g01) or front tabard flap (tf1_g and its children), and the libraries' skeletons
lack those joints. The engine keeps an untracked joint where the previous clip left it,
and the standing idles lean both flaps 3 to 14 degrees sideways, so the flap-hung skirts
of ChestSkirtBindings.py hung off to one side in parade rest while the body stood
straight (test-server report, October 2026).

This copies the stock a_ba belt joint and flap chains (belt_g1 under rootdummy, with
their part IDs and bind transforms) into each library's skeleton, which a clip's joints
must mirror path for path (as RobeAnimations.validate_animation_parts checks for generated
banks), and gives every custom emote clip that
lacks them one rest-pose orientation key (identity on every body) on each flap joint, so
the flaps hang straight from the hips. The engine pairs tracks with the wearer's joints
by part ID; the dwarf and half-orc roots number their flaps apart, so their emotes are
unchanged. Everything new is appended after the model's existing data, so every existing
byte except the rewritten child-table pointers and node counts, every other clip and the
raw data stay as they were.

The originals come from the Git blobs recorded in EmoteFlapTracks.json, so --apply is
repeatable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

import FillRobeEmoteMotion as emote
import RobeAnimationBanks as banks
import RobePoseAudit as poses
from RobeAnimations import binary_parts

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools' / 'EmoteFlapTracks.json'
SKELETON = ROOT / 'sw_cr_creature' / 'a_ba.mdl'
EMOTE = re.compile(r'custom\d+(?:start|lp|end)?')
# The stock belt joint and its flap joints, each with the joint it hangs from (None: the root dummy).
JOINTS = (('belt_g1', None), ('fb1_g01', 'belt_g1'), ('fb2_g01', 'fb1_g01'), ('fb4_g01', 'fb2_g01'),
          ('tf1_g', 'belt_g1'), ('tf1l_g', 'tf1_g'), ('tf2_g', 'tf1_g'), ('tf3_g01', 'tf2_g'), ('tf1r_g', 'tf1_g'))
FLAPS = tuple(name for name, _ in JOINTS[1:])
POSITION, ORIENTATION = 8, emote.ORIENTATION
REST = (0., 0., 0., 1.)


def stock_joints() -> dict[str, tuple[int, tuple, tuple]]:
    """Each belt and flap joint's part ID, bind position and bind orientation in the stock skeleton."""
    skeleton = poses.Model(SKELETON.read_bytes(), False)
    nodes = {node[0]: node for node in skeleton.nodes}
    result = {}
    for name, parent in JOINTS:
        node = nodes[name]
        if skeleton.nodes[node[1]][0] != (parent or 'rootdummy'):
            raise ValueError(f'{name}: unexpected parent in the stock skeleton')
        orientation = node[3].get(ORIENTATION, ((0.,), [REST]))[1][0]
        if name in FLAPS and any(abs(a - b) > 1e-6 for a, b in zip(orientation, REST)):
            raise ValueError(f'{name}: expected an unrotated rest pose')
        result[name] = (node[2], node[3].get(POSITION, ((0.,), [(0., 0., 0.)]))[1][0], orientation)
    return result


def flap_parts(joints: dict | None = None) -> dict[str, int]:
    joints = joints or stock_joints()
    return {name: joints[name][0] for name in FLAPS}


def clip_offsets(model: bytes) -> list[int]:
    pointer, count = struct.unpack_from('<2I', model, 120)
    return [struct.unpack_from('<I', model, pointer + index * 4)[0] for index in range(count)]


def nodes(model: bytes, offset: int):
    """Yield every node offset of a tree; mesh-typed nodes carry their own child pointers."""
    stack = [offset]
    while stack:
        node = stack.pop()
        yield node
        children, count = struct.unpack_from('<2I', model, node + 72)
        stack.extend(struct.unpack_from('<I', model, children + index * 4)[0] for index in range(count))


def node_name(model: bytes, node: int) -> str:
    return model[node + 32:node + 64].split(b'\0', 1)[0].decode('ascii').lower()


def emit(model: bytearray, name: str, parent: int, joints: dict, geometry: bool) -> int:
    """Append a dummy node and the joints that hang from it, and return its offset.

    Skeleton nodes carry their bind position and orientation; clip nodes carry one rest key
    on each flap joint and none on the belt joint.
    """
    part, position, orientation = joints[name]
    start = len(model)
    model.extend(emote._header(name, part))
    struct.pack_into('<I', model, start + 68, parent)
    if geometry:
        keys = struct.pack('<IHHHB', POSITION, 1, 0, 1, 3) + b'\0' + struct.pack('<IHHHB', ORIENTATION, 1, 4, 5, 4) + b'\0'
        values = struct.pack('<9f', 0., *position, 0., *orientation)
    elif name in FLAPS:
        track = emote._orientation_node(b'', [(0., REST)])
        keys, values = track.keys, track.values
    else:
        keys = values = b''
    for field, raw, stride in ((84, keys, 12), (96, values, 4)):
        struct.pack_into('<3I', model, start + field, len(model) if raw else 0, len(raw) // stride, len(raw) // stride)
        model.extend(raw)
    children = [child for child, hangs in JOINTS if hangs == name]
    table = len(model)
    struct.pack_into('<3I', model, start + 72, table if children else 0, len(children), len(children))
    model.extend(bytes(4 * len(children)))
    for index, child in enumerate(children):
        struct.pack_into('<I', model, table + index * 4, emit(model, child, start, joints, geometry))
    return start


def graft(model: bytearray, header: int, joints: dict, geometry: bool) -> None:
    """Hang the belt joint under a tree's root dummy through a new child table and count its nodes."""
    tree = list(nodes(model, struct.unpack_from('<I', model, header + 72)[0]))
    anchor = next(node for node in tree if node_name(model, node) == 'rootdummy')
    children, count = struct.unpack_from('<2I', model, anchor + 72)
    old = [struct.unpack_from('<I', model, children + index * 4)[0] for index in range(count)]
    belt = emit(model, JOINTS[0][0], anchor, joints, geometry)
    table = len(model)
    for child in [*old, belt]:
        model.extend(struct.pack('<I', child))
    struct.pack_into('<3I', model, anchor + 72, table, len(old) + 1, len(old) + 1)
    struct.pack_into('<I', model, header + 76, struct.unpack_from('<I', model, header + 76)[0] + len(JOINTS))


def fill(data: bytes, joints: dict) -> tuple[bytes, list[str]]:
    """Return the emote library with the flap joints in its skeleton and rest tracks in its custom emotes."""
    if data[:4] != bytes(4):
        raise ValueError('Expected a compiled animation model')
    model_size, raw_size = struct.unpack_from('<2I', data, 4)
    if 12 + model_size + raw_size != len(data):
        raise ValueError('Unexpected compiled model layout')
    model = bytearray(data[12:12 + model_size])
    parts = flap_parts(joints)
    skeleton = {node_name(model, node) for node in nodes(model, struct.unpack_from('<I', model, 72)[0])}
    if skeleton & {name for name, _ in JOINTS}:
        raise ValueError('The skeleton already carries belt or flap joints')
    changed = []
    for start in clip_offsets(bytes(model)):
        clip = banks._name(memoryview(model), start + 8, 64)
        if not EMOTE.fullmatch(clip):
            continue
        tree = list(nodes(model, struct.unpack_from('<I', model, start + 72)[0]))
        if {struct.unpack_from('<i', model, node + 28)[0] for node in tree} & set(parts.values()):
            raise ValueError(f'{clip}: the clip already lists flap joints')
        graft(model, start, joints, False)
        changed.append(clip)
    if not changed:
        return data, []
    graft(model, 0, joints, True)
    return struct.pack('<3I', 0, len(model), raw_size) + bytes(model) + data[12 + model_size:], changed


def build(entry: dict, joints: dict) -> tuple[bytes, list[str]]:
    import ChestSkirtBindings as skirts
    original = skirts.source(entry['sourceBlob'])
    result, changed = fill(original, joints)
    if not changed:
        raise ValueError(f"{entry['path']}: no custom emote lacks flap tracks")
    validate(original, result, changed)
    return result, changed


def validate(before: bytes, after: bytes, changed: list[str]) -> None:
    """Every existing node and track stays; the skeleton and the changed clips only gain the belt and flaps."""
    added = {name for name, _ in JOINTS}
    skeleton = {path: part for path, part in binary_parts(after).items() if path and path[-1] in added}
    clip_roots = [12 + start + 72 for start in clip_offsets(after[12:12 + struct.unpack_from('<I', after, 4)[0]])]
    for root in clip_roots:
        grafted = {path: part for path, part in binary_parts(after, struct.unpack_from('<I', after, root)[0]).items()
                   if path and path[-1] in added}
        if grafted and grafted != skeleton:
            raise ValueError("A clip's belt and flap joints must mirror the skeleton's")
    old, new = poses.Model(before), poses.Model(after)
    strip = lambda listed: [(name, part, controllers) for name, _, part, controllers, _ in listed if name not in added]
    if list(old.clips) != list(new.clips) or strip(new.nodes) != strip(old.nodes):
        raise ValueError('Clip list or skeleton changed')
    if sorted(node[0] for node in new.nodes if node[0] in added) != sorted(added):
        raise ValueError('The skeleton must gain each belt and flap joint once')
    if before[12 + struct.unpack_from('<I', before, 4)[0]:] != after[12 + struct.unpack_from('<I', after, 4)[0]:]:
        raise ValueError('Raw data changed')
    for clip, (length, nodes_before) in old.clips.items():
        new_length, nodes_after = new.clips[clip]
        if length != new_length or strip(nodes_after) != strip(nodes_before):
            raise ValueError(f'{clip}: existing tracks changed')
        tracks = {name: controllers for name, _, _, controllers, _ in nodes_after if name in added}
        expected = {name: {ORIENTATION: ((0.,), [REST])} if name in FLAPS else {} for name in added} if clip in changed else {}
        if tracks != expected:
            raise ValueError(f'{clip}: expected one rest key per flap joint in the custom emotes only')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    joints = stock_joints()
    stale = []
    for name, entry in manifest['models'].items():
        path = ROOT / entry['path']
        data, changed = build(entry, joints)
        digest = hashlib.sha256(data).hexdigest()
        if path.read_bytes() != data or entry['sha256'] != digest or entry.get('clips') != changed:
            stale.append(name)
            if args.apply:
                path.write_bytes(data)
                entry.update(sha256=digest, clips=changed)
    if args.check and stale:
        raise ValueError(f'Emote flap tracks are stale: {stale}; run FillEmoteFlapTracks.py --apply')
    if args.apply and stale:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"{'Filled' if args.apply else 'Validated'} {sum(len(entry['clips']) for entry in manifest['models'].values())} "
          f"custom emote clips in {len(manifest['models'])} emote libraries.")


if __name__ == '__main__':
    main()
