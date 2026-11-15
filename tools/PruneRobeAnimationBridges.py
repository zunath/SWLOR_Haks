#!/usr/bin/env python3
"""Remove verified, unreachable generated bridges before packaging animation updates.

The generator revises a family's installed bank in place when it rebuilds every
model that inherits from it, so this mostly finds banks left behind by families
that received a new name. Retired names stay reserved and are never reassigned.
Roots for retired phenotypes count as references too.
"""
import argparse
import json
from pathlib import Path
import subprocess

import GenerateRobeRgbModels as robes


def referenced_bridges(parents, candidates):
    """Keep all transitive parents of every non-candidate model, across HAK layers."""
    reachable = set()
    pending = [parent for name, values in parents.items() if name not in candidates for parent in values]
    while pending:
        name = pending.pop()
        if not name or name in reachable:
            continue
        reachable.add(name)
        pending.extend(parents.get(name, ()))
    return candidates & reachable


model_parent = robes.model_parent


def plan(root, manifest):
    root = root.resolve()
    directories = {(root / hak["Path"]).resolve()
                   for hak in json.loads((root / "hakbuilder.json").read_text())["HakList"]}
    if not directories or any(not path.is_relative_to(root) or not path.is_dir() for path in directories):
        raise ValueError("Every configured HAK directory must exist inside the repository")
    # A sparse checkout cannot prove that a bridge has no references.
    tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "--", "*.mdl"])
    for relative in tracked.decode().split("\0"):
        if relative:
            path = root / relative
            if path.parent.resolve() in directories and not path.is_file():
                raise ValueError(f"Materialize all tracked HAK models before pruning: {relative}")
    candidates = {}
    for name in robes.banks.bank_names(manifest):
        if not robes.banks.is_bank_name(name):
            raise ValueError(f"Invalid reserved bridge resref: {name}")
        directory = robes.model_directory(name)
        relative = f"{directory}/{name}.mdl"
        path = root / relative
        if relative not in manifest["files"]:
            if path.exists():
                raise ValueError(f"Unverified bridge occupies a reserved resref: {relative}")
            continue
        if path.resolve().parent != (root / directory).resolve() or not path.is_file():
            raise ValueError(f"Missing or redirected generated bridge: {relative}")
        if robes.file_digest(path) != manifest["files"][relative]:
            raise ValueError(f"Generated bridge changed since validation: {relative}")
        candidates[name] = path
    parents = {}
    for directory in sorted(directories):
        for path in directory.glob("*.mdl"):
            # Preserve references even in lower-priority layers and retired body roots.
            parents.setdefault(path.stem.lower(), set()).add(model_parent(path))
    keep = referenced_bridges(parents, set(candidates))
    for record in manifest.get("animation_bank_sets", {}).values():
        # Preserve a whole audited family if another model directly references a
        # child bank; partial removal would invalidate its reconstruction proof.
        parts = set(record["parts"])
        if parts & keep:
            keep.update(parts & candidates.keys())
    return [candidates[name] for name in sorted(candidates.keys() - keep)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete verified orphan files and update the manifest")
    args = parser.parse_args()
    manifest = json.loads(robes.MANIFEST.read_text())
    paths = plan(robes.ROOT, manifest)
    print(f"Unreachable bridges: {len(paths)}; bytes: {sum(path.stat().st_size for path in paths)}", flush=True)
    if args.apply:
        for path in paths:
            # plan verifies each absolute path, ownership hash, and complete reference graph.
            path.unlink()
            del manifest["files"][path.relative_to(robes.ROOT).as_posix()]
        # The head resref remains reserved in animation_bridges. Drop a bank set
        # only when its entire unreachable chain was removed together.
        removed = {path.stem for path in paths}
        for head, record in list(manifest.get("animation_bank_sets", {}).items()):
            if set(record["parts"]) <= removed:
                del manifest["animation_bank_sets"][head]
        robes.MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    elif paths:
        raise SystemExit("Run with --apply before packaging the HAKs")


if __name__ == "__main__":
    main()
