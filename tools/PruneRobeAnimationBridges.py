#!/usr/bin/env python3
"""Remove verified, unreachable generated bridges before packaging animation updates.

Version-to-resref assignments remain reserved so an old name is never reassigned
to different motion. Roots for retired phenotypes count as references too.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

import CompileModels as mdl
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


def model_parent(path):
    with path.open("rb") as stream:
        header = stream.read(244)
        if mdl.binary(header):
            if len(header) < 244:
                raise ValueError(f"Truncated model header: {path}")
            return mdl.supermodel(header)
        stream.seek(0)
        for line in stream:
            if re.match(rb"\s*setsupermodel\s", line, re.IGNORECASE):
                return mdl.supermodel(line)
    return ""


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
    for name in set(manifest.get("animation_bridges", {}).values()):
        if not re.fullmatch(r"p[fm][a-z]_ra\d{3}", name):
            raise ValueError(f"Invalid reserved bridge resref: {name}")
        relative = f"sw_pt_root/{name}.mdl"
        path = root / relative
        if relative not in manifest["files"]:
            if path.exists():
                raise ValueError(f"Unverified bridge occupies a reserved resref: {relative}")
            continue
        if path.resolve().parent != (root / "sw_pt_root").resolve() or not path.is_file():
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
        robes.MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    elif paths:
        raise SystemExit("Run with --apply before packaging the HAKs")


if __name__ == "__main__":
    main()
