#!/usr/bin/env python3
"""Generate robe-bearing body roots so EE replays material RGB on their meshes.

The ordinary separate robe is replaced by an empty attachment only for the
generated phenotype. Original models, palettes, and part-hiding rows remain
available. Output is staged and round-trip audited before --apply writes it.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import time

import CompileModels as mdl
import GenerateTintMapAssets as tint
import ImportStockRobeTints as stock_robes
import RobeAnimations as animations

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "sw_2da" / "roberender.2da"
PHENOTYPES = ROOT / "sw_2da" / "phenotype.2da"
MANIFEST = ROOT / "tools" / "RobeRgbModels.json"
PATTERN = re.compile(r"^(p[fm][a-z])0_robe(\d{3})$")
NODE = re.compile(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b")


def nodes(text):
    return list(NODE.finditer(text.split("endmodelgeom", 1)[0]))


def unique_nodes(text: str, data: bytes) -> str:
    """Disambiguate legacy exports only after checking their binary bone links."""
    matches = nodes(text)
    if len({m[2].lower() for m in matches}) == len(matches):
        return text
    if not mdl.binary(data):
        raise ValueError("Duplicate ASCII nodes have no unambiguous bone references")
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    offsets = []
    def walk(pointer):
        node = pointer + 12
        offsets.append(node)
        for i in range(uint(node + 76)):
            walk(uint(12 + uint(node + 72) + i * 4))
    walk(uint(84))
    if len(offsets) != len(matches):
        raise ValueError("Decompiled hierarchy does not match the binary")
    bones = set()
    for node in offsets:
        flags = uint(node + 108)
        if flags & 0x40:
            mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
            bones.update(i for i in struct.unpack_from("<17h", data, mesh + 576) if i >= 0)
    animated = {m[2].lower() for m in NODE.finditer(text.split("endmodelgeom", 1)[1])}
    seen = set()
    replacements = []
    for index, (match, offset) in enumerate(zip(matches, offsets)):
        name = match[2].lower()
        binary_name = data[offset + 32:offset + 64].split(b"\0", 1)[0].decode("ascii").lower()
        if binary_name != name:
            raise ValueError("Decompiled node order does not match the binary")
        if name in seen:
            if uint(offset + 76) or index in bones:
                raise ValueError(f"Duplicate node {name} is a referenced joint")
            if match[1].lower() == "dummy" or re.search(r"(?im)^\s*render\s+0\s*$", match[3]):
                replacement = ""  # Unreferenced, invisible leaf; retain the actual joint.
            else:
                if name in animated:
                    raise ValueError(f"Duplicate visible node {name} has ambiguous animations")
                unique = f"{name}_copy{index}"
                if len(unique) > 31 or unique in seen:
                    raise ValueError(f"Cannot name duplicate mesh {name}")
                replacement = f"\nnode {match[1]} {unique}\n{match[3]}\nendnode\n"
                seen.add(unique)
            replacements.append((match.start(), match.end(), replacement))
        seen.add(name)
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text


def body_root(robe: str, base: str, name: str, source_name: str | None = None) -> bytes:
    """Retain robe bind poses; fill its missing body/weapon/appendage joints."""
    original = re.search(r"(?im)^\s*newmodel\s+(\S+)", robe)[1]
    base_name = re.search(r"(?im)^\s*newmodel\s+(\S+)", base)[1]
    animation_scale = re.search(r"(?im)^\s*setanimationscale\s+(\S+)", robe)
    animation_scale = animation_scale[1] if animation_scale else "1"
    # The caller supplies a complete animation skeleton; a separate robe can
    # omit body joints and is not a suitable immediate animation parent.
    result = []
    known = set()
    hidden_joints = {}
    for match in nodes(robe):
        kind, joint, body = match[1], match[2], match[3]
        if joint.lower() in known:
            signature = tuple(re.findall(r"(?im)^\s*(parent|position|orientation|scale)\s+([^\n]+)", body))
            if re.search(r"(?im)^\s*render\s+0\s*$", body) and hidden_joints.get(joint.lower()) == signature:
                # Some exports repeat the same hidden bind-pose joint verbatim.
                # It has no visible geometry and all weights target the same name.
                continue
            raise ValueError(f"{original}: duplicate robe node {joint}")
        known.add(joint.lower())
        if re.search(r"(?im)^\s*render\s+0\s*$", body):
            hidden_joints[joint.lower()] = tuple(re.findall(r"(?im)^\s*(parent|position|orientation|scale)\s+([^\n]+)", body))
        if joint.lower() == original.lower():
            joint = name
        body = re.sub(r"(?im)^(\s*parent\s+)" + re.escape(original) + r"\s*$", r"\g<1>" + name, body)
        if joint.lower().endswith("_g") and kind.lower() != "dummy":
            transform = re.findall(r"(?im)^\s*(?:position|orientation|scale)\s+[^\n]+", body)
            parent = re.search(r"(?im)^\s*parent\s+(\S+)", body)[1]
            result.append(f"node dummy {joint}\nparent {parent}\n" + "\n".join(transform) + "\nendnode\n")
            if re.search(r"(?im)^\s*render\s+0\s*$", body):
                continue
            body = re.sub(r"(?im)^\s*parent\s+\S+", "\nparent " + joint, body)
            body = re.sub(r"(?im)^\s*(?:position|orientation|scale)\s+[^\n]+", "", body)
            body += "\nposition 0 0 0\norientation 0 0 0 0\n"
            mesh_name = "robe_" + joint
            if len(mesh_name) > 31 or mesh_name.lower() in known:
                raise ValueError(f"{original}: invalid generated mesh name {mesh_name}")
            known.add(mesh_name.lower())
            result.append(f"node {kind} {mesh_name}\n{body}\nendnode\n")
        else:
            result.append(f"node {kind} {joint}\n{body}\nendnode\n")
    for match in nodes(base):
        joint = match[2].lower()
        if joint == base_name.lower() or joint in known:
            continue
        body = re.sub(r"(?im)^(\s*parent\s+)" + re.escape(base_name) + r"\s*$", r"\g<1>" + name, match[0])
        result.append(body + "\n")
        known.add(joint)
    children = defaultdict(list)
    for node in result:
        parent = re.search(r"(?im)^\s*parent\s+(\S+)", node)[1].lower()
        children[parent].append(node)
    ordered = []
    def visit(parent):
        for node in children[parent]:
            ordered.append(node)
            if len(ordered) > len(result):
                raise ValueError(f"{original}: cyclic hierarchy")
            visit(re.search(r"(?im)^\s*node\s+\S+\s+(\S+)", node)[1].lower())
    visit("null")
    if len(ordered) != len(result):
        raise ValueError(f"{original}: orphaned nodes")
    output = (f"newmodel {name}\nsetsupermodel {name} {source_name or original}\nclassification CHARACTER\n"
              f"setanimationscale {animation_scale}\nbeginmodelgeom {name}\n" + "".join(ordered) +
              f"endmodelgeom {name}\ndonemodel {name}\n")
    validate_transformation(robe, output, name)
    return output.encode("latin1")


def validate_transformation(robe: str, generated: str, name: str) -> None:
    """Compare against authored geometry as well as the compiler round trip."""
    original = re.search(r"(?im)^\s*newmodel\s+(\S+)", robe)[1].lower()
    targets = {node: (kind, props) for kind, node, props in mdl.parse_nodes(generated)}
    for kind, node, props in mdl.parse_nodes(robe.split("endmodelgeom", 1)[0]):
        target = name if node == original else node
        expected = dict(props)
        if expected.get("parent") == [original]:
            expected["parent"] = [name]
        target_kind, actual = targets[target]
        transforms = ("position", "orientation", "scale", "parent")
        for key in transforms:
            if actual.get(key) != expected.get(key):
                raise ValueError(f"{original}/{node}: authored bind transform {key} changed")
        if kind != "dummy" and node.endswith("_g"):
            if props.get("render") == ["0"]:
                continue
            target_kind, actual = targets["robe_" + node]
            expected = {key: value for key, value in expected.items() if key not in transforms}
            actual = {key: value for key, value in actual.items() if key not in transforms}
        if target_kind != kind or expected != actual:
            raise ValueError(f"{original}/{node}: authored geometry, skin weights, or material properties changed")


def empty_attachment(name: str) -> bytes:
    if len(name) > 16:
        raise ValueError(f"Resource name exceeds engine limit: {name}")
    return (f"newmodel {name}\nsetsupermodel {name} NULL\nclassification CHARACTER\n"
            f"setanimationscale 1\nbeginmodelgeom {name}\nnode dummy {name}\n"
            f"parent NULL\nendnode\nendmodelgeom {name}\ndonemodel {name}\n").encode("ascii")


def read_mappings():
    rows = {}
    if TABLE.is_file():
        for line in TABLE.read_text().splitlines()[3:]:
            cols = line.split()
            if len(cols) >= 4:
                rows[cols[1].lower()] = int(cols[2])
    return rows


def check() -> list[str]:
    if not MANIFEST.is_file():
        return ["Robe RGB model manifest is missing; regenerate the robe models"]
    manifest = json.loads(MANIFEST.read_text())
    errors = []
    for relative, expected in manifest["files"].items():
        path = ROOT / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"Robe RGB input/output changed: {relative}; regenerate and validate")
    registered = {line.split()[1].lower() for line in tint.OUTPUT_2DA.read_text().splitlines()[3:] if len(line.split()) >= 4}
    expected = {name for name in tint.find_active_models() if PATTERN.fullmatch(name) and name in registered}
    if set(read_mappings()) != expected:
        errors.append("Robe RGB catalog does not cover exactly the registered normal-body robes")
    if not stock_robes.MANIFEST.is_file():
        errors.append("Stock robe inventory is missing; run ImportStockRobeTints.py")
    else:
        stock = json.loads(stock_robes.MANIFEST.read_text())
        for row in stock["models"]:
            if row["model"] not in expected:
                errors.append(f"Selectable stock robe {row['model']} is missing its RGB conversion")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true", help="Verify the generated catalog and its source/output hashes")
    parser.add_argument("--model", action="append", help="Restrict an experimental build; cannot apply a partial catalog")
    parser.add_argument("--stage", type=Path, help="Reuse a previous decompilation staging directory")
    parser.add_argument("--complete-animation-style", type=int, action="append", default=[],
                        help="Validate a robe style against a complete body/garment animation skeleton")
    args = parser.parse_args()
    if args.check:
        errors = check()
        if errors:
            raise ValueError("\n".join(errors[:20]))
        print("Robe RGB catalog, source models, and generated resources verified.")
        return
    if not args.game_data:
        parser.error("--game-data is required when generating models")
    if args.model and args.apply:
        parser.error("--apply requires the complete catalog")
    active = tint.find_active_models()
    registered = {line.split()[1].lower() for line in tint.OUTPUT_2DA.read_text().splitlines()[3:] if len(line.split()) >= 4}
    selected = {name: path for name, path in active.items() if PATTERN.fullmatch(name) and name in registered}
    ids = sorted({int(PATTERN.fullmatch(name)[2]) for name in selected})
    prior = read_mappings()
    id_map = {}
    for name, phenotype in prior.items():
        robe_id = int(PATTERN.fullmatch(name)[2])
        if robe_id in id_map and id_map[robe_id] != phenotype:
            raise ValueError("Inconsistent persisted robe phenotype assignments")
        id_map[robe_id] = phenotype
    used = set(id_map.values())
    native_table = PHENOTYPES.read_text().splitlines()
    for line in native_table[3:]:
        cols = line.split()
        if len(cols) > 1 and cols[1].startswith("RobeRgb_"):
            id_map[int(cols[1].split("_")[1])] = int(cols[0])
            used.add(int(cols[0]))
        if cols and cols[0].isdigit() and not (len(cols) > 1 and cols[1].startswith("RobeRgb_")):
            used.add(int(cols[0]))
    for robe_id in ids:
        if robe_id in id_map:
            continue
        available = next((i for i in range(34, 256) if i not in used), None)
        if available is None:
            raise ValueError("The native phenotype byte has no available slot; cannot truncate IDs")
        id_map[robe_id] = available
        used.add(available)
    if args.model:
        selected = {name: path for name, path in selected.items() if name in args.model}
        if len(selected) != len(set(args.model)):
            raise ValueError("Requested prototype is not a registered normal-body robe")
    stage = args.stage.resolve() if args.stage else ROOT / "output" / f"robe-rgb-{time.time_ns()}"
    for directory in ("original", "source", "input", "binary", "decompiled"):
        (stage / directory).mkdir(parents=True, exist_ok=True)
    compiler, compiler_hash = mdl.prepare_compiler(stage)
    stock = tint.read_stock_key_models(args.game_data)
    dependencies = {}
    missing = set()
    def load(name):
        if name in dependencies or name in missing:
            return
        if name in active:
            data = active[name].read_bytes()
        elif name in stock:
            data = tint.extract_stock_bif_resource(*stock[name])
        else:
            missing.add(name)
            return
        dependencies[name] = data
        parent = mdl.supermodel(data)
        if parent:
            load(parent)
    for name in selected:
        load(name)
        load(PATTERN.fullmatch(name)[1] + "0")
    reusable = set()
    for name, data in dependencies.items():
        if (stage / f"{name}.mdl").is_file() and (stage / f"{name}.mdl").read_bytes() == data:
            reusable.add(name)
        (stage / f"{name}.mdl").write_bytes(data)
    originals = set(selected) | {PATTERN.fullmatch(name)[1] + "0" for name in selected}
    for name in originals:
        if name not in dependencies:
            raise ValueError(f"Missing required body root {name}")
        data = dependencies[name]
        if name in reusable and (stage / "original" / f"{name}.mdl").is_file():
            continue
        if mdl.binary(data):
            mdl.run_compiler(compiler, stage, ["-de", str(stage / f"{name}.mdl"), str(stage / "original") + "/"], "original.log")
        else:
            (stage / "original" / f"{name}.mdl").write_bytes(data)
    text_cache = {}
    def load_text(name):
        if name in text_cache:
            return text_cache[name]
        if name not in dependencies:
            return None
        path = stage / "original" / f"{name}.mdl"
        if name not in reusable or not path.is_file():
            data = dependencies[name]
            if mdl.binary(data):
                mdl.run_compiler(compiler, stage, ["-de", str(stage / f"{name}.mdl"), str(stage / "original") + "/"], "original.log")
            else:
                path.write_bytes(data)
            reusable.add(name)
        text_cache[name] = path.read_text(encoding="latin1")
        return text_cache[name]

    prior_manifest = json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else {}
    animation_styles = set(prior_manifest.get("complete_animation_styles", [])) | set(args.complete_animation_style)
    if not animation_styles.issubset(id_map):
        raise ValueError("Animation styles must be existing robe catalog entries")
    animation_cache = {}
    animation_names = dict(prior_manifest.get("animation_bridges", {}))
    body_inheritance = animations.BodyAnimationInheritance(load_text)
    direct_body_roots = set()
    complete_roots = set()
    sources = {}
    mapping_rows = []
    for name in sorted(selected):
        prefix, robe_id = PATTERN.fullmatch(name).groups()
        phenotype = id_map[int(robe_id)]
        generated = prefix + str(phenotype)
        base_name = prefix + "0"
        robe = unique_nodes(load_text(name), dependencies[name])
        animation_parent = name
        if body_inheritance.can_inherit(base_name, robe):
            animation_parent = base_name
            direct_body_roots.add(generated)
            complete_roots.add(generated)
        elif int(robe_id) in animation_styles:
            complete_roots.add(generated)
            animation_owner = next((owner for owner, text in animations.chain(name, load_text)
                                    if animations.ANIMATION.search(text)), base_name)
            animation_key = base_name + "/" + animation_owner
            if animation_key not in animation_cache:
                if animation_key not in animation_names:
                    used_names = set(animation_names.values())
                    animation_names[animation_key] = next(f"{prefix}_ra{i:03d}" for i in range(1, 1000)
                                                           if f"{prefix}_ra{i:03d}" not in used_names)
                animation_name = animation_names[animation_key]
                animation_source = animations.bridge(base_name, name, load_text, animation_name)
                if animation_source:
                    sources[animation_name] = animation_source
                    animation_cache[animation_key] = animation_name
                else:
                    animation_cache[animation_key] = base_name
            animation_parent = animation_cache[animation_key]
        sources[generated] = body_root(robe, load_text(base_name), generated, animation_parent)
        sources[generated + "_robe" + robe_id] = empty_attachment(generated + "_robe" + robe_id)
        mapping_rows.append((name, phenotype, 0))
    for name, data in sources.items():
        (stage / "source" / f"{name}.mdl").write_bytes(data)
        (stage / "input" / f"{name}.mdl").write_bytes(mdl.protect_vertex_identity(data))
    # Compile and expose the complete animation parents before their body roots.
    for name in sorted(set(animation_cache.values()) - set(dependencies)):
        mdl.run_compiler(compiler, stage, ["-cne", str(stage / "input" / f"{name}.mdl"), str(stage / "binary") + "/"], "compile.log")
        shutil.copyfile(stage / "binary" / f"{name}.mdl", stage / f"{name}.mdl")
    print(f"Compiling {len(sources)} generated models. Staging: {stage}", flush=True)
    mdl.run_compiler(compiler, stage, ["-cne", str(stage / "input" / "*.mdl"), str(stage / "binary") + "/"], "compile.log")
    for name, source in sources.items():
        path = stage / "binary" / f"{name}.mdl"
        path.write_bytes(mdl.restore_vertex_attributes(source, path.read_bytes()))
    mdl.run_compiler(compiler, stage, ["-de", str(stage / "binary" / "*.mdl"), str(stage / "decompiled") + "/"], "decompile.log")
    reference_bodies = {}
    for base_name in {name[:3] + "0" for name in complete_roots}:
        data = dependencies[base_name]
        if not mdl.binary(data):
            reference_directory = stage / "reference"
            reference_directory.mkdir(exist_ok=True)
            mdl.run_compiler(compiler, stage, ["-cne", str(stage / f"{base_name}.mdl"), str(reference_directory) + "/"], "reference.log")
            data = (reference_directory / f"{base_name}.mdl").read_bytes()
        reference_bodies[base_name] = data
    failures = []
    for name, source in sources.items():
        try:
            compiled = (stage / "binary" / f"{name}.mdl").read_bytes()
            mdl.validate_round_trip(source, compiled,
                                    (stage / "decompiled" / f"{name}.mdl").read_text(encoding="latin1"))
            if name in animation_cache.values():
                animations.validate_animation_parts(compiled)
            elif name in complete_roots:
                parent = mdl.supermodel(compiled)
                parent_path = stage / "binary" / f"{parent}.mdl"
                parent_data = (reference_bodies[parent] if parent in reference_bodies else
                               parent_path.read_bytes() if parent_path.is_file() else dependencies[parent])
                animations.validate_body_parts(compiled, reference_bodies[name[:3] + "0"], parent_data)
        except ValueError as error:
            failures.append({"model": name, "error": str(error)})
    report = {"models": len(sources), "robe_models": len(selected), "phenotypes": len(id_map),
              "direct_body_roots": len(direct_body_roots), "validated_animation_roots": len(complete_roots),
              "compiler_sha256": compiler_hash, "missing_supermodels": sorted(missing), "failures": failures}
    (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if failures:
        raise ValueError(f"{len(failures)} round-trip failures: {failures[:10]}")
    table = "2DA V2.0\n\n    MODEL PHENOTYPE BASEPHENOTYPE\n"
    table += "".join(f"{i} {name} {phenotype} {base}\n" for i, (name, phenotype, base) in enumerate(mapping_rows))
    phenotype_lines = [line for line in native_table if "RobeRgb_" not in line]
    for robe_id, phenotype in sorted(id_map.items(), key=lambda pair: pair[1]):
        phenotype_lines.append(f"{phenotype} RobeRgb_{robe_id:03d} **** 0")
    (stage / "roberender.2da").write_text(table, encoding="ascii")
    (stage / "phenotype.2da").write_text("\n".join(phenotype_lines).rstrip() + "\n", encoding="ascii")
    if args.apply:
        for name in sources:
            directory = "sw_pt_robe" if "_robe" in name else "sw_pt_root"
            shutil.copyfile(stage / "binary" / f"{name}.mdl", ROOT / directory / f"{name}.mdl")
        shutil.copyfile(stage / "roberender.2da", TABLE)
        shutil.copyfile(stage / "phenotype.2da", PHENOTYPES)
        paths = [TABLE, PHENOTYPES]
        paths.extend(active[name] for name in dependencies if name in active)
        paths.extend(ROOT / ("sw_pt_robe" if "_robe" in name else "sw_pt_root") / f"{name}.mdl" for name in sources)
        manifest = {"compiler_sha256": compiler_hash,
                    "stock_body_sha256": {name: hashlib.sha256(dependencies[name]).hexdigest()
                                          for name in sorted(originals) if name not in active},
                    "animation_bridges": animation_names,
                    "complete_animation_styles": sorted(animation_styles),
                    "files": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted(set(paths))}}
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
