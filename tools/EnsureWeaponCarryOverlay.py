#!/usr/bin/env python3
"""Add the controller-free weapon carry overlay to the common compiled tail.

Compile only a tiny donor, then append its animation to a_ba_casts. Existing
model-data offsets and every geometry/controller byte remain unchanged; the
raw-data section moves as a whole because its offsets are section-relative.
Run again after replacing the common tail. The operation is idempotent.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import time

import CompileModels as mdl
from RobePoseAudit import Model

NAME = "sw_nohold"
OWNER = "a_ba_casts"
SOURCE = f"""newmodel {OWNER}
setsupermodel {OWNER} NULL
classification CHARACTER
setanimationscale 1
beginmodelgeom {OWNER}
node dummy {OWNER}
parent NULL
endnode
endmodelgeom {OWNER}
newanim {NAME} {OWNER}
length 1
transtime 0
animroot {OWNER}
node dummy {OWNER}
parent NULL
endnode
doneanim {NAME} {OWNER}
donemodel {OWNER}
""".encode("ascii")


def animation_offset(model, name):
    for index in range(model.uint(136)):
        offset = 12 + model.uint(12 + model.uint(132) + index * 4)
        if model.data[offset+8:offset+72].split(b"\0", 1)[0].decode().lower() == name:
            return offset
    raise ValueError(f"Missing {name}")


def validate_overlay(data):
    model = Model(data)
    length, nodes = model.clips[NAME]
    header = animation_offset(model, NAME)
    if length != 1 or len(nodes) != 1 or nodes[0][0] != OWNER:
        raise ValueError("Carry overlay must be a one-second animation with one empty root")
    node = nodes[0][4]
    if model.uint(node+108) != 1 or nodes[0][2] != 0:
        raise ValueError("Carry overlay root must be a dummy with part ID zero")
    if any(model.uint(node+offset) for offset in range(64, 108, 4)):
        raise ValueError("Carry overlay must have no parents, children, or controller arrays")
    if any(model.uint(header+offset) for offset in (184, 188, 192)):
        raise ValueError("Carry overlay cannot have animation events")
    if struct.unpack_from("<f", data, header+116)[0] != 0:
        raise ValueError("Carry overlay must have zero transition time")
    return model, header, node


def append_overlay(data, donor):
    original = Model(data)
    if data[20:84].split(b"\0", 1)[0].decode().lower() != OWNER or original.parent:
        raise ValueError("Expected the terminal a_ba_casts model")
    if NAME in original.clips:
        validate_overlay(data)
        return data
    compiled, header, node = validate_overlay(donor)
    if len(compiled.clips) != 1:
        raise ValueError("Donor must contain only the carry overlay")
    model_size, raw_size = struct.unpack_from("<II", data, 4)
    if len(data) != 12 + model_size + raw_size:
        raise ValueError("Invalid compiled model section sizes")
    count = original.uint(136)
    array = data[12+original.uint(132):12+original.uint(132)+count*4]
    new_header = model_size + (count+1)*4
    new_node = new_header + 196
    animation = bytearray(donor[header:header+196])
    struct.pack_into("<I", animation, 72, new_node)
    addition = array + struct.pack("<I", new_header) + animation + donor[node:node+112]
    result = bytearray(data[:12+model_size] + addition + data[12+model_size:])
    struct.pack_into("<I", result, 4, model_size+len(addition))
    struct.pack_into("<III", result, 132, model_size, count+1, count+1)
    validate_preservation(data, bytes(result))
    return bytes(result)


def validate_preservation(before, after):
    """Prove the old resource survives byte-for-byte, except array metadata."""
    old = Model(before)
    model, _, _ = validate_overlay(after)
    size = old.uint(4)
    old_bytes, new_bytes = bytearray(before[:12+size]), bytearray(after[:12+size])
    for start, length in ((4, 4), (132, 12)):
        new_bytes[start:start+length] = old_bytes[start:start+length]
    if old_bytes != new_bytes or before[12+size:] != after[12+model.uint(4):]:
        raise ValueError("Existing model or raw data changed")
    if model.nodes != old.nodes or any(model.clips.get(k) != v for k, v in old.clips.items()):
        raise ValueError("Existing skeleton or animation controllers changed")
    if set(model.clips) != set(old.clips) | {NAME}:
        raise ValueError("Unexpected animation inventory change")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = mdl.ROOT / "sw_cr_creature" / f"{OWNER}.mdl"
    before = target.read_bytes()
    if NAME in Model(before).clips:
        validate_overlay(before)
        print("Compiled weapon carry overlay is already present.")
        return
    stage = mdl.ROOT / "output" / f"weapon-carry-{time.time_ns()}"
    for directory in ("input", "binary", "decompiled"):
        (stage/directory).mkdir(parents=True, exist_ok=True)
    source = stage / "input" / f"{OWNER}.mdl"
    source.write_bytes(SOURCE)
    compiler, _ = mdl.prepare_compiler(stage)
    mdl.run_compiler(compiler, stage, ["-cne", str(source), str(stage/"binary")+"/"], "compile.log")
    result = append_overlay(before, (stage/"binary"/f"{OWNER}.mdl").read_bytes())
    (stage/f"{OWNER}.mdl").write_bytes(result)
    mdl.run_compiler(compiler, stage, ["-de", str(stage/f"{OWNER}.mdl"), str(stage/"decompiled")+"/"], "decompile.log")
    if args.apply:
        if target.read_bytes() != before:
            raise ValueError("Common tail changed during generation; retry")
        target.write_bytes(result)
    print(f"Validated compiled overlay: {len(result)-len(before)} added bytes; all old model bytes preserved. {stage}")


if __name__ == "__main__":
    main()
