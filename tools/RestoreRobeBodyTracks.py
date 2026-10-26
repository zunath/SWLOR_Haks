#!/usr/bin/env python3
"""Restore the wearer's attachment-joint tracks that robe bridges dropped.

Stock body clips also animate a few attachment joints that have no native part
ID (part -1): `belt_g1` carries the hip sway for belts and the female clips turn
`lshoulder_g`/`rshoulder_g` under the shoulder pads. The engine applies those
tracks by joint name. Bridge generation maps body tracks by part ID only, so
every robe phenotype lost them: rigid belts stayed square to the root while the
hips turned beneath them, and female shoulder pads stopped turning.

This copies each such track, byte for byte and with its -1 part ID, from the
wearer's own body clip into the same bridge clip, exactly as the stock clip
carries it. Joints whose body tracks never leave their bind pose need nothing:
the bridge never moves them either. Garment tracks, every other body track,
other clips and the skeleton are unchanged.

`--check/--apply --game-data "<NWN>/data"` repairs the installed banks in place,
like FillRobeEmoteMotion.py; GenerateRobeRgbModels applies the same tracks to
newly compiled bridges.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path

import CompileModels as mdl
import FillRobeEmoteMotion as emote
import GenerateTintMapAssets as tint
import RobeAnimationBanks as banks
import RobePoseAudit as poses

PLAYABLE = emote.PLAYABLE
ORIENTATION = 20
# Controller types the engine evaluates on dummy joints, with their columns.
COLUMNS = {8: 3, 20: 4, 36: 1}


def _uint(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def raw_controllers(model, offset):
    """Return a compiled node's controller key and value arrays verbatim."""
    data = model.data
    keys, key_count = _uint(data, offset + 84), _uint(data, offset + 88)
    values, value_count = _uint(data, offset + 96), _uint(data, offset + 100)
    raw_keys = bytes(data[12 + keys:12 + keys + key_count * 12]) if key_count else b""
    raw_values = bytes(data[12 + values:12 + values + value_count * 4]) if value_count else b""
    for index in range(key_count):
        kind, rows, times, start, columns = struct.unpack_from("<IHHHB", raw_keys, index * 12)
        if kind not in COLUMNS:
            raise ValueError(f"Unsupported attachment-joint controller {kind}")
        if (columns != COLUMNS[kind] or times + rows > value_count or
                start + rows * columns > value_count):
            raise ValueError(f"Malformed attachment-joint controller {kind}")
    return raw_keys, raw_values


def _moves(controllers, bind):
    for kind, (_, values) in controllers.items():
        rest = bind.get(kind, (None, [None]))[1][0]
        for value in values:
            if kind == ORIENTATION:
                if not mdl.equivalent_quaternion(value, rest or (0, 0, 0, 1)):
                    return True
            elif kind == 8:
                if math.dist(value, rest or (0, 0, 0)) > 1e-6:
                    return True
            elif abs(value[0] - (rest[0] if rest else 1)) > 1e-6:
                return True
    return False


def moving_joints(library, base):
    """Unnumbered body joints that some body clip turns away from their bind pose."""
    model = library.model(base)
    bind = {}
    for name, _, part, controllers, _ in model.nodes:
        bind.setdefault(name, controllers)
    result = set()
    for _, nodes in library.clips(base).values():
        for name, _, part, controllers, _ in nodes:
            if part < 0 and name in bind and controllers and _moves(controllers, bind[name]):
                result.add(name)
    return result


def missing_tracks(data, library):
    """Return {clip: {joint: (keys, values)}} for the body tracks a bridge lacks."""
    bridge = banks.CompiledBridge(data)
    if not PLAYABLE.fullmatch(bridge.name):
        return {}
    model = poses.Model(data)
    base = bridge.parent
    joints = moving_joints(library, base)
    body = {name for name, _, _, _, _ in library.model(base).nodes}
    bank = {}
    for name, _, part, _, _ in model.nodes:
        if name in body:
            bank.setdefault(name, part)
    if any(bank.get(joint, -1) >= 0 for joint in joints):
        raise ValueError(f"{bridge.name}: an unnumbered body joint is numbered in the bridge")
    clips, owners = library.clips(base), library.owners(base)
    result = {}
    for clip in bridge.clip_names:
        if clip not in clips:
            continue
        length, nodes = clips[clip]
        source = library.model(owners[clip])
        present = {name: offset for name, _, _, _, offset in model.clips[clip][1]}
        found = {}
        for name, _, part, controllers, offset in nodes:
            if part >= 0 or name not in joints or name not in bank or name in found:
                continue
            if name in present and _uint(model.data, present[name] + 88):
                continue
            tracks = raw_controllers(source, offset)
            if not tracks[0]:
                continue
            # Bridges have unit animation scale; only rotations copy across unchanged.
            if any(struct.unpack_from("<I", tracks[0], index)[0] != ORIENTATION
                   for index in range(0, len(tracks[0]), 12)):
                raise ValueError(f"{bridge.name}/{clip}/{name}: body joint translation or scale "
                                 "tracks need animation-scale compensation")
            found[name] = tracks
        if found:
            if not math.isclose(length, model.clips[clip][0], abs_tol=2e-6):
                raise ValueError(f"{bridge.name}/{clip}: bridge and body clip durations differ")
            result[clip] = found
    return result


