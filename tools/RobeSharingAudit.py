"""Compare compiled garment animation behavior across a shared-rig conversion."""
from collections import defaultdict
import gc
import json
import math
import struct

import CompileModels as mdl
import RobePoseAudit as poses


def controllers_equal(before, after):
    if before.keys() != after.keys():
        return False
    for kind, (times, values) in before.items():
        actual_times, actual_values = after[kind]
        if (len(times) != len(actual_times) or len(values) != len(actual_values) or
                any(not math.isclose(a, b, rel_tol=0, abs_tol=2e-6)
                    for a, b in zip(times, actual_times))):
            return False
        for expected, actual in zip(values, actual_values):
            if len(expected) != len(actual):
                return False
            if kind == 20:
                if len(expected) != 4 or not mdl.equivalent_quaternion(expected, actual):
                    return False
            elif any(
                    not math.isclose(a, b, rel_tol=0, abs_tol=2e-6) for a, b in zip(expected, actual)):
                return False
    return True


def clip_headers(data, names=None):
    """Compare meaningful header fields, excluding compiler string padding."""
    if not mdl.binary(data):
        raise ValueError("Shared animation header audit requires compiled models")
    model_name = data[20:84].split(b"\0", 1)[0].decode("ascii").lower()
    names = names or {}
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    result = {}
    for index in range(uint(136)):
        start = 12 + uint(12 + uint(132) + index * 4)
        name = data[start+8:start+72].split(b"\0", 1)[0].decode("ascii").lower()
        duration, transition = struct.unpack_from("<2f", data, start + 112)
        root = data[start+120:start+184].split(b"\0", 1)[0].decode("ascii").lower()
        root = "$model" if root == model_name else names.get(root, root)
        events = []
        for event in range(uint(start + 188)):
            offset = 12 + uint(start + 184) + event * 36
            events.append((struct.unpack_from("<f", data, offset)[0],
                           data[offset+4:offset+36].split(b"\0", 1)[0].decode("ascii")))
        if name in result:
            raise ValueError(f"{name}: duplicate native animation name")
        result[name] = (duration, transition, root, events)
    return result


def mapped_nodes(model, names):
    """Index only audited garment nodes; stock bodies can repeat helper names."""
    result = {}
    for node in model.nodes:
        if node[0] not in names:
            continue
        if node[0] in result:
            raise ValueError(f"{node[0]}: ambiguous garment node name")
        result[node[0]] = node
    return result


def parent_name(model, node):
    parent = node[1]
    if parent is None or parent < 0:
        return None
    return "$model" if parent == 0 else model.nodes[parent][0]


def validate_native_channels(data, node):
    """The pose reader skips unknown controllers; the equivalence gate must not."""
    offset = node[4]
    keys, count = struct.unpack_from("<II", data, offset + 84)
    kinds = set()
    for index in range(count):
        kind, rows, _, _, columns = struct.unpack_from("<IHHHB", data, 12 + keys + index * 12)
        if kind not in (8, 20, 36) or not rows or columns != {8: 3, 20: 4, 36: 1}[kind] or kind in kinds:
            raise ValueError(f"{node[0]}: unsupported or duplicate native garment controller")
        kinds.add(kind)


def validate_wearer(before, after, names):
    """Check actual garment hierarchy/binds; generator audits body and skin data separately."""
    original, target = poses.Model(before, False), poses.Model(after, False)
    complete_names = {node[0]: names.get(node[0], node[0]) for node in original.nodes
                      if node[0] in names or node[0].startswith(("rg_", "rm_"))}
    if len(set(complete_names.values())) != len(complete_names):
        raise ValueError("Shared rig collapsed separate wearer joints")
    originals = mapped_nodes(original, complete_names)
    targets = mapped_nodes(target, set(complete_names.values()))
    for node, source in originals.items():
        renamed = complete_names[node]
        if renamed not in targets:
            raise ValueError(f"{node}: shared wearer lost a node")
        actual = targets[renamed]
        expected_parent = parent_name(original, source)
        expected_parent = complete_names.get(expected_parent, expected_parent)
        if expected_parent != parent_name(target, actual) or not controllers_equal(source[3], actual[3]):
            raise ValueError(f"{node}: shared wearer hierarchy or local bind changed")


def validate_joints(before, after, names):
    """Exhaustively compare controller channels/keys; newly added garment rigs are ignored."""
    if before.clips.keys() != after.clips.keys() or clip_headers(before.data, names) != clip_headers(after.data):
        raise ValueError("Shared rig changed animation names, timing, roots, or events")
    original_names = {node[0] for node in before.nodes}
    mappings = {name: target for name, target in names.items() if name in original_names}
    original_geometry = mapped_nodes(before, mappings)
    target_geometry = mapped_nodes(after, set(mappings.values()))
    if any(target not in target_geometry for target in mappings.values()):
        raise ValueError("Shared animation rig is missing a mapped garment joint")
    for name, target in mappings.items():
        expected_parent = parent_name(before, original_geometry[name])
        if mappings.get(expected_parent, expected_parent) != parent_name(after, target_geometry[target]):
            raise ValueError(f"{name}: shared animation garment hierarchy changed")
    for clip, (_, nodes) in before.clips.items():
        old, new = {}, {}
        for data, clip_nodes, wanted, result in (
                (before.data, nodes, mappings, old),
                (after.data, after.clips[clip][1], set(mappings.values()), new)):
            for node in clip_nodes:
                if node[0] not in wanted:
                    continue
                if node[0] in result:
                    raise ValueError(f"{clip}/{node[0]}: ambiguous garment animation node")
                validate_native_channels(data, node)
                result[node[0]] = node[3]
        for name, target in mappings.items():
            if not controllers_equal(old.get(name, {}), new.get(target, {})):
                raise ValueError(f"{clip}/{name}: shared garment animation controllers changed")
    return len(before.clips)


def validate_records(records, load_before, load_after):
    """Read one new rig and one previous family at a time, regardless of catalog size."""
    grouped = defaultdict(lambda: defaultdict(dict))
    for record in records:
        names = record["names"]
        grouped[record["after"]][record["before"]][json.dumps(names, sort_keys=True)] = names
    count = 0
    for new_name, originals in sorted(grouped.items()):
        after = poses.Model(load_after(new_name))
        for old_name, variants in sorted(originals.items()):
            before = poses.Model(load_before(old_name))
            for names in variants.values():
                count += validate_joints(before, after, names)
            del before
            # Model.read_nodes uses a recursive closure that retains its model
            # until cyclic collection; release it before loading another family.
            gc.collect()
        print(f"Verified shared garment controllers: {new_name}", flush=True)
        del after
        gc.collect()
    return count
