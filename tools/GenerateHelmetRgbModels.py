#!/usr/bin/env python3
"""Copy tintable helmets into the head path that receives EE material overrides.

Each tintable helm_NNN is published as head 1000+NNN for both genders of every playable
body race (p<g><race>0_head1NNN). Only fixed-size model/root name fields change. Geometry,
animation, materials, controllers, vertex attributes, skin bindings, and native helmet
resources remain byte-identical. Missing phenotypes fall back to phenotype 0, but heads
never fall back across race or gender; the server keeps the native helmet for any wearer
whose head resource is missing.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from ModelLodAssets import ModelPath, model_paths
import re
import struct

import GenerateTintMapAssets as tint

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "sw_pt_head"
PATTERN = re.compile(r"helm_(\d{3})")
HEAD_BASE = 1000
# appearance.2da RACE letters of the playable parts-based species. Head models never fall
# back across race or gender, so every letter needs both genders. SWLOR.Game.Server.Tests'
# HelmetTintTests fails if a species in RacialAppearanceRegistry uses a letter missing here.
BODY_RACES = ("h", "e", "o", "d", "z")
HEAD_TARGET = re.compile(r"p[fm][a-z]0_head1\d{3}")


def head_targets(head: int) -> list[str]:
    return [f"p{gender}{race}0_head{head}" for race in BODY_RACES for gender in ("m", "f")]


def rename_model(data: bytes, source: str, target: str) -> bytes:
    if len(target) > 16:
        raise ValueError(f"Model exceeds the resource name limit: {target}")
    # Validate all mesh pointers and the exact model/raw section lengths first.
    tint.read_binary_model_material_fields(Path(source + ".mdl"), data)
    result = bytearray(data)
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]

    def rename(offset: int, size: int):
        if data[offset:offset + size].split(b"\0", 1)[0].decode("ascii").lower() == source:
            result[offset:offset + size] = target.encode("ascii").ljust(size, b"\0")

    def nodes(pointer: int):
        node = 12 + pointer
        rename(node + 32, 32)
        for index in range(uint(node + 76)):
            nodes(uint(12 + uint(node + 72) + index * 4))

    rename(20, 64)
    nodes(uint(84))
    for index in range(uint(136)):
        animation = 12 + uint(12 + uint(132) + index * 4)
        rename(animation + 84, 64)  # animation root, not the animation clip name
        nodes(uint(animation + 72))
    return bytes(result)


def plan() -> dict[Path, bytes]:
    active = tint.find_active_models()
    registered = {line.split()[1].lower() for line in tint.OUTPUT_2DA.read_text().splitlines()[3:]
                  if len(line.split()) == 4 and PATTERN.fullmatch(line.split()[1].lower())}
    if not registered:
        raise ValueError("No tintable helmets are registered")
    output = {}
    for name in sorted(registered):
        head = HEAD_BASE + int(PATTERN.fullmatch(name)[1])
        targets = head_targets(head)
        # Head IDs are reserved across every gender/race/phenotype, not just
        # the two fallback resources we generate.
        for model, path in active.items():
            if re.fullmatch(rf"p[fm][a-z]\d+_head{head}", model) and model not in targets:
                raise ValueError(f"Helmet head ID collides with an authored model: {path}")
        source = active[name].read_bytes()
        for target in targets:
            path = DIRECTORY / (target + ".mdl")
            if target in active and active[target].resolve() != path.resolve():
                raise ValueError(f"Helmet head is overridden by another HAK: {active[target]}")
            output[path] = rename_model(source, name, target)
    return output


def check() -> list[str]:
    outputs = plan()
    errors = []
    for path, expected in outputs.items():
        path = ModelPath(path)
        if not path.is_file() or path.read_bytes() != expected:
            errors.append(f"Helmet RGB model is missing or differs from its source geometry: {path.name}")
    # A reserved head without a registered tintable helmet would never be selected.
    for path in model_paths(DIRECTORY):
        if HEAD_TARGET.fullmatch(path.stem.lower()) and path not in outputs:
            errors.append(f"Orphaned helmet RGB head: {path.name}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        errors = check()
        if errors:
            raise ValueError("\n".join(errors[:20]))
        print("Helmet RGB heads match their byte-identical source geometry.")
        return
    outputs = plan()
    for path, data in outputs.items():
        path.write_bytes(data)
    print(f"Generated {len(outputs)} compiled head models for {len(outputs) // (2 * len(BODY_RACES))} tintable helmets.")


if __name__ == "__main__":
    main()
