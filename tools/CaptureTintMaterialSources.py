#!/usr/bin/env python3
"""Capture authored MTR inputs before tint conversion, in module HAK order.

Run with the original HAK and module refs, plus the installed game's data path.
The resulting source data is independent of subsequently generated tint MTRs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

import GenerateTintMapAssets as tint


def capture(baseline: str, module_baseline: str, converted_baseline: str, game_data: Path) -> dict:
    root = tint.REPOSITORY_ROOT
    def git(repo, *args):
        return subprocess.check_output(["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), *args])
    commit = git(root, "rev-parse", baseline).decode().strip()
    module_commit = git(root.parent, "rev-parse", module_baseline).decode().strip()
    converted_commit = git(root, "rev-parse", converted_baseline).decode().strip()
    converted_sources = json.loads(git(root, "show", f"{converted_commit}:tools/TintMapSources.json"))
    bitmap_aliases = {str(alias): row["model"] for row in converted_sources for alias in row.get("aliases", [])}
    module = json.loads(git(root.parent, "show", f"{module_commit}:Module/ifo/module.ifo.json").decode("latin1"))
    priority = [row["Mod_Hak"]["value"].lower() for row in module["Mod_HakList"]["value"]]
    order = {name: index for index, name in enumerate(priority)}
    resources = {}
    for row in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not row:
            continue
        meta, name = row.split(b"\t", 1)
        path = Path(name.decode())
        if path.parts[0].lower() not in order or path.suffix.lower() not in {".mtr", ".dds", ".tga", ".plt"}:
            continue
        key = (path.stem.lower(), path.suffix.lower())
        candidate = (order[path.parts[0].lower()], path.as_posix(), meta.split()[2].decode())
        if key not in resources or candidate < resources[key]:
            resources[key] = candidate
    fixed = {name for name, suffix in resources if suffix in {".tga", ".dds"}}
    stock_keys = []
    for path in sorted(game_data.glob("*.key"), key=lambda item: item.name.lower()):
        data = path.read_bytes()
        if data[:4] != b"KEY ":
            raise ValueError(f"Invalid KEY: {path}")
        count, offset = struct.unpack_from("<I4xI", data, 12)
        if offset + count * 22 > len(data):
            raise ValueError(f"Truncated KEY: {path}")
        stock_keys.append({"name": path.name, "sha256": hashlib.sha256(data).hexdigest()})
        for index in range(count):
            at = offset + index * 22
            if struct.unpack_from("<H", data, at + 16)[0] in {3, 2033}:
                fixed.add(data[at:at + 16].split(b"\0", 1)[0].decode("ascii").lower())
    materials = {}
    process = subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for (name, suffix), (_, path, blob) in sorted(resources.items()):
            if suffix != ".mtr":
                continue
            process.stdin.write((blob + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().split()
            data = process.stdout.read(int(header[2]))
            assert process.stdout.read(1) == b"\n"
            lines = data.decode("utf-8-sig").splitlines()
            texture0 = next((line.split()[1].lower() for line in lines if line.strip().lower().startswith("texture0 ")), "")
            materials[name] = {
                "path": path, "gitBlob": blob, "sha256": hashlib.sha256(data).hexdigest(),
                "lines": lines, "resolvedTexture0": texture0 if texture0 in fixed else None,
            }
    finally:
        process.stdin.close()
        process.stdout.close()
        process.wait(timeout=10)
    # Early conversions wrote scoped names into bitmap as well as materialname.
    # Some aliases were later retired from tintmap.2da but remain in MDLs.
    # Recover only names whose exact deterministic alias digest proves lineage.
    current_bitmaps = {
        bitmap for path in tint.find_active_models().values()
        if path.parent.name.startswith("sw_pt_")
        for bitmap in tint.raw_model_bitmaps(path).values()
    }
    scopes = {f"part:{directory.removeprefix('sw_pt_')}" for directory in tint.STOCK_MODEL_PART_DIRECTORIES.values()}
    for source in materials:
        for scope in scopes:
            alias = tint.scoped_material_alias(source, scope)
            if alias in current_bitmaps:
                existing = bitmap_aliases.setdefault(alias, source)
                if existing != source:
                    raise ValueError(f"Ambiguous original bitmap alias: {alias}")
    return {
        "formatVersion": 1,
        "description": "Authored shared-material declarations before PLT conversion. Earlier module HAK entries win. Any explicit non-NULL texture0 suppresses the native local PLT, including missing rasters; resolvedTexture0 separately records verified DDS/TGA resource existence.",
        "hakCommit": commit, "moduleCommit": module_commit,
        "convertedCommit": converted_commit, "bitmapAliases": dict(sorted(bitmap_aliases.items())),
        "hakPriorityFirstWins": priority, "stockKeys": stock_keys, "materials": materials,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--module-baseline", required=True)
    parser.add_argument("--converted-baseline", required=True)
    parser.add_argument("--game-data", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = capture(args.baseline, args.module_baseline, args.converted_baseline, args.game_data)
    text = json.dumps(result, indent=2) + "\n"
    if args.check:
        if tint.MATERIAL_SOURCES.read_text(encoding="utf-8") != text:
            raise RuntimeError("Authored material source snapshot differs from its baseline resources")
    else:
        tint.MATERIAL_SOURCES.write_text(text, encoding="utf-8", newline="\n")
    print(f"Verified {len(result['materials'])} authored material source profiles.")


if __name__ == "__main__":
    main()
