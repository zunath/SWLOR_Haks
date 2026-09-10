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
import tempfile
import time

import CompileModels as mdl
import GenerateTintMapAssets as tint
import ImportStockRobeTints as stock_robes
import RobeAnimations as animations
import RobeSkeleton as skeleton
import RobePoseAudit as poses
import RobeBuildCache as build_cache
import RobeAnimationBanks as banks

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "sw_2da" / "roberender.2da"
PHENOTYPES = ROOT / "sw_2da" / "phenotype.2da"
MANIFEST = ROOT / "tools" / "RobeRgbModels.json"
PATTERN = re.compile(r"^(p[fm][a-z])0_robe(\d{3})$")
NODE = re.compile(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b")
GENERATOR_INPUTS = ("GenerateRobeRgbModels.py", "RobeSkeleton.py", "RobePoseAudit.py",
                    "RobeAnimations.py", "CompileModels.py", "ImportStockRobeTints.py",
                    "TintMapStockRobes.json", "GenerateTintMapAssets.py", "RobeBuildCache.py")
PACKAGING_INPUTS = ("RobeAnimationBanks.py", "RepackRobeAnimationBanks.py")
CATALOG_INPUTS = ("sw_2da/parts_robe.2da", "sw_2da/tintmap.2da", "hakbuilder.json")
BODY_RESOURCE = re.compile(r"^p[fm][a-z](\d+)(?:_robe\d{3})?$", re.IGNORECASE)


def model_directory(name):
    """Route shared robe animations separately from wearable body roots."""
    if banks.is_bank_name(name):
        return "sw_anim_f" if name[1] == "f" else "sw_anim_m"
    return "sw_pt_robe" if "_robe" in name else "sw_pt_root"


def file_digest(path):
    return content_digest(path, path.read_bytes())


def content_digest(path, data):
    # Git may check source and tables out with CRLF on Windows. Their hashes
    # describe content changes, while compiled resources remain byte-exact.
    if path.suffix.lower() in {".py", ".2da", ".json"} or (path.suffix.lower() == ".mdl" and not mdl.binary(data)):
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


def owned_animation_bytes(name, manifest, active):
    """Recover the audited compiler output only from byte-verified owned banks."""
    if not re.fullmatch(r"p[fm][a-z]_ra\d{3}", name):
        raise ValueError(f"Invalid animation bank head: {name}")
    record = manifest.get("animation_bank_sets", {}).get(name)
    if record is not None and not isinstance(record, dict):
        raise ValueError(f"Malformed animation bank record: {name}")
    names = record.get("parts") if isinstance(record, dict) else [name]
    if record is not None:
        if not isinstance(names, list) or not names:
            raise ValueError(f"Malformed animation bank record: {name}")
        expected = [name] + [f"{name}_b{i:02d}" for i in range(1, len(names))]
        if (names != expected or
                any(not banks.is_bank_name(part) for part in names) or
                not isinstance(record.get("source_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"])):
            raise ValueError(f"Malformed animation bank record: {name}")
    parts = []
    for part in names:
        relative = f"{model_directory(part)}/{part}.mdl"
        expected_path = ROOT / relative
        path = active.get(part, expected_path)
        if (expected_path.parent.resolve().parent != ROOT.resolve() or
                not path.is_file() or path.resolve() != expected_path.resolve() or
                path.resolve().parent != (ROOT / model_directory(part)).resolve()):
            raise ValueError(f"Missing or redirected animation bank: {relative}")
        data = path.read_bytes()
        if manifest.get("files", {}).get(relative) != content_digest(path, data):
            raise ValueError(f"Animation bank is not a verified prior output: {relative}")
        if record is not None and (not mdl.binary(data) or len(data) >= banks.LIMIT_BYTES):
            raise ValueError(f"Animation bank exceeds the compiled resource limit: {relative}")
        parts.append(data)
    if record is None:
        return parts[0]
    original = banks.join(parts)
    if hashlib.sha256(original).hexdigest() != record["source_sha256"]:
        raise ValueError(f"Animation bank source checksum differs: {name}")
    banks.validate_split(original, parts)
    return original


def animation_bank_errors(manifest, active):
    errors = []
    seen = set()
    records = manifest.get("animation_bank_sets", {})
    if not isinstance(records, dict):
        return ["Malformed animation bank set catalog"]
    heads = set(manifest.get("animation_bridges", {}).values())
    for head, record in records.items():
        try:
            if head not in heads:
                raise ValueError(f"Unreserved animation bank head: {head}")
            if not isinstance(record, dict) or not isinstance(record.get("parts"), list):
                raise ValueError(f"Malformed animation bank record: {head}")
            for name in record["parts"]:
                if not isinstance(name, str) or name in seen:
                    raise ValueError(f"Animation bank is shared between families: {name}")
                seen.add(name)
            owned_animation_bytes(head, manifest, active)
        except (ValueError, TypeError) as error:
            errors.append(str(error))
    return errors


def validate_animation_bank_ownership(names, manifest, active):
    """A new chunk must not overwrite an unrelated resource, even in another HAK."""
    owned = banks.bank_names(manifest)
    for name in names:
        if not banks.is_bank_name(name):
            raise ValueError(f"Invalid generated animation bank: {name}")
        expected = ROOT / model_directory(name) / f"{name}.mdl"
        path = active.get(name, expected)
        if expected.parent.resolve().parent != ROOT.resolve():
            raise ValueError(f"Generated model directory is redirected: {expected.parent}")
        if path.exists():
            relative = expected.relative_to(ROOT).as_posix()
            if (name not in owned or path.resolve() != expected.resolve() or
                    path.resolve().parent != expected.parent.resolve() or
                    manifest.get("files", {}).get(relative) != file_digest(path)):
                raise ValueError(f"Occupied animation bank is not a verified prior output: {path}")


def package_animation_models(stage, heads, generated_names, manifest, active):
    """Package validated compiler output losslessly after all pose and skin audits."""
    result = set(generated_names)
    records = dict(manifest.get("animation_bank_sets", {}))
    for head in sorted(heads):
        path = stage / "binary" / f"{head}.mdl"
        original = path.read_bytes()
        parts = banks.split(original)
        names = list(parts)
        if not names or names[0] != head or any(name in result for name in names[1:]):
            raise ValueError(f"Animation bank names collide with generated resources: {head}")
        prior = records.get(head)
        if prior is not None and prior["parts"] != names:
            raise ValueError(f"Animation bank layout changed for {head}; repack its owned banks first")
        validate_animation_bank_ownership(names, manifest, active)
        banks.validate_split(original, list(parts.values()))
        for name, data in parts.items():
            if len(data) >= banks.LIMIT_BYTES:
                raise ValueError(f"Generated model exceeds the NWSync resource limit: {name}")
            (stage / "binary" / f"{name}.mdl").write_bytes(data)
        result.update(names)
        records[head] = {"parts": names, "source_sha256": hashlib.sha256(original).hexdigest()}
    for name in result:
        path = stage / "binary" / f"{name}.mdl"
        if path.stat().st_size >= banks.LIMIT_BYTES:
            raise ValueError(f"Generated model exceeds the NWSync resource limit: {name}")
    return result, records


def check() -> list[str]:
    if not MANIFEST.is_file():
        return ["Robe RGB model manifest is missing; regenerate the robe models"]
    manifest = json.loads(MANIFEST.read_text())
    errors = []
    for relative in [f"tools/{name}" for name in GENERATOR_INPUTS + PACKAGING_INPUTS] + list(CATALOG_INPUTS):
        if relative not in manifest["files"]:
            errors.append(f"Robe RGB manifest does not track {relative}; regenerate and validate")
    if not manifest.get("independent_skeletons") or not manifest.get("body_pose_samples"):
        errors.append("Robe models lack independent skeleton and animation pose validation")
    for relative, expected in manifest["files"].items():
        path = ROOT / relative
        if not path.is_file() or file_digest(path) != expected:
            errors.append(f"Robe RGB input/output changed: {relative}; regenerate and validate")
        if path.is_file() and path.suffix.lower() == ".mdl" and path.stat().st_size >= banks.LIMIT_BYTES:
            errors.append(f"Model exceeds the NWSync resource limit: {relative}")
    active = fresh_active_models()
    errors.extend(animation_bank_errors(manifest, active))
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
        expected = ROOT / model_directory(name) / f"{name}.mdl"
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


def version_animation_families(families, manifest, verify_legacy, native_bodies=None, save_source=None):
    """A bridge's paths, bind transforms and clips are immutable for saved roots."""
    prior = dict(manifest.get("animation_bridges", {}))
    versions = {}
    for key in sorted(families.groups):
        source = families.bridge(key, "rgb_bridge")
        if save_source is not None:
            save_source(key, source)
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


def name_bridge(source, name):
    """Rename the generated root token without regenerating every controller."""
    if not re.fullmatch(r"p[fm][a-z]_ra\d{3}", name):
        raise ValueError("Invalid animation bridge name")
    return re.sub(rb"(?<![A-Za-z0-9_])rgb_bridge(?![A-Za-z0-9_])", name.encode("ascii"), source)


def stock_sources_current(game_data, manifest):
    """A fast no-op must also notice game-data changes outside the repository."""
    stock = tint.read_stock_key_models(game_data)
    validate_stock_inventory(stock)
    expected = manifest.get("stock_model_sha256")
    return isinstance(expected, dict) and all(
        name in stock and hashlib.sha256(tint.extract_stock_bif_resource(*stock[name])).hexdigest() == digest
        for name, digest in expected.items())


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
    bridges = banks.bank_names(manifest)
    for relative, digest in manifest.get("files", {}).items():
        path = ROOT / relative
        name = path.stem
        match = BODY_RESOURCE.fullmatch(name)
        if (path.suffix != ".mdl" or name in generated_names or
                not (name in bridges or match and int(match[1]) in phenotype_ids)):
            continue
        expected_directory = model_directory(name)
        if (relative != f"{expected_directory}/{name}.mdl" or not path.is_file() or
                active.get(name, path).resolve() != path.resolve() or file_digest(path) != digest):
            raise ValueError(f"Retained robe output is not a verified prior resource: {relative}")
        if path.stat().st_size >= banks.LIMIT_BYTES:
            raise ValueError(f"Retained model exceeds the NWSync resource limit; repack it first: {relative}")
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
    paths = [ROOT / "tools" / name for name in GENERATOR_INPUTS + PACKAGING_INPUTS]
    paths += [ROOT / name for name in CATALOG_INPUTS]
    files = {path.relative_to(ROOT).as_posix(): input_digests[path] for path in paths}
    files.update({active[name].relative_to(ROOT).as_posix(): content_digest(active[name], data)
                  for name, data in dependencies.items() if name in active})
    return files


def compile_model_files(compiler, stage, names, source_directory, destination_directory, mode, log):
    """Keep the native compiler timeout per model, not per growing asset library."""
    names = sorted(names)
    for index, name in enumerate(names, 1):
        mdl.run_compiler(compiler, stage,
                         [mode, str(stage / source_directory / f"{name}.mdl"),
                          str(stage / destination_directory) + "/"], log)
        if index % 250 == 0 or index == len(names):
            print(f"{mode}: {index}/{len(names)} models completed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true", help="Verify the generated catalog and its source/output hashes")
    parser.add_argument("--model", action="append", help="Restrict an experimental build; cannot apply a partial catalog")
    parser.add_argument("--stage", type=Path, help="Reuse a previous decompilation staging directory")
    parser.add_argument("--force", action="store_true", help="Run generation even when the complete installed catalog is current")
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
    if args.apply and not args.force and not check():
        manifest = json.loads(MANIFEST.read_text())
        if stock_sources_current(args.game_data, manifest):
            with tempfile.TemporaryDirectory(prefix="swlor-robe-compiler-") as directory:
                _, current_compiler_hash = mdl.prepare_compiler(Path(directory))
            if current_compiler_hash == manifest.get("compiler_sha256"):
                print("Robe catalog and all source/output hashes are current; no models need rebuilding.", flush=True)
                return
    input_paths = [ROOT / "tools" / name for name in GENERATOR_INPUTS + PACKAGING_INPUTS]
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
    bank_errors = animation_bank_errors(prior_manifest, active)
    if bank_errors:
        raise ValueError("\n".join(bank_errors))
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

    reference_models = {}
    def load_reference(name):
        if name in reference_models:
            return reference_models[name]
        data = dependencies.get(name)
        if data is None:
            return None
        if not mdl.binary(data):
            reference_directory = stage / "reference"
            reference_directory.mkdir(exist_ok=True)
            mdl.run_compiler(compiler, stage, ["-cne", str(stage / f"{name}.mdl"), str(reference_directory) + "/"], "reference.log")
            data = (reference_directory / f"{name}.mdl").read_bytes()
        reference_models[name] = data
        return data
    # Authored animation overlays remain ASCII source assets. The pose/part-ID audit
    # needs compiled references for any owner in the chain, not only body roots.
    reference_bodies = {name[:3] + "0": load_reference(name[:3] + "0") for name in selected}
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
        compiled = owned_animation_bytes(name, prior_manifest, active)
        staged = stage / f"{name}.mdl"
        staged.write_bytes(compiled)
        mdl.run_compiler(compiler, stage, ["-de", str(staged), str(legacy_directory) + "/"], "legacy_bridges.log")
        try:
            mdl.validate_round_trip(families.bridge(key, name), compiled,
                                    (legacy_directory / f"{name}.mdl").read_text(encoding="latin1"))
            animations.validate_animation_parts(compiled)
            base = reference_bodies[families.groups[key]["base"]]
            animations.validate_body_parts(compiled, base, base)
            return True
        except ValueError as error:
            legacy_bridge_changes[name] = str(error)
            return False
    source_directory = stage / "bridge_sources"
    source_directory.mkdir(exist_ok=True)
    source_paths = {}
    def save_bridge_source(key, source):
        path = source_directory / (hashlib.sha256(key.encode()).hexdigest() + ".mdl")
        path.write_bytes(source)
        source_paths[key] = path
    versions, prior_manifest = version_animation_families(
        families, prior_manifest, verify_legacy_bridge, dependencies, save_bridge_source)
    versioned_groups = {versions[key]: group for key, group in families.groups.items()}
    animation_names = allocate_animation_bridges(versioned_groups, active, prior_manifest)
    for key, group in sorted(families.groups.items()):
        parent = animation_names[versions[key]]
        sources[parent] = name_bridge(source_paths[key].read_bytes(), parent)
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
    # A cache hit requires the exact generated source, compiler, current parent,
    # validation code, original inverse binds and owned binary to agree.
    validation_digest = hashlib.sha256(json.dumps(
        {name: input_digests[ROOT / "tools" / name] for name in GENERATOR_INPUTS},
        sort_keys=True).encode()).hexdigest()
    build_keys, reused, parent_digests = {}, set(), {}
    def compile_one(name):
        source = sources[name]
        parent = mdl.supermodel(source)
        if parent not in parent_digests:
            parent_data = ((stage / "binary" / f"{parent}.mdl").read_bytes()
                           if parent in sources else load_reference(parent) if parent else b"")
            if parent_data is None:
                raise ValueError(f"{name}: missing compiled parent {parent}")
            parent_digests[parent] = hashlib.sha256(parent_data).hexdigest()
        original = dependencies[original_robes[name]] if name in original_robes else b""
        base = reference_bodies[name[:3] + "0"] if name in complete_roots else b""
        validation_key = hashlib.sha256(bytes.fromhex(validation_digest) +
                                       hashlib.sha256(original).digest() + hashlib.sha256(base).digest()).hexdigest()
        key = build_cache.build_key(source, compiler_hash, parent_digests[parent], validation_key)
        build_keys[name] = key
        previous = active.get(name)
        bank_record = prior_manifest.get("animation_bank_sets", {}).get(name)
        previous_data = (owned_animation_bytes(name, prior_manifest, active) if bank_record is not None
                         else previous.read_bytes() if previous is not None else b"")
        relative = f"{model_directory(name)}/{name}.mdl"
        path = stage / "binary" / f"{name}.mdl"
        ownership_hash = (bank_record["source_sha256"] if bank_record is not None else
                          prior_manifest.get("files", {}).get(relative))
        if build_cache.reusable(prior_manifest.get("model_builds", {}).get(name), key, previous_data,
                                ownership_hash):
            path.write_bytes(previous_data)
            reused.add(name)
            return
        mdl.run_compiler(compiler, stage, ["-cne", str(stage / "input" / f"{name}.mdl"),
                                          str(stage / "binary") + "/"], "compile.log")
        compiled = mdl.restore_vertex_attributes(source, path.read_bytes())
        compiled = poses.repair_compiler_skin_bindings(compiled)
        if name in renamed_nodes:
            compiled = poses.preserve_skin_bindings(original, compiled, renamed_nodes[name])
        path.write_bytes(compiled)
    # Compile and expose the complete animation parents before their body roots.
    compiled_parents = set(animation_cache.values()) - set(dependencies)
    print(f"Building or reusing {len(sources)} generated models. Staging: {stage}", flush=True)
    for name in sorted(compiled_parents):
        compile_one(name)
        shutil.copyfile(stage / "binary" / f"{name}.mdl", stage / f"{name}.mdl")
    for index, name in enumerate(sorted(set(sources) - compiled_parents), 1):
        compile_one(name)
        if index % 250 == 0:
            print(f"Prepared {index} body/attachment models; {len(reused)} cache hits.", flush=True)
    changed = set(sources) - reused
    compile_model_files(compiler, stage, changed, "binary", "decompiled", "-de", "decompile.log")
    failures = []
    for name, source in sources.items():
        if name in reused:
            continue
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
        return path.read_bytes() if name in sources else load_reference(name)
    library = poses.Library(load_binary)
    checked_poses = 0
    if not failures:
        for key, parent in animation_cache.items():
            checked_poses += poses.validate_body_poses(parent, families.groups[key]["base"], library)
    report = {"models": len(sources), "compiled_models": len(changed), "reused_models": len(reused),
              "robe_models": len(selected), "phenotypes": len(id_map),
              "body_pose_samples": checked_poses,
              "independent_skeletons": len(complete_roots), "animation_families": len(animation_cache),
              "missing_animation_fallbacks": families.fallbacks,
              "versioned_legacy_bridges": legacy_bridge_changes,
              "validated_animation_roots": len(complete_roots),
              "compiler_sha256": compiler_hash, "missing_supermodels": sorted(missing), "failures": failures}
    (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if failures:
        raise ValueError(f"{len(failures)} round-trip failures: {failures[:10]}")
    # Cache provenance describes the original validated compiler output. Packaging
    # changes its file layout only; joining verified banks recovers these exact bytes.
    model_builds = {name: build_cache.make_record(build_keys[name],
                    (stage / "binary" / f"{name}.mdl").read_bytes()) for name in sorted(sources)}
    packaged_names, bank_sets = package_animation_models(
        stage, animation_cache.values(), sources, prior_manifest, active)
    report["packaged_models"] = len(packaged_names)
    report["largest_model_bytes"] = max(
        ((stage / "binary" / f"{name}.mdl").stat().st_size for name in packaged_names), default=0)
    (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n")
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
        validate_animation_bank_ownership(
            {name for head in animation_cache.values() for name in bank_sets[head]["parts"]},
            prior_manifest, current_active)
        retained = retained_model_files(prior_manifest, packaged_names, set(id_map.values()), current_active)
        # Hash validated inputs from their captured bytes and outputs from staging.
        # Copying thousands of outputs must never re-certify later input edits.
        output_files = {f"sw_2da/{path.name}": file_digest(stage / path.name) for path in (TABLE, PHENOTYPES)}
        output_files.update({f"{model_directory(name)}/{name}.mdl":
                             file_digest(stage / "binary" / f"{name}.mdl") for name in packaged_names})
        for name in packaged_names:
            directory = ROOT / model_directory(name)
            if directory.resolve().parent != ROOT.resolve():
                raise ValueError(f"Generated model directory is redirected: {directory}")
            directory.mkdir(exist_ok=True)
            shutil.copyfile(stage / "binary" / f"{name}.mdl", directory / f"{name}.mdl")
        shutil.copyfile(stage / "roberender.2da", TABLE)
        shutil.copyfile(stage / "phenotype.2da", PHENOTYPES)
        manifest = {"compiler_sha256": compiler_hash, "independent_skeletons": True,
                    "model_builds": model_builds,
                    "body_pose_samples": checked_poses,
                    "missing_animation_fallbacks": families.fallbacks,
                    "stock_model_sha256": stock_hashes,
                    "animation_bridges": {**prior_manifest.get("animation_bridges", {}), **animation_names},
                    "animation_bank_sets": dict(sorted(bank_sets.items())),
                    "files": dict(sorted({**retained, **input_files, **output_files}.items()))}
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
