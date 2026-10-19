#!/usr/bin/env python3
"""Let the engine animate cloth helpers beneath static garment joints.

Generated robe roots bind each garment copy of a wearer bone beneath that bone
with no tracks. The compiler gives those joints part -1, and the engine animates
nothing beneath a -1 node, so coat tails and skirt bones keep their bind shape.
This repairs the installed catalog in place: each verified bridge receives part
IDs for those joints (geometry and every clip, no controllers), is split into
its existing bank layout, and every wearer root inheriting from it gets the same
IDs by path. All other bytes are unchanged. GenerateRobeRgbModels applies the
same numbering to newly compiled bridges.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct

import CompileModels as mdl
import GenerateRobeRgbModels as generator
import RobeAnimationBanks as banks
import RobeAnimations as anim

ROOT = generator.ROOT
TOOLS = ("NumberRobeGarmentParts.py", "GenerateRobeRgbModels.py", "RobeAnimations.py", "RobeSkeleton.py")


def number_root(data, bridge):
    """Copy the bridge's part IDs onto the same garment paths in a wearer root."""
    expected = anim.binary_parts(bridge)
    targets = {path: part for path, part in expected.items()
               if path and path[-1].startswith("rg_") and part >= 0}
    result = bytearray(data)
    for path, offset in anim._node_offsets(data, struct.unpack_from("<I", data, 84)[0]):
        if path in targets and struct.unpack_from("<i", data, offset + 28)[0] < 0:
            struct.pack_into("<i", result, offset + 28, targets[path])
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest_path = ROOT / "tools" / "RobeRgbModels.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    active = generator.fresh_active_models()
    outputs, bridges, records = {}, {}, {}
    for head, record in sorted(manifest["animation_bank_sets"].items()):
        original = generator.owned_animation_bytes(head, manifest, active)
        patched = anim.number_static_garment_ancestors(original)
        anim.validate_static_garment_parts(patched)
        bridges[head] = patched
        if patched == original:
            continue
        parts = banks.split(patched)
        if list(parts) != record["parts"]:
            raise ValueError(f"{head}: part numbering changed the bank layout")
        banks.validate_split(patched, list(parts.values()))
        for name, data in parts.items():
            path = ROOT / generator.model_directory(name) / f"{name}.mdl"
            outputs[path] = (path.read_bytes(), data)
        records[head] = {"parts": record["parts"], "source_sha256": hashlib.sha256(patched).hexdigest()}
        print(f"{head}: numbered {len(anim.static_garment_ancestors(original))} static garment joints", flush=True)

    roots = 0
    for relative in sorted(manifest["files"]):
        path = ROOT / relative
        if not relative.startswith("sw_pt_root/") or path.suffix != ".mdl" or not path.is_file():
            continue
        before = path.read_bytes()
        parent = mdl.supermodel(before) if mdl.binary(before) else None
        if parent not in bridges:
            continue
        after = number_root(before, bridges[parent])
        changed = {key for key, value in anim.binary_parts(after).items()
                   if anim.binary_parts(before)[key] != value}
        expected = anim.binary_parts(bridges[parent])
        if any(not key[-1].startswith("rg_") or anim.binary_parts(after)[key] != expected[key] for key in changed):
            raise ValueError(f"{path.stem}: part numbering changed an unexpected node")
        anim.validate_animation_parts(after)
        if after != before:
            outputs[path] = (before, after)
            roots += 1
    if args.check and outputs:
        raise ValueError(f"{len(outputs)} robe models still have animated garment joints beneath unnumbered joints")
    if args.apply:
        if manifest_path.read_bytes() != manifest_bytes or any(
                path.read_bytes() != before for path, (before, _) in outputs.items()):
            raise ValueError("Robe assets changed during validation")
        for path, (_, after) in outputs.items():
            path.write_bytes(after)
            manifest["files"][path.relative_to(ROOT).as_posix()] = hashlib.sha256(after).hexdigest()
            manifest["model_builds"].pop(path.stem, None)
        manifest["animation_bank_sets"].update(records)
        for name in TOOLS:
            tool = ROOT / "tools" / name
            relative = tool.relative_to(ROOT).as_posix()
            manifest["files"][relative] = generator.file_digest(tool)
            # Audited tool fingerprints take precedence over the build-time record.
            if relative in manifest.get("validation_tool_files", {}):
                manifest["validation_tool_files"][relative] = manifest["files"][relative]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{'Applied' if args.apply else 'Validated'} {len(records)} bridges and {roots} wearer roots")


if __name__ == "__main__":
    main()
