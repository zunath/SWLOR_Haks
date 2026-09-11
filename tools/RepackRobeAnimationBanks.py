#!/usr/bin/env python3
"""Split existing compiled robe bridges without rebuilding wearable models.

All source/output ownership checks, exact binary reconstruction and native
decompilation complete in staging before --apply replaces any HAK resource.
"""
import argparse
from contextlib import contextmanager
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import CompileModels as mdl
import GenerateRobeRgbModels as robes
import RobeAnimationBanks as banks
import RobeAnimations as animations
import RobePoseAudit as poses


INSTALL_DIRECTORY = "nwsync-bank-install"


def reject_install_stage(stage):
    """Reject reserved paths before recovery can remove any existing staged audit."""
    stage = stage.resolve()
    output = (robes.ROOT / "output").resolve()
    transaction = output / INSTALL_DIRECTORY
    if stage.is_relative_to(transaction) or stage.is_relative_to(transaction.with_suffix(".lock")):
        raise ValueError("Staging directory overlaps a reserved animation install path")


def validate_stage(stage):
    """Check general staging constraints only after a pending install is recovered."""
    stage = stage.resolve()
    if not stage.is_relative_to((robes.ROOT / "output").resolve()):
        raise ValueError("Use a staging directory inside this repository's output folder")
    reject_install_stage(stage)
    return stage


def preflight(manifest):
    # Only these packaging modules may change without regenerating wearables.
    # The generator also authors poses and skins, so its entire fingerprint
    # must still match even when an edit appears to affect packaging alone.
    packaging = {"tools/RobeAnimationBanks.py", "tools/RepackRobeAnimationBanks.py"}
    if "tools/GenerateRobeRgbModels.py" not in manifest["files"]:
        raise ValueError("Missing validated generator fingerprint; regenerate the robe catalog")
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


def _install_directory(root):
    root = root.resolve()
    output = root / "output"
    if output.resolve() != output or (output.exists() and not output.is_dir()):
        raise ValueError("Animation install output directory is redirected")
    output.mkdir(exist_ok=True)
    directory = output / INSTALL_DIRECTORY
    if directory.resolve() != directory:
        raise ValueError("Animation install transaction directory is redirected")
    return directory


@contextmanager
def _install_lock(root):
    """OS locks are released on termination, unlike a persistent PID lock file."""
    directory = _install_directory(root)
    path = directory.with_suffix(".lock")
    if path.resolve() != path:
        raise ValueError("Animation install lock is redirected")
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another animation bank install or recovery is running") from error
        try:
            yield directory
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _install_destination(root, relative):
    path = root / relative
    if relative != "tools/RobeRgbModels.json":
        name = path.stem
        if (not banks.is_bank_name(name) or
                relative != f"{robes.model_directory(name)}/{name}.mdl"):
            raise ValueError(f"Unexpected animation install destination: {relative}")
    if (path.parent.resolve().parent != root.resolve() or
            path.resolve() != path or not path.parent.is_dir()):
        raise ValueError(f"Missing or redirected animation install destination: {relative}")
    return path


def _write_durable(path, data):
    with path.open("wb") as output:
        if output.write(data) != len(data):
            raise OSError(f"Incomplete animation transaction write: {path}")
        output.flush()
        os.fsync(output.fileno())


def _write_install_journal(directory, journal):
    pending = directory / "journal.next"
    _write_durable(pending, (json.dumps(journal, indent=2) + "\n").encode())
    os.replace(pending, directory / "journal.json")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _recover_install(root, directory):
    """Restore verified backups by rename, without allocating another model copy."""
    if not directory.exists():
        return
    journal_path = directory / "journal.json"
    if not journal_path.exists():
        # All preparation completes before this journal exists or live files move.
        shutil.rmtree(directory)
        return
    journal = json.loads(journal_path.read_text())
    if journal.get("version") != 1 or journal.get("state") not in {"installing", "committed"}:
        raise ValueError("Unrecognized animation install recovery journal")
    entries = journal.get("entries")
    if (not isinstance(entries, list) or not entries or
            entries[-1].get("path") != "tools/RobeRgbModels.json"):
        raise ValueError("Incomplete animation install recovery journal")
    seen = set()
    for index, entry in enumerate(entries):
        destination = _install_destination(root, entry["path"])
        if entry["path"] in seen:
            raise ValueError("Duplicate animation install destination")
        seen.add(entry["path"])
        for value in (entry["before"], entry["after"]):
            if value is not None and (not isinstance(value, str) or len(value) != 64 or
                                      any(char not in "0123456789abcdef" for char in value)):
                raise ValueError("Malformed animation install checksum")
        if entry["after"] is None:
            raise ValueError("Missing animation install output checksum")
        if journal["state"] == "installing":
            current = _sha(destination)
            # An earlier rollback may have already consumed this backup. Its
            # absence is safe only when the live file is the exact original.
            if (entry["before"] is not None and current != entry["before"] and
                    _sha(directory / f"old-{index}") != entry["before"]):
                raise ValueError(f"Animation install backup changed: {entry['path']}")
            if current not in {None, entry["before"], entry["after"]}:
                raise ValueError(f"Animation install destination changed externally: {entry['path']}")
    if journal["state"] == "installing":
        for index, entry in enumerate(entries):
            destination = _install_destination(root, entry["path"])
            if _sha(destination) == entry["before"]:
                continue
            if entry["before"] is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(directory / f"old-{index}", destination)
        # No new journal write is necessary: checksums identify restored files
        # if termination interrupts rollback or cleanup, even with a full disk.
        print("Recovered the previous animation banks and manifest.", flush=True)
    shutil.rmtree(directory)


