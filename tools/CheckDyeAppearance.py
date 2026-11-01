#!/usr/bin/env python3
"""Keep existing dye colors stable and dyed metal on the palette.

Every tint material (each sw_tint_mtr material and any other MTR that samples
the dye palette) is fingerprinted from its directives and the exact content of
every texture (and TXI) it binds, tint masks included. Every custom shader those
materials use, the palette atlas, and each model's rows in tintmap.2da and
cloaktint.2da (which material and dye layers it uses) are recorded as well.
DyeAppearanceBaseline.json records the approved state, so any change to how an
existing part takes dyes fails until the baseline is updated on purpose, with
before/after client captures in the pull request (SWLOR.Game.Server/Readmes/TintMapRendering.md).

Dyed metal takes its reflectance from the palette. A bound specular (texture2) or
roughness (texture3) map replaces it and turns dyed metal into a pale,
nonmetallic sheen, so materials whose mask has Metal 1 or Metal 2 must bind
neither. Materials that already did when the baseline was created are listed in
legacyMetalSpecular; nothing can be added to that list.

Run: python -B tools/CheckDyeAppearance.py            (check, used by BuildHaks.cmd)
     python -B tools/CheckDyeAppearance.py --update   (approve the current state)
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("DyeAppearanceBaseline.json")
SOURCES = Path(__file__).with_name("TintMapSources.json")
TINT_SHADERS = frozenset({"fs_plt_tinter", "fs_plt_tinter_nm", "fs_plt_hair_nm", "fs_cloaktint"})
# Every custom shader a tint material uses is a shared input as well (see current_state).
SHARED_INPUTS = (
    "sw_shader/fs_plt_tinter.shd",
    "sw_shader/fs_plt_tinter_nm.shd",
    "sw_shader/fs_plt_hair_nm.shd",
    "sw_shader/fs_cloaktint.shd",
    "sw_item/plt_palette.tga",
    "sw_item/plt_palette.txi",
    "sw_item/plt_white.tga",
)
# Each model's material and dye-layer rows; changing one recolors that model.
CATALOGS = ("sw_2da/tintmap.2da", "sw_2da/cloaktint.2da")
# A texture's TXI changes how it is sampled (tint masks rely on "mipmap 0").
TEXTURE_EXTENSIONS = (".dds", ".tga", ".txi")
# The shared placeholder and palette are covered by SHARED_INPUTS. Tint masks
# (tm_*) are hashed like any other texture: their names do not guarantee content.
UNHASHED_TEXTURES = re.compile(r"^(null|plt_white|plt_palette)$")
METAL_LAYERS = {2, 3}


def git(*arguments: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *arguments], input=data,
                          capture_output=True, check=True).stdout


def file_blobs() -> dict[str, str]:
    """Blob id of every tracked or untracked hak file, as git would store it."""
    blobs = {}
    for record in git("ls-files", "-s", "-z").split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            blobs[path.decode()] = meta.split()[1].decode()
    changed = [path.decode() for path in git(
        "ls-files", "-m", "-o", "--exclude-standard", "-z").split(b"\0") if path]
    present = [path for path in changed if (ROOT / path).is_file()]
    for path in set(changed) - set(present):
        blobs.pop(path, None)
    if present:
        hashed = git("hash-object", "--stdin-paths", data="\n".join(present).encode()).decode().split()
        blobs.update(zip(present, hashed))
    return blobs


def texture_blobs(blobs: dict[str, str]) -> dict[str, list[str]]:
    textures: dict[str, set[str]] = {}
    for path, blob in blobs.items():
        if path.startswith("sw_") and path.lower().endswith(TEXTURE_EXTENSIONS):
            textures.setdefault(Path(path).stem.lower(), set()).add(blob)
    return {name: sorted(values) for name, values in textures.items()}


def directives(text: str) -> list[str]:
    lines = (" ".join(line.split("//", 1)[0].split()).lower() for line in text.splitlines())
    return sorted(line for line in lines if line)


def bindings(lines: list[str]) -> dict[str, str]:
    return {parts[0]: parts[1] for parts in (line.split() for line in lines)
            if len(parts) == 2 and re.fullmatch(r"texture\d+", parts[0])}


def mtr_texts(blobs: dict[str, str]) -> dict[str, str]:
    """Every MTR in the haks, from the working tree or, for folders a sparse checkout omits, from git."""
    texts, unread = {}, []
    for path in sorted(path for path in blobs if path.lower().endswith(".mtr")):
        if (ROOT / path).is_file():
            texts[path] = (ROOT / path).read_bytes()
        else:
            unread.append(path)
    if unread:
        output = git("cat-file", "--batch", data="".join(f"{blobs[path]}\n" for path in unread).encode())
        offset = 0
        for path in unread:
            header_end = output.index(b"\n", offset)
            size = int(output[offset:header_end].split()[2])
            texts[path] = output[header_end + 1:header_end + 1 + size]
            offset = header_end + 1 + size + 1
    return {path: data.decode("utf-8-sig", errors="replace") for path, data in texts.items()}


def mask_layers() -> dict[str, set[int]]:
    layers: dict[str, set[int]] = {}
    for entry in json.loads(SOURCES.read_text(encoding="utf-8")):
        layers.setdefault(str(entry["texture"]).lower(), set()).update(int(value) for value in entry["layers"])
    return layers


def catalog_rows() -> dict[str, dict[str, str]]:
    catalogs = {}
    for relative in CATALOGS:
        lines = (ROOT / relative).read_text(encoding="utf-8", errors="replace").splitlines()
        header = lines[2].split()
        rows: dict[str, list[str]] = {}
        for line in lines[3:]:
            values = dict(zip(header, line.split()[1:]))
            model = values.get("MODEL", "****").lower()
            if model != "****":
                rows.setdefault(model, []).append(" ".join(
                    values.get(column, "****").lower() for column in header if column != "MODEL"))
        catalogs[Path(relative).stem] = {model: "; ".join(sorted(entries)) for model, entries in rows.items()}
    return catalogs


def current_state() -> tuple[dict[str, str], dict[str, str], set[str], dict[str, dict[str, str]]]:
    blobs = file_blobs()
    textures = texture_blobs(blobs)
    layers = mask_layers()
    shared = {path: blobs.get(path, "missing") for path in SHARED_INPUTS}
    shader_files = {Path(path).stem.lower(): path for path in blobs
                    if path.startswith("sw_shader/") and path.lower().endswith(".shd")}
    materials, metal_specular = {}, set()
    for relative, text in mtr_texts(blobs).items():
        lines = directives(text)
        bound = bindings(lines)
        shaders = {line.split()[1] for line in lines if line.startswith(("customshaderfs ", "customshadervs "))}
        # A tint material samples the dye palette; most also use a tint shader.
        if "plt_palette" not in bound.values() and not shaders & TINT_SHADERS:
            continue
        for shader in sorted(shaders & set(shader_files)):
            shared[shader_files[shader]] = blobs[shader_files[shader]]
        folder, name = Path(relative).parent.as_posix().lower(), Path(relative).stem.lower()
        if folder != "sw_tint_mtr":
            name = f"{folder}/{name}"
        record = {
            "directives": lines,
            "textures": {texture: textures.get(texture, ["missing"])
                         for texture in sorted(set(bound.values())) if not UNHASHED_TEXTURES.match(texture)},
        }
        materials[name] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]
        if (layers.get(bound.get("texture7", ""), set()) & METAL_LAYERS
                and any(bound.get(unit, "null") != "null" for unit in ("texture2", "texture3"))):
            metal_specular.add(name)
    return shared, materials, metal_specular, catalog_rows()


def check(baseline: dict, shared: dict[str, str], materials: dict[str, str], metal_specular: set[str],
          catalogs: dict[str, dict[str, str]]) -> list[str]:
    errors = []
    for path, blob in shared.items():
        if baseline["shared"].get(path) != blob:
            errors.append(f"{path}: shared tint input changed; every dyed part may change color")
    for name, digest in sorted(baseline["materials"].items()):
        if name not in materials:
            errors.append(f"{name}: approved tint material was removed")
        elif materials[name] != digest:
            errors.append(f"{name}: dye appearance inputs changed (MTR directives or a bound texture)")
    for catalog, approved in sorted(baseline.get("catalogs", {}).items()):
        rows = catalogs.get(catalog, {})
        for model, value in sorted(approved.items()):
            if rows.get(model) != value:
                errors.append(f"{catalog}.2da {model}: material or dye layers changed "
                              f"(approved '{value}', now '{rows.get(model, 'removed')}')")
    legacy = set(baseline["legacyMetalSpecular"])
    for name in sorted(metal_specular - legacy):
        errors.append(f"{name}: binds a specular/roughness map over dyed metal; set texture2/texture3 null "
                      "(add its maps to PALETTE_SPECULAR_MAPS in GenerateTintMapAssets.py)")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true",
                        help="approve the current dye inputs (requires before/after client captures)")
    arguments = parser.parse_args()
    shared, materials, metal_specular, catalogs = current_state()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if arguments.update:
        violations = [error for error in check(baseline, shared, materials, metal_specular, catalogs)
                      if "over dyed metal" in error]
        if violations:
            print("\n".join(violations))
            print("Dyed metal rule violations cannot be approved.")
            return 1
        baseline["shared"] = shared
        baseline["materials"] = materials
        baseline["catalogs"] = catalogs
        baseline["legacyMetalSpecular"] = sorted(set(baseline["legacyMetalSpecular"]) & metal_specular)
        BASELINE.write_text(json.dumps(baseline, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Approved dye inputs for {len(materials)} tint materials.")
        return 0
    errors = check(baseline, shared, materials, metal_specular, catalogs)
    if errors:
        print("\n".join(errors))
        print(f"{len(errors)} dye appearance change(s). Existing dye colors must not change silently: "
              "fix the change, or capture before/after client screenshots with the same dyes, get the "
              "owner's approval, and run tools/CheckDyeAppearance.py --update (TintMapRendering.md).")
        return 1
    print(f"Dye appearance unchanged for {len(materials)} tint materials; dyed metal rule holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
