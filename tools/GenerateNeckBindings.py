#!/usr/bin/env python3
"""Preserve neck skins when a robe phenotype inserts nodes in the wearer rig.

Dynamic neck parts store traversal indices into the wearer's skeleton. Add
phenotype-specific resources, retaining the original neck IDs and source models.
Only binding tables and the generated resource's name change; raw vertex data,
mesh properties, faces, controllers and original inverse binds remain intact.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import struct

import CompileModels as mdl
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
BANKS = ("sw_neck_f0", "sw_neck_f1")
MANIFEST = "tools/NeckBindings.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def skin_nodes(data):
    """Return skin headers and validated external wearer binding tables."""
    model = Model(data, False)
    model_size, raw_size = struct.unpack_from("<II", data, 4)
    if 12 + model_size + raw_size != len(data):
        raise ValueError("Invalid binary model section sizes")
    result = []
    for name, _, _, _, offset in model.nodes:
        flags = model.uint(offset + 108)
        if not flags & 0x40:
            continue
        mesh = offset + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
        extra = mesh + 512
        pointer, count = struct.unpack_from("<II", data, extra + 20)
        if not count or pointer + count * 2 > model_size:
            raise ValueError(f"{name}: invalid node-to-bone table")
        node_map = struct.unpack_from(f"<{count}h", data, 12 + pointer)
        bones = struct.unpack_from("<17h", data, extra + 64)
        slots = {slot: index for index, slot in enumerate(node_map) if slot >= 0}
        if len(slots) != sum(slot >= 0 for slot in node_map):
            raise ValueError(f"{name}: duplicate bone slots")
        if any(slot >= 17 or bones[slot] != index for slot, index in slots.items()):
            raise ValueError(f"{name}: inconsistent bone tables")
        arrays = []
        for field, width in ((28, 16), (40, 12), (52, 4)):
            start, length, capacity = struct.unpack_from("<III", data, extra + field)
            if length != count or capacity < length or start + length * width > model_size:
                raise ValueError(f"{name}: invalid inverse-bind/constant array")
            arrays.append((field, width, start))
        vertices = struct.unpack_from("<H", data, mesh + 448)[0]
        stride, raw = model.uint(mesh + 440), 12 + model_size
        for vertex in range(vertices):
            weights = struct.unpack_from("<4f", data, raw + model.uint(extra + 12) + vertex * (stride or 16))
            indices = struct.unpack_from("<4h", data, raw + model.uint(extra + 16) + vertex * (stride or 8))
            if any(weight != 0 and slot not in slots for weight, slot in zip(weights, indices)):
                raise ValueError(f"{name}: vertex references an absent bone slot")
        result.append((extra, node_map, bones, slots, arrays))
    return result


def wearer_mapping(original, wearer):
    """Match repeated helper names by occurrence, including the renamed root."""
    targets = defaultdict(list)
    for index, node in enumerate(wearer.nodes):
        targets[node[0]].append(index)
    seen, mapping = defaultdict(int), {0: 0}
    for index, (name, *_) in enumerate(original.nodes[1:], 1):
        occurrence = seen[name]
        seen[name] += 1
        if occurrence >= len(targets[name]):
            raise ValueError(f"Wearer is missing native bone {name}/{occurrence}")
        mapping[index] = targets[name][occurrence]
    return mapping


def remap_neck(source, original, wearer, resource_name):
    """Return a generated binary, or None when all bound indices already match."""
    skins = skin_nodes(source)
    mapping = wearer_mapping(original, wearer)
    used = {index for _, _, _, slots, _ in skins for index in slots.values()}
    if any(index not in mapping for index in used):
        raise ValueError("Neck binds to a bone outside the native wearer skeleton")
    if not any(mapping[index] != index for index in used):
        return None
    if not re.fullmatch(r"pfh\d+_neck\d{3}", resource_name) or len(resource_name) > 16:
        raise ValueError("Invalid generated neck resource name")
    # The fix changes lookup indices only. Refuse a root that also changed the
    # referenced native joints' world bind transforms.
    before, after = original.pose(), wearer.pose()
    for index in used:
        name = original.nodes[index][0]
        p, q, s = before[name]
        P, Q, S = after[name]
        if math.dist(p, P) > 2e-5 or not mdl.equivalent_quaternion(q, Q) or abs(s - S) > 2e-6:
            raise ValueError(f"{resource_name}: wearer changed native bind {name}")
    model_size = struct.unpack_from("<I", source, 4)[0]
    result = bytearray(source[:12 + model_size])
    result[20:84] = resource_name.encode("ascii").ljust(64, b"\0")
    root_offset = Model(source, False).nodes[0][-1]
    result[root_offset + 32:root_offset + 64] = resource_name.encode("ascii").ljust(32, b"\0")
    count = len(wearer.nodes)
    if count > 32767:
        raise ValueError("Wearer exceeds signed skin node-index capacity")
    for extra, node_map, bones, slots, arrays in skins:
        if len(node_map) != len(original.nodes):
            raise ValueError("Neck binding arrays do not match native wearer node count")
        new_map = [-1] * count
        new_bones = list(bones)
        for slot, index in slots.items():
            new_map[mapping[index]] = slot
            new_bones[slot] = mapping[index]
        struct.pack_into("<II", result, extra + 20, len(result) - 12, count)
        result.extend(struct.pack(f"<{count}h", *new_map))
        struct.pack_into("<17h", result, extra + 64, *new_bones)
        for field, width, pointer in arrays:
            default = struct.pack("<4f", 1, 0, 0, 0) if field == 28 else bytes(width)
            values = [default] * count
            for index, target in mapping.items():
                values[target] = source[12 + pointer + index * width:12 + pointer + (index + 1) * width]
            struct.pack_into("<III", result, extra + field, len(result) - 12, count, count)
            result.extend(b"".join(values))
    struct.pack_into("<I", result, 4, len(result) - 12)
    result.extend(source[12 + model_size:])
    return bytes(result)


def validate_remap(source, output, original, wearer, resource_name):
    """Audit external skin semantics and every byte outside permitted headers."""
    source_model_size = struct.unpack_from("<I", source, 4)[0]
    output_model_size = struct.unpack_from("<I", output, 4)[0]
    if source[12 + source_model_size:] != output[12 + output_model_size:]:
        raise ValueError("Raw vertex, normal, UV, weight or index data changed")
    old_skins, new_skins = skin_nodes(source), skin_nodes(output)
    if len(old_skins) != len(new_skins):
        raise ValueError("Skin mesh inventory changed")
    mapping = wearer_mapping(original, wearer)
    expected = bytearray(source[:12 + source_model_size])
    struct.pack_into("<I", expected, 4, output_model_size)
    expected[20:84] = resource_name.encode("ascii").ljust(64, b"\0")
    root = Model(source, False).nodes[0][-1]
    expected[root + 32:root + 64] = resource_name.encode("ascii").ljust(32, b"\0")
    for old, new in zip(old_skins, new_skins):
        extra, _, _, slots, arrays = old
        target_extra, target_map, target_bones, target_slots, target_arrays = new
        if extra != target_extra or len(target_map) != len(wearer.nodes):
            raise ValueError("Skin header or wearer node inventory changed")
        if target_slots != {slot: mapping[index] for slot, index in slots.items()}:
            raise ValueError("A skin slot no longer follows its original named bone")
        for (field, width, pointer), (_, _, target_pointer) in zip(arrays, target_arrays):
            for index, target in mapping.items():
                before = source[12 + pointer + index * width:12 + pointer + (index + 1) * width]
                after = output[12 + target_pointer + target * width:12 + target_pointer + (target + 1) * width]
                if before != after:
                    raise ValueError("Native inverse bind or bone constant changed")
        # These are the only existing skin bytes a remap may alter: four
        # array headers and the 17-entry bone slot lookup. All old array data,
        # topology, mesh appearance, animation and controller bytes must match.
        expected[extra + 20:extra + 98] = output[extra + 20:extra + 98]
    if expected != output[:12 + source_model_size]:
        raise ValueError("Geometry, material, controller or unrelated model bytes changed")


def inventory(source_root, neck_ids=None, phenotypes=None):
    sources = {}
    for path in sorted((source_root / "sw_pt_neck").glob("pfh0_neck*.mdl")):
        neck_id = int(path.stem[-3:])
        if neck_ids is not None and neck_id not in neck_ids:
            continue
        data = path.read_bytes()
        if skin_nodes(data):
            sources[neck_id] = (path, data)
    roots = []
    for path in sorted((source_root / "sw_pt_root").glob("pfh*.mdl")):
        match = re.fullmatch(r"pfh(\d+)", path.stem)
        if match and int(match[1]) != 0 and (phenotypes is None or int(match[1]) in phenotypes):
            roots.append((int(match[1]), path))
    return sources, sorted(roots)


def generate(source_root, output_root, neck_ids=None, phenotypes=None):
    original_data = (source_root / "sw_pt_root/pfh0.mdl").read_bytes()
    original = Model(original_data, False)
    sources, roots = inventory(source_root, neck_ids, phenotypes)
    manifest = {"version": 1, "native_root_sha256": digest(original_data), "sources": {},
                "selection": {"necks": sorted(neck_ids) if neck_ids is not None else None,
                              "phenotypes": sorted(phenotypes) if phenotypes is not None else None},
                "generator_sha256": digest(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
                "wearers": {}, "files": {}, "archive_bytes": {bank: 160 for bank in BANKS}}
    for neck_id, (path, data) in sources.items():
        manifest["sources"][path.name] = {"neck_id": neck_id, "sha256": digest(data)}
    for phenotype, path in sorted(roots):
        wearer_data = path.read_bytes()
        wearer = Model(wearer_data, False)
        manifest["wearers"][path.stem] = digest(wearer_data)
        for neck_id, (source_path, source) in sorted(sources.items()):
            name = f"pfh{phenotype}_neck{neck_id:03}"
            output = remap_neck(source, original, wearer, name)
            if output is None:
                continue
            validate_remap(source, output, original, wearer, name)
            bank = BANKS[phenotype % 2]
            manifest["archive_bytes"][bank] += len(output) + 32
            if manifest["archive_bytes"][bank] >= 2**31:
                raise ValueError(f"{bank}: archive reaches the 2 GiB compatibility limit")
            relative = f"{bank}/{name}.mdl"
            target = output_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != output:
                raise ValueError(f"Refusing to replace different existing output: {target}")
            target.write_bytes(output)
            manifest["files"][relative] = {"source": source_path.name, "wearer": path.stem,
                                            "sha256": digest(output), "bytes": len(output)}
    (output_root / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (output_root / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def check(source_root, output_root):
    """Verify inputs, complete affected-pair coverage, files and archive budgets.

    Hash shipped outputs once; do not regenerate gigabytes merely to check a
    manifest. Generation already audits every binding and immutable byte.
    """
    report = json.loads((output_root / MANIFEST).read_text(encoding="utf-8"))
    if report["version"] != 1 or report["generator_sha256"] != digest(Path(__file__).read_bytes().replace(b"\r\n", b"\n")):
        raise ValueError("Neck generator changed; regenerate its outputs")
    selection = report["selection"]
    sources, roots = inventory(source_root, selection["necks"], selection["phenotypes"])
    current_sources = {path.name: {"neck_id": number, "sha256": digest(data)}
                       for number, (path, data) in sources.items()}
    if current_sources != report["sources"]:
        raise ValueError("Native neck inventory or source contents changed")
    original_data = (source_root / "sw_pt_root/pfh0.mdl").read_bytes()
    if digest(original_data) != report["native_root_sha256"]:
        raise ValueError("Native wearer skeleton changed")
    original = Model(original_data, False)
    used = {number: {index for _, _, _, slots, _ in skin_nodes(data) for index in slots.values()}
            for number, (_, data) in sources.items()}
    expected, current_roots = {}, {}
    for phenotype, path in roots:
        data = path.read_bytes()
        current_roots[path.stem] = digest(data)
        mapping = wearer_mapping(original, Model(data, False))
        for number, (source_path, _) in sources.items():
            if any(mapping[index] != index for index in used[number]):
                name = f"{BANKS[phenotype % 2]}/pfh{phenotype}_neck{number:03}.mdl"
                expected[name] = (source_path.name, path.stem)
    if current_roots != report["wearers"]:
        raise ValueError("Robe wearer inventory or skeleton contents changed")
    if set(expected) != report["files"].keys():
        raise ValueError("Generated manifest does not cover every affected neck/wearer pair")
    actual = {path.relative_to(output_root).as_posix() for bank in BANKS
              for path in (output_root / bank).glob("*.mdl")}
    if actual != set(expected):
        raise ValueError("Generated neck resources are missing or unrecorded")
    sizes = {bank: 160 for bank in BANKS}
    for relative, (source, wearer) in expected.items():
        row = report["files"][relative]
        data = (output_root / relative).read_bytes()
        if (row["source"], row["wearer"]) != (source, wearer) or len(data) != row["bytes"] or digest(data) != row["sha256"]:
            raise ValueError(f"Generated neck resource changed: {relative}")
        sizes[relative.split("/")[0]] += len(data) + 32
    if sizes != report["archive_bytes"] or any(size >= 2**31 for size in sizes.values()):
        raise ValueError("Generated neck archive size or 2 GiB budget changed")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--necks", type=int, nargs="+")
    parser.add_argument("--phenotypes", type=int, nargs="+")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check and (args.necks is not None or args.phenotypes is not None):
        parser.error("--check uses the selection recorded in the manifest")
    report = (check(args.source_root, args.output_root) if args.check else
              generate(args.source_root, args.output_root, args.necks, args.phenotypes))
    print(json.dumps({"models": len(report["files"]), "neck_models": len(report["sources"]),
                      "archive_bytes": report["archive_bytes"]}))


if __name__ == "__main__":
    main()
