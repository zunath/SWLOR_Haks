#!/usr/bin/env python3
"""Generate robe-bearing body roots so EE replays material RGB on their meshes.

The ordinary separate robe is replaced by an empty attachment only for the
generated phenotype. Original models, palettes, and part-hiding rows remain
available. Output is staged and round-trip audited before --apply writes it.
"""
from __future__ import annotations

import argparse
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
import RobeSkeleton as skeleton
import RobePoseAudit as poses

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "sw_2da" / "roberender.2da"
PHENOTYPES = ROOT / "sw_2da" / "phenotype.2da"
MANIFEST = ROOT / "tools" / "RobeRgbModels.json"
PATTERN = re.compile(r"^(p[fm][a-z])0_robe(\d{3})$")
NODE = re.compile(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b")
GENERATOR_INPUTS = ("GenerateRobeRgbModels.py", "RobeSkeleton.py", "RobePoseAudit.py",
                    "RobeAnimations.py", "CompileModels.py", "ImportStockRobeTints.py",
                    "TintMapStockRobes.json", "GenerateTintMapAssets.py")
CATALOG_INPUTS = ("sw_2da/parts_robe.2da", "sw_2da/tintmap.2da", "hakbuilder.json")
BODY_RESOURCE = re.compile(r"^p[fm][a-z](\d+)(?:_robe\d{3})?$", re.IGNORECASE)


def file_digest(path):
    data = path.read_bytes()
    # Git may check source and tables out with CRLF on Windows. Their hashes
    # describe content changes, while compiled resources remain byte-exact.
    if path.suffix in {".py", ".2da", ".json"}:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


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


def fresh_active_models():
    tint._ACTIVE_MODELS = None
    tint._HAK_DIRECTORIES = None
    return tint.find_active_models()


def model_path_errors(files, stock_names, active):
    errors = []
    for relative in files:
        path = ROOT / relative
        if path.suffix.lower() == ".mdl" and (
                path.stem.lower() not in active or
                active[path.stem.lower()].resolve() != path.resolve()):
            errors.append(f"Robe RGB model is no longer the active resource: {relative}")
    for name in stock_names:
        if name in active:
            errors.append(f"Robe RGB stock dependency is now overridden: {name}")
    return errors


def check() -> list[str]:
    if not MANIFEST.is_file():
        return ["Robe RGB model manifest is missing; regenerate the robe models"]
    manifest = json.loads(MANIFEST.read_text())
    errors = []
    for relative in [f"tools/{name}" for name in GENERATOR_INPUTS] + list(CATALOG_INPUTS):
        if relative not in manifest["files"]:
            errors.append(f"Robe RGB manifest does not track {relative}; regenerate and validate")
    if not manifest.get("independent_skeletons") or not manifest.get("body_pose_samples"):
        errors.append("Robe models lack independent skeleton and animation pose validation")
    for relative, expected in manifest["files"].items():
        path = ROOT / relative
        if not path.is_file() or file_digest(path) != expected:
            errors.append(f"Robe RGB input/output changed: {relative}; regenerate and validate")
    active = fresh_active_models()
    if "stock_model_sha256" not in manifest:
        errors.append("Robe RGB manifest lacks complete stock dependency provenance; regenerate")
    errors.extend(model_path_errors(manifest["files"], manifest.get("stock_model_sha256", {}), active))
    registered = {line.split()[1].lower() for line in tint.OUTPUT_2DA.read_text().splitlines()[3:] if len(line.split()) >= 4}
    expected = {name for name in active if PATTERN.fullmatch(name) and name in registered}
    if set(read_mappings()) != expected:
        errors.append("Robe RGB catalog does not cover exactly the registered normal-body robes")
    if not stock_robes.MANIFEST.is_file():
        errors.append("Stock robe inventory is missing; run ImportStockRobeTints.py")
    else:
        stock = json.loads(stock_robes.MANIFEST.read_text())
        if {row["model"] for row in stock["models"]} != set(stock["stockModelSha256"]):
            errors.append("Stock robe inventory differs from its recorded source models; rerun the importer")
        for row in stock["models"]:
            if row["model"] not in expected:
                errors.append(f"Selectable stock robe {row['model']} is missing its RGB conversion")
    return errors


def allocate_phenotypes(ids, prior, native_table, active):
    id_map = {}
    for name, phenotype in prior.items():
        robe_id = int(PATTERN.fullmatch(name)[2])
        if robe_id in id_map and id_map[robe_id] != phenotype:
            raise ValueError("Inconsistent persisted robe phenotype assignments")
        id_map[robe_id] = phenotype
    used = set(id_map.values())
    for line in native_table[3:]:
        cols = line.split()
        if not cols or not cols[0].isdigit():
            continue
        phenotype = int(cols[0])
        if len(cols) > 1 and cols[1].startswith("RobeRgb_"):
            robe_id = int(cols[1].split("_")[1])
            if robe_id in id_map and id_map[robe_id] != phenotype:
                raise ValueError("Inconsistent persisted robe phenotype assignments")
            id_map[robe_id] = phenotype
        used.add(phenotype)
    # Model resources can occupy an otherwise unused table row. Reserve an ID if
    # ANY body prefix already has its root or robe attachment, including other HAKs.
    used.update(int(match[1]) for name in active if (match := BODY_RESOURCE.fullmatch(name)))
    for robe_id in ids:
        if robe_id in id_map:
            continue
        available = next((i for i in range(34, 256) if i not in used), None)
        if available is None:
            raise ValueError("The native phenotype byte has no available slot; cannot truncate IDs")
        id_map[robe_id] = available
        used.add(available)
    return id_map


def validate_output_ownership(selected, id_map, prior, active, manifest):
    for name in selected:
        prefix, robe_id = PATTERN.fullmatch(name).groups()
        phenotype = id_map[int(robe_id)]
        root_name = prefix + str(phenotype)
        for generated, directory in ((root_name, "sw_pt_root"),
                                     (root_name + "_robe" + robe_id, "sw_pt_robe")):
            if generated not in active:
                continue
            path = active[generated]
            expected = ROOT / directory / f"{generated}.mdl"
            relative = expected.relative_to(ROOT).as_posix()
            if (prior.get(name) != phenotype or path.resolve() != expected.resolve() or
                    manifest.get("files", {}).get(relative) != file_digest(path)):
                raise ValueError(f"Occupied robe output is not a verified prior mapping: {path}")


def allocate_animation_bridges(groups, active, manifest, expected_allocation=None):
    prior = manifest.get("animation_bridges", {})
    if len(set(prior.values())) != len(prior):
        raise ValueError("Animation families share a persisted bridge name")
    # Retired families can still be referenced by saved creatures' body roots.
    for key, name in prior.items():
        if not re.fullmatch(r"p[fm][a-z]_ra\d{3}", name):
            raise ValueError(f"Invalid persisted animation resource: {name}")
        expected = ROOT / "sw_pt_root" / f"{name}.mdl"
        path = active.get(name, expected)
        if path.is_file():
            relative = expected.relative_to(ROOT).as_posix()
            if (path.resolve() != expected.resolve() or
                    manifest.get("files", {}).get(relative) != file_digest(path)):
                raise ValueError(f"Occupied animation bridge is not a verified prior output: {path}")
    reserved = set(active) | set(prior.values())
    result = {}
    for key, group in sorted(groups.items()):
        prefix = group["base"][:3]
        if key in prior:
            name = prior[key]
            if not name.startswith(prefix + "_ra"):
                raise ValueError(f"Persisted bridge has the wrong body prefix: {name}")
        else:
            name = next((f"{prefix}_ra{i:03d}" for i in range(1, 1000)
                         if f"{prefix}_ra{i:03d}" not in reserved), None)
            if name is None:
                raise ValueError(f"No animation bridge resref available for {prefix}")
        reserved.add(name)
        result[key] = name
    if expected_allocation is not None and result != expected_allocation:
        raise ValueError("Animation bridge allocation changed during generation; retry with the new active resources")
    return result


def version_animation_families(families, manifest, verify_legacy, native_bodies=None):
    """A bridge's paths, bind transforms and clips are immutable for saved roots."""
    prior = dict(manifest.get("animation_bridges", {}))
    versions = {}
    for key in sorted(families.groups):
        source = families.bridge(key, "rgb_bridge")
        # Native parent part IDs are binary metadata, absent from bridge ASCII.
        # A parent change must also preserve the old bridge for retained roots.
        parent = (native_bodies or {}).get(families.groups[key]["base"], b"")
        fingerprint = hashlib.sha256(source).digest() + hashlib.sha256(parent).digest()
        version = key + "/" + hashlib.sha256(fingerprint).hexdigest()
        # Upgrade old, unversioned records only after comparing the existing
        # binary's complete geometry/controllers with this exact source.
        if version not in prior and key in prior and verify_legacy(key, prior[key]):
            prior[version] = prior.pop(key)
        versions[key] = version
    return versions, {**manifest, "animation_bridges": prior}


def validate_stock_inventory(stock_models):
    styles = stock_robes.selectable_styles()
    expected = {name for name in stock_models
                if (match := stock_robes.PATTERN.fullmatch(name)) and int(match[1]) in styles}
    stock = json.loads(stock_robes.MANIFEST.read_text())
    if ({row["model"] for row in stock["models"]} != expected or
            set(stock["stockModelSha256"]) != expected):
        raise ValueError("Stock robe inventory is incomplete; run ImportStockRobeTints.py before generation")


def retained_model_files(manifest, generated_names, phenotype_ids, active):
    """Keep audited roots, attachments and bridges for persisted retired phenotypes."""
    retained = {}
    bridges = set(manifest.get("animation_bridges", {}).values())
    for relative, digest in manifest.get("files", {}).items():
        path = ROOT / relative
        name = path.stem
        match = BODY_RESOURCE.fullmatch(name)
        if (path.suffix != ".mdl" or name in generated_names or
                not (name in bridges or match and int(match[1]) in phenotype_ids)):
            continue
        expected_directory = "sw_pt_robe" if "_robe" in name else "sw_pt_root"
        if (relative != f"{expected_directory}/{name}.mdl" or not path.is_file() or
                active.get(name, path).resolve() != path.resolve() or file_digest(path) != digest):
            raise ValueError(f"Retained robe output is not a verified prior resource: {relative}")
        retained[relative] = digest
    return retained


def validate_input_snapshot(dependencies, active, input_digests):
    for name, data in dependencies.items():
        if name in active and (not active[name].is_file() or active[name].read_bytes() != data):
            raise ValueError(f"Source changed during robe generation: {active[name]}")
    for path, digest in input_digests.items():
        if not path.is_file() or file_digest(path) != digest:
            raise ValueError(f"Input changed during robe generation: {path}")


def snapshot_manifest_inputs(dependencies, active, input_digests):
    paths = [ROOT / "tools" / name for name in GENERATOR_INPUTS]
    paths += [ROOT / name for name in CATALOG_INPUTS]
    files = {path.relative_to(ROOT).as_posix(): input_digests[path] for path in paths}
    files.update({active[name].relative_to(ROOT).as_posix(): hashlib.sha256(data).hexdigest()
                  for name, data in dependencies.items() if name in active})
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true", help="Verify the generated catalog and its source/output hashes")
    parser.add_argument("--model", action="append", help="Restrict an experimental build; cannot apply a partial catalog")
    parser.add_argument("--stage", type=Path, help="Reuse a previous decompilation staging directory")
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
    input_paths = [ROOT / "tools" / name for name in GENERATOR_INPUTS]
    input_paths += [ROOT / name for name in CATALOG_INPUTS] + [TABLE, PHENOTYPES, MANIFEST]
    input_digests = {path: file_digest(path) for path in input_paths if path.is_file()}
    active = fresh_active_models()
    registered = {line.split()[1].lower() for line in tint.OUTPUT_2DA.read_text().splitlines()[3:] if len(line.split()) >= 4}
    selected = {name: path for name, path in active.items() if PATTERN.fullmatch(name) and name in registered}
    ids = sorted({int(PATTERN.fullmatch(name)[2]) for name in selected})
    prior = read_mappings()
    native_table = PHENOTYPES.read_text().splitlines()
    id_map = allocate_phenotypes(ids, prior, native_table, active)
    prior_manifest = json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else {}
    validate_output_ownership(selected, id_map, prior, active, prior_manifest)
    if args.model:
        selected = {name: path for name, path in selected.items() if name in args.model}
        if len(selected) != len(set(args.model)):
            raise ValueError("Requested prototype is not a registered normal-body robe")
    stage = args.stage.resolve() if args.stage else ROOT / "output" / f"robe-rgb-{time.time_ns()}"
    for directory in ("original", "source", "input", "binary", "decompiled"):
        (stage / directory).mkdir(parents=True, exist_ok=True)
    compiler, compiler_hash = mdl.prepare_compiler(stage)
    stock = tint.read_stock_key_models(args.game_data)
    validate_stock_inventory(stock)
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
        text_cache[name] = poses.accurate_rotations(path.read_text(encoding="latin1"), dependencies[name])
        return text_cache[name]

    reference_bodies = {}
    for base_name in {name[:3] + "0" for name in selected}:
        data = dependencies[base_name]
        if not mdl.binary(data):
            reference_directory = stage / "reference"
            reference_directory.mkdir(exist_ok=True)
            mdl.run_compiler(compiler, stage, ["-cne", str(stage / f"{base_name}.mdl"), str(reference_directory) + "/"], "reference.log")
            data = (reference_directory / f"{base_name}.mdl").read_bytes()
        reference_bodies[base_name] = data
    def load_reference(name):
        return reference_bodies.get(name, dependencies.get(name))
    original_library = poses.Library(load_reference)

    prior_manifest = json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else {}
    animation_cache = {}
    families = skeleton.Families(load_text, original_library.body_track_names)
    complete_roots = set()
    sources = {}
    renamed_nodes = {}
    original_robes = {}
    mapping_rows = []
    print(f"Preparing independent rigs for {len(selected)} robes.", flush=True)
    for name in sorted(selected):
        robe = unique_nodes(load_text(name), dependencies[name])
        families.add(name[:3] + "0", robe, name)
    print(f"Building {len(families.groups)} shared animation families.", flush=True)
    legacy_directory = stage / "legacy_bridges"
    legacy_directory.mkdir(exist_ok=True)
    legacy_bridge_changes = {}
    def verify_legacy_bridge(key, name):
        path = active.get(name)
        if path is None:
            return False
        mdl.run_compiler(compiler, stage, ["-de", str(path), str(legacy_directory) + "/"], "legacy_bridges.log")
        try:
            compiled = path.read_bytes()
            mdl.validate_round_trip(families.bridge(key, name), compiled,
                                    (legacy_directory / f"{name}.mdl").read_text(encoding="latin1"))
            animations.validate_animation_parts(compiled)
            base = reference_bodies[families.groups[key]["base"]]
            animations.validate_body_parts(compiled, base, base)
            return True
        except ValueError as error:
            legacy_bridge_changes[name] = str(error)
            return False
    versions, prior_manifest = version_animation_families(
        families, prior_manifest, verify_legacy_bridge, dependencies)
    versioned_groups = {versions[key]: group for key, group in families.groups.items()}
    animation_names = allocate_animation_bridges(versioned_groups, active, prior_manifest)
    for key, group in sorted(families.groups.items()):
        parent = animation_names[versions[key]]
        sources[parent] = families.bridge(key, parent)
        animation_cache[key] = parent
    for name in sorted(selected):
        prefix, robe_id = PATTERN.fullmatch(name).groups()
        phenotype = id_map[int(robe_id)]
        generated = prefix + str(phenotype)
        base_name = prefix + "0"
        animation_parent = animation_cache[families.members[name][0]]
        sources[generated], renamed_nodes[generated] = families.body_root(name, generated, animation_parent)
        original_robes[generated] = name
        complete_roots.add(generated)
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
    for pattern in ([name + ".mdl" for name in sources] if args.model else ["*.mdl"]):
        mdl.run_compiler(compiler, stage, ["-cne", str(stage / "input" / pattern), str(stage / "binary") + "/"], "compile.log")
    for name, source in sources.items():
        path = stage / "binary" / f"{name}.mdl"
        compiled = mdl.restore_vertex_attributes(source, path.read_bytes())
        compiled = poses.repair_compiler_skin_bindings(compiled)
        if name in renamed_nodes:
            compiled = poses.preserve_skin_bindings(dependencies[original_robes[name]], compiled, renamed_nodes[name])
        path.write_bytes(compiled)
    for pattern in ([name + ".mdl" for name in sources] if args.model else ["*.mdl"]):
        mdl.run_compiler(compiler, stage, ["-de", str(stage / "binary" / pattern), str(stage / "decompiled") + "/"], "decompile.log")
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
                poses.validate_body_skeleton(compiled, reference_bodies[name[:3] + "0"])
                poses.validate_garment_bindings(dependencies[original_robes[name]], compiled, renamed_nodes[name])
        except ValueError as error:
            failures.append({"model": name, "error": str(error)})
    def load_binary(name):
        if name in reference_bodies:
            return reference_bodies[name]
        path = stage / "binary" / f"{name}.mdl"
        return path.read_bytes() if name in sources else dependencies.get(name)
    library = poses.Library(load_binary)
    checked_poses = 0
    if not failures:
        for key, parent in animation_cache.items():
            checked_poses += poses.validate_body_poses(parent, families.groups[key]["base"], library)
    report = {"models": len(sources), "robe_models": len(selected), "phenotypes": len(id_map),
              "body_pose_samples": checked_poses,
              "independent_skeletons": len(complete_roots), "animation_families": len(animation_cache),
              "missing_animation_fallbacks": families.fallbacks,
              "versioned_legacy_bridges": legacy_bridge_changes,
              "validated_animation_roots": len(complete_roots),
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
        validate_input_snapshot(dependencies, active, input_digests)
        input_files = snapshot_manifest_inputs(dependencies, active, input_digests)
        stock_hashes = {name: hashlib.sha256(data).hexdigest()
                        for name, data in sorted(dependencies.items()) if name not in active}
        current_active = fresh_active_models()
        path_errors = model_path_errors(input_files, stock_hashes, current_active)
        if path_errors:
            raise ValueError("\n".join(path_errors))
        validate_output_ownership(selected, id_map, prior, current_active, prior_manifest)
        allocate_animation_bridges(versioned_groups, current_active, prior_manifest, animation_names)
        retained = retained_model_files(prior_manifest, sources, set(id_map.values()), current_active)
        # Hash validated inputs from their captured bytes and outputs from staging.
        # Copying thousands of outputs must never re-certify later input edits.
        output_files = {f"sw_2da/{path.name}": file_digest(stage / path.name) for path in (TABLE, PHENOTYPES)}
        output_files.update({("sw_pt_robe" if "_robe" in name else "sw_pt_root") + f"/{name}.mdl":
                             file_digest(stage / "binary" / f"{name}.mdl") for name in sources})
        for name in sources:
            directory = "sw_pt_robe" if "_robe" in name else "sw_pt_root"
            shutil.copyfile(stage / "binary" / f"{name}.mdl", ROOT / directory / f"{name}.mdl")
        shutil.copyfile(stage / "roberender.2da", TABLE)
        shutil.copyfile(stage / "phenotype.2da", PHENOTYPES)
        manifest = {"compiler_sha256": compiler_hash, "independent_skeletons": True,
                    "body_pose_samples": checked_poses,
                    "missing_animation_fallbacks": families.fallbacks,
                    "stock_model_sha256": stock_hashes,
                    "animation_bridges": {**prior_manifest.get("animation_bridges", {}), **animation_names},
                    "files": dict(sorted({**retained, **input_files, **output_files}.items()))}
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
