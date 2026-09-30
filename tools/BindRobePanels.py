"""Bind static robe attachments to their body bones through the skin renderer.

Rigid child meshes can remain displaced after animation changes even without
cloth simulation. A single torso weight preserves their rigid shape while using
the same bone-following renderer as the sleeves and skirt. Existing geometry,
materials, transforms, native animation IDs and other skin bindings are kept.
Use --all to audit/repair the catalog, or --robe/--panel for a selected covering.
Moving cloth and independently animated attachments retain their authored motion.
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
import StripInheritedBoneTracks as tracks

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/RobeRgbModels.json"
BODY_BONE = re.compile(r"(?:rg_)?(?:rootdummy|(?:torso|pelvis|neck|head|[lr](?:bicep|forearm|hand|thigh|shin|foot))_g)(?:_\d+)?\Z")


def catalog_targets(manifest):
    return sorted(set((ROOT / "sw_pt_robe").glob("*.mdl")) |
                  {ROOT / "sw_pt_root" / f"{name}.mdl" for name in manifest["garment_node_names"]})


def unique_leaf_names(data, animated_parts=(), selected_names=None):
    """Disambiguate unanimated, unreferenced visible leaves without moving nodes."""
    if not mdl.binary(data):
        return data
    model = poses.Model(data, False)
    seen, duplicates = set(), []
    for index, node in enumerate(model.nodes):
        if node[0] in seen and (selected_names is None or node[0] in selected_names):
            duplicates.append((index, node))
        seen.add(node[0])
    if not duplicates:
        return data
    bones = set()
    for _, _, _, _, offset in model.nodes:
        flags = model.uint(offset + 108)
        if flags & 0x40:
            mesh = offset + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
            bones.update(i for i in struct.unpack_from("<17h", data, mesh + 576) if i >= 0)
    result = bytearray(data)
    for index, (name, _, part, _, offset) in duplicates:
        if name in animated_parts or part >= 0 and part in animated_parts or index in bones or model.uint(offset + 76):
            raise ValueError(f"{name}: duplicate node is an animated or referenced joint")
        renamed = f"{name}_copy{index}"
        if renamed in seen or len(renamed) > 31:
            raise ValueError(f"{name}: cannot disambiguate duplicate leaf")
        result[offset + 32:offset + 64] = renamed.encode("ascii").ljust(32, b"\0")
        seen.add(renamed)
    return bytes(result)


class AnimationParts:
    def __init__(self, load):
        self.load, self.cache = load, {}

    def inherited(self, data, pending=()):
        parent = mdl.supermodel(data)
        result = set()
        if parent:
            if parent in pending:
                raise ValueError(f"Cyclic animation parent: {parent}")
            if parent not in self.cache:
                source = self.load(parent)
                self.cache[parent] = self.inherited(source, (*pending, parent)) if source else set()
            result.update(self.cache[parent])
        if mdl.binary(data):
            for _, offset in tracks.animation_nodes(data):
                part = struct.unpack_from("<i", data, offset + 28)[0]
                if part < 0:
                    continue
                keys, count = struct.unpack_from("<II", data, offset + 84)
                if any(struct.unpack_from("<IH", data, 12 + keys + i * 12)[0] in (8, 20, 36)
                       and struct.unpack_from("<H", data, 12 + keys + i * 12 + 4)[0] for i in range(count)):
                    result.add(part)
        else:
            # ASCII animation parents bind by name; keep those attachments animated.
            for _, name, props in mdl.parse_nodes(data.decode("latin1").split("endmodelgeom", 1)[-1]):
                if set(props) & {"position", "orientation", "scale", "positionkey", "orientationkey", "scalekey"}:
                    result.add(name)
        return result


def panel_candidates(data, animated_parts=()):
    """Find visible static attachments by hierarchy, never by a robe/name list."""
    animated_parts = set(animated_parts)
    if mdl.binary(data):
        model = poses.Model(data, False)
        records = []
        for name, parent, part, _, offset in model.nodes:
            flags = model.uint(offset + 108)
            props = {}
            if flags & 0x20:
                mesh = offset + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
                props["visible"] = bool(model.uint(mesh + 108) and struct.unpack_from("<H", data, mesh + 448)[0])
                if flags & 0x100 and not flags & 0x40:
                    pointer, count = struct.unpack_from("<II", data, mesh + 512)
                    props["moving"] = any(struct.unpack_from(f"<{count}f", data, 12 + pointer))
            records.append((name, parent, part, flags, props))
    else:
        nodes = mdl.parse_nodes(data.decode("latin1").split("endmodelgeom", 1)[0])
        indices = {name: index for index, (_, name, _) in enumerate(nodes)}
        if len(indices) != len(nodes):
            raise ValueError("Duplicate ASCII nodes have no unambiguous hierarchy")
        records = []
        for kind, name, props in nodes:
            flags = {"trimesh": 0x21, "skin": 0x61, "danglymesh": 0x121}.get(kind, 1)
            records.append((name, indices.get(props.get("parent", [""])[0]), -1, flags,
                            {"visible": bool(props.get("verts") and props.get("render", ["1"]) != ["0"]),
                             "moving": any(float(row[0]) for row in props.get("constraints", []))}))
    candidates, moving, animated = {}, [], []
    for index, (name, parent, part, flags, props) in enumerate(records):
        if flags & 0x40 or not props.get("visible") or BODY_BONE.fullmatch(name):
            continue
        ancestors, current = [index], parent
        while current is not None and not BODY_BONE.fullmatch(records[current][0]):
            ancestors.append(current)
            current = records[current][1]
        if current is None:
            continue
        if props.get("moving"):
            moving.append(name)
            continue
        if any(records[i][0] in animated_parts or records[i][2] >= 0 and records[i][2] in animated_parts for i in ancestors):
            animated.append(name)
            continue
        candidates[name] = records[current][0]
    return candidates, moving, animated


def bind_source(text, panels, bones=None, scales=None):
    selected = set(panels)
    bindings = {}

    def replace(match):
        kind, name, props = mdl.parse_nodes(match[0])[0]
        authored = name.removeprefix("rm_").removeprefix("rg_")
        if (name not in selected and authored not in selected) or not props.get("verts"):
            return match[0]
        bone = bones[name] if bones is not None else props.get("parent", [""])[0]
        if not BODY_BONE.fullmatch(bone) or (bones is None and not re.fullmatch(r"(?:rg_)?torso_g(?:_\d+)?", bone)):
            raise ValueError(f"{name}: selected panel is not attached to a torso")
        weights = [[bone, "1"] for _ in props["verts"]]
        if kind == "skin" and mdl.equivalent(props.get("weights"), weights):
            return match[0]
        if kind == "danglymesh":
            constraints = props.get("constraints", [])
            if len(constraints) != len(props["verts"]) or any(float(row[0]) for row in constraints):
                raise ValueError(f"{name}: moving or incomplete cloth must retain its simulation")
            for key in ("constraints", "period", "tightness", "displacement"):
                props.pop(key, None)
        elif kind != "trimesh":
            raise ValueError(f"{name}: expected a rigid panel, found {kind}")
        if any(key.endswith("key") for key in props):
            raise ValueError(f"{name}: a static panel must not have animated controllers")
        factor = scales.get(name, 1) if scales is not None else 1
        if factor != 1:
            props["verts"] = [[format(float(value) * factor, '.17g') for value in row] for row in props["verts"]]
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
        if not math.isfinite(bs) or bs <= 0 or not math.isfinite(ms) or ms <= 0:
            raise ValueError(f"{mesh}: bind scales must be finite and positive")
        inverse = (-bq[0], -bq[1], -bq[2], bq[3])
        translation = poses.rotate(inverse, tuple((a-b)/bs for a, b in zip(mp, bp)))
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
    bind = original.pose()
    scales = {mesh: bind[mesh][2]/bind[bone][2] for mesh, bone in bindings.items()}
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
                    rigid_vertex = tuple(a+b for a, b in zip(mp, poses.rotate(mq, tuple(v*ms/scales[mesh] for v in vertex))))
                    bound = tuple(a+b for a, b in zip(translation, poses.rotate((x, y, z, w), vertex)))
                    skin_vertex = tuple(a+b for a, b in zip(bp, poses.rotate(bq, tuple(v*bs for v in bound))))
                    if math.dist(rigid_vertex, skin_vertex) > 2e-5:
                        raise ValueError(f"{mesh}: skin placement differs at phase {fraction}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robe", type=int)
    parser.add_argument("--panel", action="append", default=[])
    parser.add_argument("--all", action="store_true", help="Repair all visible static body-bone attachments")
    parser.add_argument("--audit", action="store_true", help="Report catalog coverage without compiling or writing models")
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.all == (args.robe is not None) or (not args.all and not args.panel):
        parser.error("Use --all, or --robe with --panel")
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    paths = catalog_targets(manifest) if args.all else rigid.targets(args.robe, manifest)
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(args.game_data)
    def read_model(name):
        if name in active:
            return active[name].read_bytes()
        if name in stock:
            return tint.extract_stock_bif_resource(*stock[name])
        return None

    animation_parts = AnimationParts(read_model)
    selected, candidates, ascii_models, moving_count, animated_count = {}, {}, [], 0, 0
    for path in paths:
        raw = path.read_bytes()
        if args.all:
            animated_parts = animation_parts.inherited(raw)
            panels, moving, animated = panel_candidates(raw, animated_parts)
            if panels and mdl.binary(raw):
                data = unique_leaf_names(raw, animated_parts, panels)
                panels, _, _ = panel_candidates(data, animated_parts)
            moving_count += len(moving)
            animated_count += len(animated)
            if not mdl.binary(raw):
                ascii_models.append(path.name)
                if panels and any(kind == "skin" for kind, _, _ in mdl.parse_nodes(raw.decode("latin1"))):
                    raise ValueError(f"{path.name}: ASCII skin bindings have no compiled preservation reference")
            if not panels:
                continue
            candidates[path] = panels
        elif not mdl.binary(raw):
            print(f"Skipping ASCII model {path.name}: compiled input is required for binding preservation.")
            continue
        selected[path] = raw
    print(f"Audited {len(paths)} models ({len(ascii_models)} ASCII); {len(selected)} models need binding. "
          f"Preserved {moving_count} moving cloth meshes and {animated_count} independently animated attachments.", flush=True)
    if args.audit:
        for path, panels in candidates.items():
            print(f"{path.relative_to(ROOT)}: {', '.join(panels)}")
        if selected:
            raise SystemExit(1)
        return
    if not selected:
        print("No compiled panels require bone binding.")
        return
    stage = ROOT / "output" / f"bound-robe-{'catalog' if args.all else args.robe}-{time.time_ns()}"
    for folder in (stage, stage / "reference", stage / "reference_binary", stage / "original", stage / "input", stage / "binary", stage / "decompiled"):
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
    print(f"Preparing {len(selected)} models. Staging: {stage}", flush=True)
    for index, (path, raw) in enumerate(selected.items(), 1):
        data = unique_leaf_names(raw, animation_parts.inherited(raw), candidates[path]) if args.all else raw
        load(path.stem)
        reference = stage / "reference" / path.name
        if not mdl.binary(data):
            reference_source = data
            prepared = mdl.protect_vertex_identity(data)
            if mdl.supermodel(data) in missing:
                prepared = re.sub(rb"(?im)^(\s*setsupermodel\s+\S+\s+)\S+", rb"\1NULL", prepared)
            reference.write_bytes(prepared)
            mdl.run_compiler(compiler, stage, ["-cne", str(reference), str(stage / "reference_binary") + "/"], "reference-compile.log")
            data = mdl.restore_vertex_attributes(reference_source, (stage / "reference_binary" / path.name).read_bytes())
            if mdl.supermodel(reference_source) in missing:
                patched = bytearray(data)
                patched[180:244] = mdl.supermodel(reference_source).encode("ascii").ljust(64, b"\0")
                data = bytes(patched)
        reference.write_bytes(data)
        mdl.run_compiler(compiler, stage, ["-de", str(reference), str(stage / "original") + "/"], "original.log")
        if not mdl.binary(raw):
            mdl.validate_round_trip(raw, data, (stage / "original" / path.name).read_text(encoding="latin1"))
        source = poses.accurate_rotations((stage / "original" / path.name).read_text(encoding="latin1"), data)
        bones = candidates[path] if args.all else None
        world = poses.Model(data, False).pose()
        scales = {mesh: world[mesh][2]/world[bone][2] for mesh, bone in bones.items()} if bones else None
        source, bindings = bind_source(source, candidates[path] if args.all else args.panel, bones, scales)
        if not bindings:
            continue
        encoded = source.encode("latin1")
        prepared = mdl.protect_vertex_identity(encoded)
        if mdl.supermodel(data) in missing:
            prepared = re.sub(rb"(?im)^(\s*setsupermodel\s+\S+\s+)\S+", rb"\1NULL", prepared)
        (stage / "input" / path.name).write_bytes(prepared)
        changed[path] = (raw, data, encoded, bindings)
        if args.all and index % 100 == 0:
            print(f"Prepared {index}/{len(selected)} models.", flush=True)
    if not changed:
        print("No compiled panels require torso binding.")
        return
    print(f"Compiling {len(changed)} robe models. Staging: {stage}", flush=True)
    mdl.run_compiler(compiler, stage, ["-cne", str(stage / "input/*.mdl"), str(stage / "binary") + "/"], "compile.log")
    for index, (path, (_, before, source, bindings)) in enumerate(changed.items(), 1):
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
        if not args.all:
            print(f"Validated {path.name}: {', '.join(bindings)}", flush=True)
        elif index % 100 == 0 or index == len(changed):
            print(f"Validated {index}/{len(changed)} models.", flush=True)
    if args.apply:
        if MANIFEST.read_bytes() != manifest_bytes:
            raise ValueError("Robe manifest changed during validation")
        for path, (before, _, _, _) in changed.items():
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
