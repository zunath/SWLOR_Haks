#!/usr/bin/env python3
"""Split existing compiled robe bridges without rebuilding wearable models.

All source/output ownership checks, exact binary reconstruction and native
decompilation complete in staging before --apply replaces any HAK resource.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time

import CompileModels as mdl
import GenerateRobeRgbModels as robes
import RobeAnimationBanks as banks
import RobeAnimations as animations
import RobePoseAudit as poses


def preflight(manifest):
    # This operation changes packaging only. It may refresh the wrapper and its
    # packer, but must never re-certify changed authoring/pose/skin inputs.
    packaging = {"tools/GenerateRobeRgbModels.py", *(f"tools/{name}" for name in robes.PACKAGING_INPUTS)}
    for relative, expected in manifest["files"].items():
        path = robes.ROOT / relative
        if not path.resolve().is_relative_to(robes.ROOT.resolve()):
            raise ValueError(f"Manifest path escapes repository: {relative}")
        if relative not in packaging and (not path.is_file() or robes.file_digest(path) != expected):
            raise ValueError(f"Prior validated input/output changed: {relative}")
    errors = robes.model_path_errors(manifest["files"], manifest.get("stock_model_sha256", {}),
                                     robes.fresh_active_models())
    if errors:
        raise ValueError("\n".join(errors))
    return {relative: robes.file_digest(robes.ROOT / relative) for relative in packaging}


def cached_audit_matches(cached, head, source_sha, validation_sha, stage):
    """A receipt is reusable only for the complete, unchanged native bank chain."""
    try:
        if (not isinstance(cached, dict) or cached.get("head") != head or
                cached.get("source_sha256") != source_sha or cached.get("validation_sha256") != validation_sha or
                not isinstance(cached.get("banks"), dict) or not cached["banks"] or
                not isinstance(cached.get("bank_sha256"), dict)):
            return False
        names = list(cached["banks"])
        expected = [head] + [f"{head}_b{i:02d}" for i in range(1, len(names))]
        if names != expected or names != list(cached["bank_sha256"]) or not all(map(banks.is_bank_name, names)):
            return False
        parts = []
        for name in names:
            size = cached["banks"][name]
            path = stage / "binary" / f"{name}.mdl"
            if (type(size) is not int or not 0 < size < banks.LIMIT_BYTES or not path.is_file() or
                    path.stat().st_size != size or path.resolve().parent != (stage / "binary").resolve()):
                return False
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != cached["bank_sha256"][name]:
                return False
            parts.append(data)
        original = banks.join(parts)
        return (hashlib.sha256(original).hexdigest() == source_sha and cached.get("original_bytes") == len(original) and
                cached.get("clips") == sum(int.from_bytes(data[136:140], "little") for data in parts))
    except (ValueError, TypeError, KeyError, OSError):
        return False


def independent_audit(original, parts, stage, compiler):
    """Use the existing pose reader and the native tool, independent of relocation."""
    expected = poses.Model(original)
    seen = set()
    for name, data in parts.items():
        animations.validate_animation_parts(data)
        current = poses.Model(data)
        for clip, (duration, nodes) in current.clips.items():
            if clip in seen or clip not in expected.clips:
                raise ValueError(f"Duplicated or unexpected animation: {name}/{clip}")
            old_duration, old_nodes = expected.clips[clip]
            # Root names change with bank names; parent indices, numeric part
            # IDs and every controller value/time must remain exactly equal.
            normalize = lambda rows: [(parent, part, controls) for _, parent, part, controls, _ in rows]
            if duration != old_duration or normalize(nodes) != normalize(old_nodes):
                raise ValueError(f"Native pose data changed: {name}/{clip}")
            seen.add(clip)
        mdl.run_compiler(compiler, stage,
                         ["-de", str(stage / "binary" / f"{name}.mdl"), str(stage / "decompiled") + "/"],
                         "native-read.log")
        path = stage / "decompiled" / f"{name}.mdl"
        text = path.read_text(encoding="latin1")
        decoded = [match[1].lower() for match in animations.ANIMATION.finditer(text)]
        if decoded != list(current.clips):
            raise ValueError(f"Native decompiler animation inventory differs: {name}")
        # The large ASCII copies are disposable verification output, not assets.
        path.unlink()
    if seen != set(expected.clips):
        raise ValueError("Partitioned bridge is missing animations")
    return len(seen)


def repack_family(head, stage, input_manifest, validation_sha):
    """Bounded foreground worker; each native compiler has its own staging area."""
    manifest = json.loads(input_manifest.read_text())
    original = robes.owned_animation_bytes(head, manifest, {})
    source_sha = hashlib.sha256(original).hexdigest()
    stage = stage / head
    for directory in ("binary", "decompiled"):
        (stage / directory).mkdir(parents=True, exist_ok=True)
    receipt = stage / "audit.json"
    if receipt.is_file():
        try:
            cached = json.loads(receipt.read_text())
        except (ValueError, OSError):
            cached = None
        if cached_audit_matches(cached, head, source_sha, validation_sha, stage):
            return cached
    compiler, compiler_hash = mdl.prepare_compiler(stage)
    if compiler_hash != manifest["compiler_sha256"]:
        raise ValueError("Native compiler changed since the robe catalog was validated")
    parts = banks.split(original)
    for name, data in parts.items():
        (stage / "binary" / f"{name}.mdl").write_bytes(data)
    clips = independent_audit(original, parts, stage, compiler)
    report = {"head": head, "original_bytes": len(original), "clips": clips,
              "source_sha256": source_sha, "validation_sha256": validation_sha,
              "banks": {name: len(data) for name, data in parts.items()},
              "bank_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in parts.items()}}
    receipt.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stage", type=Path, help="Keep validated binaries and report in this staging directory")
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 5),
                        help="Bounded parallel validation workers (default: 2)")
    args = parser.parse_args()
    initial = robes.MANIFEST.read_bytes()
    manifest = json.loads(initial)
    packaging = preflight(manifest)
    active = robes.fresh_active_models()
    errors = robes.animation_bank_errors(manifest, active)
    if errors:
        raise ValueError("\n".join(errors))
    heads = [name for name in sorted(set(manifest["animation_bridges"].values()))
             if f"{robes.model_directory(name)}/{name}.mdl" in manifest["files"]]
    stage = (args.stage or robes.ROOT / "output" / f"nwsync-banks-{time.time_ns()}").resolve()
    if not stage.is_relative_to((robes.ROOT / "output").resolve()):
        raise ValueError("Use a staging directory inside this repository's output folder")
    stage.mkdir(parents=True, exist_ok=True)
    input_manifest = stage / "manifest-input.json"
    input_manifest.write_bytes(initial)
    validation_sha = hashlib.sha256(json.dumps({**packaging, "compiler": manifest["compiler_sha256"],
        **{f"tools/{name}": robes.file_digest(robes.ROOT / "tools" / name)
           for name in ("CompileModels.py", "RobePoseAudit.py", "RobeAnimations.py")}}, sort_keys=True).encode()).hexdigest()
    updated = copy.deepcopy(manifest)
    records = updated.setdefault("animation_bank_sets", {})
    output_names, report = set(), []
    sources = {}
    with ProcessPoolExecutor(max_workers=args.jobs) as workers:
        futures = [workers.submit(repack_family, head, stage, input_manifest, validation_sha) for head in heads]
        for index, future in enumerate(as_completed(futures), 1):
            entry = future.result()
            head, names = entry["head"], list(entry["banks"])
            if head in records and records[head]["parts"] != names:
                raise ValueError(f"Existing bank layout differs for {head}; do not overwrite its reserved children")
            robes.validate_animation_bank_ownership(names, manifest, active)
            records[head] = {"parts": names, "source_sha256": entry["source_sha256"]}
            for name in names:
                updated["files"][f"{robes.model_directory(name)}/{name}.mdl"] = entry["bank_sha256"][name]
                sources[name] = stage / head / "binary" / f"{name}.mdl"
            output_names.update(names)
            report.append(entry)
            print(f"Verified {index}/{len(heads)}: {head}, {entry['clips']} clips, {len(names)} banks; "
                  f"largest {max(entry['banks'].values()):,} bytes.", flush=True)
    report.sort(key=lambda entry: entry["head"])
    updated["animation_bank_sets"] = dict(sorted(records.items()))
    updated["files"].update(packaging)
    updated["files"] = dict(sorted(updated["files"].items()))
    (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (stage / "RobeRgbModels.json").write_text(json.dumps(updated, indent=2) + "\n")
    if args.apply:
        if robes.MANIFEST.read_bytes() != initial or preflight(manifest) != packaging:
            raise ValueError("Inputs changed while validating animation banks")
        robes.validate_animation_bank_ownership(output_names, manifest, robes.fresh_active_models())
        # Install only verified staged bytes. Repacking never writes wearable
        # roots, attachments, authoring projects, registration or phenotype tables.
        for name in sorted(output_names):
            destination = robes.ROOT / robes.model_directory(name) / f"{name}.mdl"
            source = sources[name]
            if robes.file_digest(source) != updated["files"][destination.relative_to(robes.ROOT).as_posix()]:
                raise ValueError(f"Staged animation bank changed: {name}")
        for name in sorted(output_names):
            destination = robes.ROOT / robes.model_directory(name) / f"{name}.mdl"
            source = sources[name]
            if not destination.exists() or robes.file_digest(destination) != robes.file_digest(source):
                shutil.copyfile(source, destination)
        robes.MANIFEST.write_text(json.dumps(updated, indent=2) + "\n")
    print(f"{'Installed' if args.apply else 'Staged'} {len(output_names)} compiled banks. Report: {stage / 'report.json'}")


if __name__ == "__main__":
    main()
