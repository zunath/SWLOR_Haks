#!/usr/bin/env python3
"""Import selectable stock robes and their PLTs before generating RGB assets.

HAK overrides win. Models and palettes already converted are never replaced.
The recorded stock inventory lets the robe audit catch holes that a scan of
HAK files alone cannot see. Run with --game-data <NWN>/data, then --apply.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import GenerateTintMapAssets as tint

MANIFEST = Path(__file__).with_name("TintMapStockRobes.json")
PATTERN = re.compile(r"^p[fm][a-z]0_robe(\d{3})$")


def selectable_styles():
    lines = (tint.REPOSITORY_ROOT / "sw_2da/parts_robe.2da").read_text().splitlines()
    ac_column = lines[2].split().index("ACBONUS") + 1
    return {int(columns[0]) for line in lines[3:] if (columns := line.split())
            and len(columns) > ac_column and columns[ac_column] != "****"}


def plan(stock_models, stock_palettes, active_models, converted, native_palettes, styles):
    rows = []
    for name in sorted(stock_models):
        match = PATTERN.fullmatch(name)
        if not match or int(match[1]) not in styles:
            continue
        palette = next((candidate for candidate in tint.modular_palette_candidates(name)
                        if candidate in converted or candidate in native_palettes or candidate in stock_palettes), None)
        if palette is None:
            raise ValueError(f"Selectable stock robe {name} has no resolved palette")
        rows.append({"model": name, "palette": palette, "importModel": name not in active_models,
                     "importPalette": palette not in converted and palette not in native_palettes})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    tint.require_repository_root()
    stock_models = tint.read_stock_key_models(args.game_data)
    stock_palettes = tint.read_stock_key_resources(args.game_data, 6)
    active = tint.find_active_models()
    entries = tint.load_source_manifest()
    native = {path.stem.lower() for directory in tint.hak_directories() for path in directory.glob("*.plt")
              if not tint.is_native_robe_control_plt(path)}
    rows = plan(stock_models, stock_palettes, active, entries, native, selectable_styles())
    model_names = {row["model"] for row in rows if row["importModel"]}
    palette_names = {row["palette"] for row in rows if row["importPalette"]}
    print(json.dumps({"selectableStockModels": len(rows), "importModels": sorted(model_names),
                      "importPalettes": sorted(palette_names)}, indent=2), flush=True)
    if not args.apply:
        return
    directory = tint.REPOSITORY_ROOT / "sw_pt_robe"
    # Read and validate every source before writing any of them.
    payloads = {directory / f"{name}.mdl": tint.extract_stock_bif_resource(*stock_models[name])
                for name in model_names}
    payloads.update({directory / f"{name}.plt": tint.extract_stock_bif_resource(*stock_palettes[name], 6)
                     for name in palette_names})
    for path, data in payloads.items():
        if path.exists():
            raise ValueError(f"Refusing to overwrite an existing HAK resource: {path}")
        if path.suffix == ".plt" and not data.startswith(tint.PLT_HEADER):
            raise ValueError(f"Invalid stock PLT: {path.name}")
    for path, data in payloads.items():
        path.write_bytes(data)
    tint._ACTIVE_MODELS = None
    tint._NATIVE_MODULAR_PALETTES = None
    selected_sources = {row["palette"] for row in rows}
    candidates = {**entries, **{name: {} for name in selected_sources}}
    ascii_models = [name for name, path in tint.find_active_models().items()
                    if (name in model_names or tint.modular_palette_source(name, candidates, tint.native_modular_palettes()) in palette_names)
                    and not path.read_bytes().startswith(b"\0\0\0\0")]
    if ascii_models:
        command = [sys.executable, "-B", str(Path(__file__).with_name("CompileModels.py")),
                   "--game-data", str(args.game_data), "--allow-missing-supermodels", "--apply"]
        for name in ascii_models:
            command.extend(["--model", name])
        subprocess.run(command, check=True)
    if any(name in tint.find_tint_material_plts()[0] for name in selected_sources):
        tint.generate_preserving_manifest()
    entries = tint.load_source_manifest()
    # Some imported race variants use a mask converted in an earlier run. Bind
    # these too; converting only newly extracted PLTs would omit those meshes.
    changed, _ = tint.synchronize_selected_model_material_aliases(entries, selected_sources)
    tint.write_source_manifest(entries, list(entries))
    tint.write_2da(entries)
    manifest = {"models": [{"model": row["model"], "palette": row["palette"]} for row in rows],
                "stockModelSha256": {name: hashlib.sha256(tint.extract_stock_bif_resource(*stock_models[name])).hexdigest()
                                     for name in sorted({row["model"] for row in rows})}}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Stock robe tint assets imported; bound {changed} additional models. Run GenerateRobeRgbModels.py next.")


if __name__ == "__main__":
    main()