def add_tracks(model, component, model_name, tracks):
    header, events, root = emote.read_clip(component)
    nodes = model.nodes
    first = {}
    children = {index: [] for index in range(len(nodes))}
    for index, (name, parent, _, _, _) in enumerate(nodes):
        first.setdefault(name, index)
        if parent is not None:
            children[parent].append(index)
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
        child = emote.Node(emote._header(name, nodes[index][2]))
        siblings = children[nodes[index][1]]
        rank = {nodes[sibling][0]: siblings.index(sibling) for sibling in siblings}
        position = sum(1 for other in parent_node.children if rank.get(other.name, -1) < siblings.index(index))
        parent_node.children.insert(position, child)
        return child
    for name in sorted(tracks, key=first.get):
        current = root
        for step in path(first[name]):
            current = ensure(current, step)
        if current.keys:
            raise ValueError(f"{name}: clip already animates this joint")
        current.keys, current.values = tracks[name]
    return emote.write_clip(header, events, root, model_name)


def restore(data, library):
    """Return the bridge with every dropped body attachment track restored, and those clips."""
    missing = missing_tracks(data, library)
    if not missing:
        return data, []
    bridge = banks.CompiledBridge(data)
    model = poses.Model(data)
    clips = [add_tracks(model, component, bridge.name, missing[name]) if name in missing else component
             for name, component in zip(bridge.clip_names, bridge.clips)]
    result = bridge.pack(bridge.name, bridge.parent, clips)
    changed = sorted(missing)
    banks.validate_motion_revision(data, result, changed)
    return result, changed


def library_for(active, game_data=None):
    """Body animation chains from the HAKs, then the installed game's stock models."""
    stock = tint.read_stock_key_models(game_data) if game_data is not None else {}
    def load(name):
        if name in active:
            return active[name].read_bytes()
        return tint.extract_stock_bif_resource(*stock[name]) if name in stock else None
    return poses.Library(load)


def main():
    import GenerateRobeRgbModels as generator
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--game-data", type=Path, required=True,
                        help="the installed game's data directory (stock body animations)")
    args = parser.parse_args()
    root = generator.ROOT
    manifest_path = root / "tools" / "RobeRgbModels.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    active = generator.fresh_active_models()
    library = library_for(active, args.game_data)
    outputs, records, pending = {}, {}, 0
    for head, record in sorted(manifest["animation_bank_sets"].items()):
        if not PLAYABLE.fullmatch(head):
            continue
        original = generator.owned_animation_bytes(head, manifest, active)
        patched, changed = restore(original, library)
        if not changed:
            continue
        pending += len(changed)
        parts = banks.split(patched)
        if list(parts) != record["parts"]:
            raise ValueError(f"{head}: restored body tracks changed the bank layout")
        for name, part in parts.items():
            path = root / generator.model_directory(name) / f"{name}.mdl"
            if path.read_bytes() != part:
                outputs[path] = (path.read_bytes(), part)
        records[head] = {**record, "source_sha256": hashlib.sha256(patched).hexdigest()}
        print(f"{head}: restored body attachment tracks in {len(changed)} clips", flush=True)
    if args.check and pending:
        raise ValueError(f"{pending} robe bridge clips drop body attachment tracks; "
                         "run RestoreRobeBodyTracks.py --apply")
    if args.apply:
        if manifest_path.read_bytes() != manifest_bytes or any(
                path.read_bytes() != before for path, (before, _) in outputs.items()):
            raise ValueError("Robe assets changed during validation")
        for path, (_, after) in outputs.items():
            path.write_bytes(after)
            manifest["files"][path.relative_to(root).as_posix()] = hashlib.sha256(after).hexdigest()
        manifest["animation_bank_sets"].update(records)
        for head, record in records.items():
            # Keep the generator's build cache valid for the restored bridge.
            if head in manifest.get("model_builds", {}):
                manifest["model_builds"][head]["binarySha256"] = record["source_sha256"]
        for name in ("RestoreRobeBodyTracks.py", "GenerateRobeRgbModels.py"):
            relative = f"tools/{name}"
            manifest["files"][relative] = generator.file_digest(root / relative)
            if relative in manifest.get("validation_tool_files", {}):
                manifest["validation_tool_files"][relative] = manifest["files"][relative]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{'Applied' if args.apply else 'Validated'} {pending} clips in {len(records)} bridges")


if __name__ == "__main__":
    main()
