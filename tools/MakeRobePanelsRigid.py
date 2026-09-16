"""Compile fully constrained robe panels as ordinary meshes, including RGB bodies.

Zero-constraint panels have no authored cloth movement. Keeping them on the
dangly renderer can detach the panel from its animated parent. Convert only
those panels; keep every vertex, material, joint, animation and skin binding.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import CompileModels as mdl
import GenerateTintMapAssets as tint
import RobePoseAudit as poses
import RobeSkeleton as skeleton

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/RobeRgbModels.json"
NODE = re.compile(r"(?im)^\s*node\s+\S+\s+\S+\s*$[\s\S]*?^\s*endnode\b[^\r\n]*")


def rigid_source(text):
    changed = []
    for kind, name, props in mdl.parse_nodes(text):
        if kind != "danglymesh" or not props.get("verts"):
            continue
        constraints = props.get("constraints", [])
        if not constraints or len(constraints) != len(props.get("verts", [])):
            raise ValueError(f"{name}: missing or incomplete cloth constraints")
        if any(len(row) != 1 or float(row[0]) != 0 for row in constraints):
            continue
        changed.append(name)
    def replace(match):
        kind, name, props = mdl.parse_nodes(match[0])[0]
        if kind != "danglymesh" or name not in changed:
            return match[0]
        for key in ("constraints", "displacement", "period", "tightness"):
            props.pop(key, None)
        return "\n" + skeleton.serialize("trimesh", name, props)
    result = NODE.sub(replace, text)
    return result, changed


def targets(robe, manifest):
    suffix = f"_robe{robe:03d}"
    paths = set((ROOT / "sw_pt_robe").glob(f"*{suffix}.mdl"))
    for root, names in manifest["garment_node_names"].items():
        if any(name.endswith(suffix) for name in names):
            paths.add(ROOT / "sw_pt_root" / f"{root}.mdl")
    if not paths:
        raise ValueError(f"No models found for robe {robe}")
    return sorted(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robe", type=int, required=True)
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(args.game_data)
    stage = ROOT / "output" / f"rigid-robe-{args.robe}-{time.time_ns()}"
    for folder in (stage, stage / "original", stage / "input", stage / "binary", stage / "decompiled"):
        folder.mkdir(parents=True, exist_ok=True)
    compiler, _ = mdl.prepare_compiler(stage)
    dependencies = {}
    missing = set()
    def load(name):
        if not name or name in dependencies:
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
    for path in targets(args.robe, manifest):
        data = path.read_bytes()
        model = poses.Model(data, False)
        if not any(model.uint(node[4] + 108) & 0x100 for node in model.nodes):
            continue
        load(path.stem)
        mdl.run_compiler(compiler, stage, ["-de", str(path), str(stage / "original") + "/"], "original.log")
        source = poses.accurate_rotations((stage / "original" / path.name).read_text(encoding="latin1"), data)
        source, panels = rigid_source(source)
        if not panels:
            continue
        encoded = source.encode("latin1")
        prepared = mdl.protect_vertex_identity(encoded)
        if model.parent in missing:
            # Legacy phenotype 22 parents are absent from both the HAKs and
            # stock data. Match the compiler's existing missing-parent workflow;
            # the compiled part-ID audit below must still pass unchanged.
            prepared = re.sub(rb"(?im)^(\s*setsupermodel\s+\S+\s+)\S+", rb"\1NULL", prepared)
        (stage / "input" / path.name).write_bytes(prepared)
        changed[path] = (data, encoded, panels)
    if not changed:
        print("No fully constrained dangly panels remain.")
        return
    print(f"Compiling {len(changed)} robe models. Staging: {stage}", flush=True)
    mdl.run_compiler(compiler, stage, ["-cne", str(stage / "input/*.mdl"), str(stage / "binary") + "/"], "compile.log")
    for path, (before, source, panels) in changed.items():
        output = stage / "binary" / path.name
        data = mdl.restore_vertex_attributes(source, output.read_bytes())
        if mdl.supermodel(before) in missing:
            patched = bytearray(data)
            patched[180:244] = mdl.supermodel(before).encode("ascii").ljust(64, b"\0")
            data = bytes(patched)
        names = {node[0]: node[0] for node in poses.Model(before, False).nodes}
        data = poses.preserve_skin_bindings(before, data, names)
        poses.validate_body_skeleton(data, before)
        poses.validate_garment_bindings(before, data, names)
        output.write_bytes(data)
        mdl.run_compiler(compiler, stage, ["-de", str(output), str(stage / "decompiled") + "/"], "decompile.log")
        mdl.validate_round_trip(source, data, (stage / "decompiled" / path.name).read_text(encoding="latin1"))
        print(f"Validated {path.name}: {', '.join(panels)}", flush=True)
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
            # A changed source/output must acquire a fresh proof on regeneration.
            manifest["model_builds"].pop(path.stem, None)
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{'Applied' if args.apply else 'Validated'} {len(changed)} models.")


if __name__ == "__main__":
    main()