def recover_bank_install():
    """Run before reading the manifest or doing provenance checks."""
    root = robes.ROOT.resolve()
    with _install_lock(root) as directory:
        _recover_install(root, directory)


def install_banks(sources, updated, initial):
    """Prepare everything, install each resource atomically, then commit its manifest."""
    root = robes.ROOT.resolve()
    prior = json.loads(initial)
    with _install_lock(root) as directory:
        if any(path.resolve().is_relative_to(directory) for path in sources.values()):
            raise ValueError("Staged sources overlap the reserved animation install directory")
        _recover_install(root, directory)
        manifest_path = _install_destination(root, "tools/RobeRgbModels.json")
        if manifest_path.read_bytes() != initial:
            raise ValueError("Manifest changed before animation bank installation")
        directory.mkdir()
        entries = []
        try:
            for name in sorted(sources):
                relative = f"{robes.model_directory(name)}/{name}.mdl"
                destination = _install_destination(root, relative)
                data = sources[name].read_bytes()
                after = hashlib.sha256(data).hexdigest()
                if len(data) >= banks.LIMIT_BYTES or after != updated["files"].get(relative):
                    raise ValueError(f"Staged animation bank changed: {name}")
                old = destination.read_bytes() if destination.exists() else None
                before = hashlib.sha256(old).hexdigest() if old is not None else None
                if before != prior["files"].get(relative):
                    raise ValueError(f"Animation bank changed before installation: {relative}")
                if before == after:
                    continue
                index = len(entries)
                _write_durable(directory / f"new-{index}", data)
                if old is not None:
                    _write_durable(directory / f"old-{index}", old)
                entries.append({"path": relative, "before": before, "after": after})
            index = len(entries)
            data = (json.dumps(updated, indent=2) + "\n").encode()
            _write_durable(directory / f"new-{index}", data)
            _write_durable(directory / f"old-{index}", initial)
            entries.append({"path": "tools/RobeRgbModels.json", "before": hashlib.sha256(initial).hexdigest(),
                            "after": hashlib.sha256(data).hexdigest()})
            journal = {"version": 1, "state": "installing", "entries": entries}
            _write_install_journal(directory, journal)
            # The complete journal and backups are durable before the first move.
            # Check all destinations again before changing any, then commit the
            # manifest last so it never advertises a partly installed bank set.
            for index, entry in enumerate(entries):
                if (_sha(directory / f"new-{index}") != entry["after"] or
                        entry["before"] is not None and _sha(directory / f"old-{index}") != entry["before"]):
                    raise ValueError(f"Prepared animation install bytes changed: {entry['path']}")
                if _sha(_install_destination(root, entry["path"])) != entry["before"]:
                    raise ValueError(f"Animation install destination changed: {entry['path']}")
            for index, entry in enumerate(entries):
                os.replace(directory / f"new-{index}", _install_destination(root, entry["path"]))
            journal["state"] = "committed"
            _write_install_journal(directory, journal)
        except Exception:
            _recover_install(root, directory)
            raise
        shutil.rmtree(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stage", type=Path, help="Keep validated binaries and report in this staging directory")
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 5),
                        help="Bounded parallel validation workers (default: 2)")
    args = parser.parse_args()
    stage = (args.stage or robes.ROOT / "output" / f"nwsync-banks-{time.time_ns()}").resolve()
    reject_install_stage(stage)
    recover_bank_install()
    stage = validate_stage(stage)
    initial = robes.MANIFEST.read_bytes()
    manifest = json.loads(initial)
    packaging = preflight(manifest)
    active = robes.fresh_active_models()
    errors = robes.animation_bank_errors(manifest, active)
    if errors:
        raise ValueError("\n".join(errors))
    heads = [name for name in sorted(set(manifest["animation_bridges"].values()))
             if f"{robes.model_directory(name)}/{name}.mdl" in manifest["files"]]
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
        install_banks(sources, updated, initial)
    print(f"{'Installed' if args.apply else 'Staged'} {len(output_names)} compiled banks. Report: {stage / 'report.json'}")


if __name__ == "__main__":
    main()
