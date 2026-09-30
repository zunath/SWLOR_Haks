"""Bind static robe coverings to their torso through the native skin renderer.

Rigid child meshes can remain displaced after animation changes even without
cloth simulation. A single torso weight preserves their rigid shape while using
the same bone-following renderer as the sleeves and skirt. Existing geometry,
materials, transforms, native animation IDs and other skin bindings are kept.
Only explicitly selected compiled panels are changed, including RGB roots.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import time

import CompileModels as mdl
import GenerateTintMapAssets as tint
import MakeRobePanelsRigid as rigid
import RobePoseAudit as poses
import RobeSkeleton as skeleton

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/RobeRgbModels.json"


def bind_source(text, panels):
    selected = set(panels)
    bindings = {}

    def replace(match):
        kind, name, props = mdl.parse_nodes(match[0])[0]
        authored = name.removeprefix("rm_").removeprefix("rg_")
        if authored not in selected or not props.get("verts"):
            return match[0]
        bone = props.get("parent", [""])[0]
        if not re.fullmatch(r"(?:rg_)?torso_g(?:_\d+)?", bone):
            raise ValueError(f"{name}: selected panel is not attached to a torso")
        weights = [[bone, "1"] for _ in props["verts"]]
        if kind == "skin" and mdl.equivalent(props.get("weights"), weights):
            return match[0]
        if kind != "trimesh":
            raise ValueError(f"{name}: expected a rigid panel, found {kind}")
        if any(key.endswith("key") for key in props):
            raise ValueError(f"{name}: a static panel must not have animated controllers")
        props["weights"] = weights
        bindings[name] = bone
        return "\n" + skeleton.serialize("skin", name, props)

    return rigid.NODE.sub(replace, text), bindings


def preserve_bindings(before, after, bindings):
    original = poses.Model(before, False)
    names = {node[0]: node[0] for node in original.nodes}
    after = poses.preserve_skin_bindings(before, after, names)
    target = poses.Model(after, False)
    world = original.pose()
    result = bytearray(after)
    for mesh, bone in bindings.items():
        bp, bq, bs = world[bone]
        mp, mq, ms = world[mesh]
        if abs(bs - 1) > 2e-6 or abs(ms - 1) > 2e-6:
            raise ValueError(f"{mesh}: non-unit bind scale is unsupported")
        inverse = (-bq[0], -bq[1], -bq[2], bq[3])
        translation = poses.rotate(inverse, tuple(a-b for a, b in zip(mp, bp)))
        rotation = poses.multiply(inverse, mq)
        actual = target.skin_bindings()[mesh]
        if set(actual) != {bone}:
            raise ValueError(f"{mesh}: compiler changed the selected torso binding")
        qoffset, toffset = actual[bone]
        struct.pack_into("<4f", result, qoffset, rotation[3], *rotation[:3])
        struct.pack_into("<3f", result, toffset, *translation)
    return bytes(result)


def validate_panel_poses(before, after, bindings, clips=()):
    """Every compiled vertex must follow the original rigid panel's bone pose."""
    original, target = poses.Model(before, False), poses.Model(after, False)
    geometry = {name: props for name, _, props in mdl.binary_nodes(after) if props}
    skin_bindings = target.skin_bindings()
    for clip in [None, *clips]:
        for fraction in ((0,) if clip is None else (0, .25, .5, .75, 1)):
            time = 0 if clip is None else clip[0] * fraction
            expected = original.pose(clip, time, original.scale)
            actual = target.pose(clip, time, target.scale)
            for mesh, bone in bindings.items():
                qoffset, toffset = skin_bindings[mesh][bone]
                w, x, y, z = struct.unpack_from("<4f", after, qoffset)
                translation = struct.unpack_from("<3f", after, toffset)
                mp, mq, ms = expected[mesh]
                bp, bq, bs = actual[bone]
                for vertex in geometry[mesh]["verts"]:
                    rigid_vertex = tuple(a+b for a, b in zip(mp, poses.rotate(mq, tuple(v*ms for v in vertex))))
                    bound = tuple(a+b for a, b in zip(translation, poses.rotate((x, y, z, w), vertex)))
                    skin_vertex = tuple(a+b for a, b in zip(bp, poses.rotate(bq, tuple(v*bs for v in bound))))
                    if math.dist(rigid_vertex, skin_vertex) > 2e-5:
                        raise ValueError(f"{mesh}: skin placement differs at phase {fraction}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robe", type=int, required=True)
    parser.add_argument("--panel", action="append", required=True)
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    selected = rigid.compiled_targets(rigid.targets(args.robe, manifest))
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(args.game_data)
    stage = ROOT / "output" / f"bound-robe-{args.robe}-{time.time_ns()}"
    for folder in (stage, stage / "original", stage / "input", stage / "binary", stage / "decompiled"):
        folder.mkdir(parents=True, exist_ok=True)
    compiler, _ = mdl.prepare_compiler(stage)
    dependencies, missing = {}, set()

    def load(name):
        if not name or name in dependencies or name in missing:
            return
        if name in active:
            data = active[name].read_bytes()
        elif name in stock:
            data = tint.extract_stock_bif_resource(*stock[name])
        else:
            missing.add(name)
            return
        dependencies[name] = data
        load(mdl.supermodel(data))
        (stage / f"{name}.mdl").write_bytes(data)

    changed = {}
    for path, data in selected.items():
        load(path.stem)
        mdl.run_compiler(compiler, stage, ["-de", str(path), str(stage / "original") + "/"], "original.log")
        source = poses.accurate_rotations((stage / "original" / path.name).read_text(encoding="latin1"), data)
        source, bindings = bind_source(source, args.panel)
        if not bindings:
            continue
        encoded = source.encode("latin1")
        prepared = mdl.protect_vertex_identity(encoded)
        if mdl.supermodel(data) in missing:
            prepared = re.sub(rb"(?im)^(\s*setsupermodel\s+\S+\s+)\S+", rb"\1NULL", prepared)
        (stage / "input" / path.name).write_bytes(prepared)
        changed[path] = (data, encoded, bindings)
    if not changed:
        print("No compiled panels require torso binding.")
        return
    print(f"Compiling {len(changed)} robe models. Staging: {stage}", flush=True)
    mdl.run_compiler(compiler, stage, ["-cne", str(stage / "input/*.mdl"), str(stage / "binary") + "/"], "compile.log")
    for path, (before, source, bindings) in changed.items():
        output = stage / "binary" / path.name
        data = mdl.restore_vertex_attributes(source, output.read_bytes())
        if mdl.supermodel(before) in missing:
            patched = bytearray(data)
            patched[180:244] = mdl.supermodel(before).encode("ascii").ljust(64, b"\0")
            data = bytes(patched)
        data = preserve_bindings(before, data, bindings)
        poses.validate_body_skeleton(data, before)
        poses.validate_garment_bindings(before, data, {node[0]: node[0] for node in poses.Model(before, False).nodes})
        validate_panel_poses(before, data, bindings)
        output.write_bytes(data)
        mdl.run_compiler(compiler, stage, ["-de", str(output), str(stage / "decompiled") + "/"], "decompile.log")
        mdl.validate_round_trip(source, data, (stage / "decompiled" / path.name).read_text(encoding="latin1"))
        print(f"Validated {path.name}: {', '.join(bindings)}", flush=True)
    if args.apply:
        if MANIFEST.read_bytes() != manifest_bytes:
            raise ValueError("Robe manifest changed during validation")
        for path, (before, _, _) in changed.items():
            if path.read_bytes() != before:
                raise ValueError(f"Source changed during validation: {path}")
        for path in changed:
            data = (stage / "binary" / path.name).read_bytes()
            path.write_bytes(data)
            relative = path.relative_to(ROOT).as_posix()
            if relative in manifest["files"]:
                manifest["files"][relative] = hashlib.sha256(data).hexdigest()
            manifest["model_builds"].pop(path.stem, None)
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{'Applied' if args.apply else 'Validated'} {len(changed)} models.")


if __name__ == "__main__":
    main()
