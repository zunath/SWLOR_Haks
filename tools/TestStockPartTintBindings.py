"""Guard stock body parts against borrowed tint maps.

A stock modular part keeps NWN's native PLT lookup only while its mesh bitmaps name no
generated tint material. Many stock parts name another part's texture (female chest 4
names pfh0_chest001). Once that name became a tint MTR, its non-null texture0 won over
the native PLT and the part drew the other part's mask on its own UVs. Every such part
must be imported and bound to the palette the native lookup selects.

Run: python -B tools/TestStockPartTintBindings.py --game-data "<NWN>/data"
"""
import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

import GenerateTintMapAssets as tint

ARMOR_PARTS = {
    "belt", "bicepl", "bicepr", "chest", "footl", "footr", "forel", "forer", "handl", "handr",
    "legl", "legr", "neck", "pelvis", "robe", "shinl", "shinr", "shol", "shor",
}
# Playable species render only these model families, on the normal phenotype
# (SWLOR.Game.Server/Readmes/ContentStandards.md, Characters).
PLAYABLE_FAMILIES = {"h", "e", "o", "d", "z"}
REPORTED = (
    "pfh0_chest004", "pfh0_chest008", "pfh0_bicepl006", "pfh0_bicepr006", "pfh0_forel008",
    "pfh0_shinl005", "pfh0_shinr005", "pfh0_belt005", "pfh0_pelvis030", "pfh0_pelvis033",
)


def supported(match) -> bool:
    return (match.group("race").lower() in PLAYABLE_FAMILIES and match.group("phenotype") == "0"
            and re.sub(r"[0-9]+$", "", match.group("part").lower()) in ARMOR_PARTS)


def tint_materials() -> set[str]:
    materials = set()
    for directory in tint.hak_directories():
        for path in directory.glob("*.mtr"):
            texture0 = next((line.split()[1].lower() for line in path.read_text(errors="replace").splitlines()
                             if re.match(r"^\s*texture0\s+\S+", line, re.IGNORECASE)), "null")
            if texture0 != "null":
                materials.add(path.stem.lower())
    return materials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True)
    args = parser.parse_args()
    entries = tint.load_source_manifest()
    natives = tint.native_modular_palettes()
    active = tint.find_active_models()
    stock_models = tint.read_stock_key_models(args.game_data)
    stock_palettes = tint.read_stock_key_resources(args.game_data, 6)
    materials = tint_materials()
    aliases = tint.build_alias_source_lookup(entries)

    def palette_hash(name):
        if name in entries:
            return entries[name]["sourceSha256"]
        return hashlib.sha256(tint.extract_stock_bif_resource(*stock_palettes[name], expected_type=6)).hexdigest()

    failures = []
    scratch_directory = tempfile.TemporaryDirectory()
    scratch = Path(scratch_directory.name)
    for model in sorted(stock_models):
        match = tint.MODULAR_MODEL_PATTERN.fullmatch(model)
        if not match or model in active:
            continue
        if not supported(match):
            continue
        palette = next((c for c in tint.modular_palette_candidates(model) if c in entries or c in natives), None)
        if palette is None or (palette not in entries and palette not in stock_palettes):
            continue
        source = scratch / f"{model}.mdl"
        source.write_bytes(tint.extract_stock_bif_resource(*stock_models[model]))
        bitmaps = set(tint.raw_model_bitmaps(source).values())
        expected = palette_hash(palette)
        borrowed = sorted(b for b in bitmaps if b in materials and b != palette
                          and entries.get(aliases.get(b, b), {}).get("sourceSha256") != expected)
        if borrowed:
            failures.append(f"{model}: native palette {palette}, but draws {borrowed}")

    scratch_directory.cleanup()

    # Imported stock parts must bind only the palette their native lookup selects.
    imported = 0
    for model, path in sorted(active.items()):
        match = tint.MODULAR_MODEL_PATTERN.fullmatch(model)
        if not match or model not in stock_models or not path.parent.name.lower().startswith("sw_pt_"):
            continue
        if not supported(match):
            continue
        palette = tint.modular_palette_source(model, entries, natives)
        # Without a subtree named after the model the native loader replaces nothing either.
        if palette is None or tint.find_native_modular_tint_references(path, entries) is None:
            continue
        imported += 1
        data = path.read_bytes()
        if data[:4] != bytes(4):
            failures.append(f"{model}: imported model is not compiled")
            continue
        bound = {aliases.get(material, material) for _, _, _, material in
                 tint.read_binary_model_material_fields(path, data) if material}
        wrong = sorted(source for source in bound if source in entries
                       and entries[source]["sourceSha256"] != entries[palette]["sourceSha256"])
        if wrong:
            failures.append(f"{model}: native palette {palette}, but binds {wrong}")

    # Negative controls: the reported female human parts are imported and bound to their own palettes.
    for model in REPORTED:
        assert model in active, f"{model} is not imported"
        bound = {aliases.get(material, material) for _, _, _, material in
                 tint.read_binary_model_material_fields(active[model], active[model].read_bytes()) if material}
        assert bound == {model}, f"{model} binds {sorted(bound)}"
    for model in ("pfh0_handl193", "pfh0_handr193", "pmh0_handl193", "pmh0_handr193"):
        assert entries[model]["layers"] == [0], f"{model} must follow Skin like hands 194-196"
    assert entries["pfh0_forer008"]["sourceSha256"] == entries["pfh0_forel008"]["sourceSha256"], \
        "forearm 8 sides must share the stock palette"

    if failures:
        raise SystemExit("Stock parts draw another part's tint map:\n" + "\n".join(failures[:40]) +
                         f"\n({len(failures)} total)")
    print(f"No stock body part borrows another part's tint map; {imported} imported stock parts bind their own palettes.")


if __name__ == "__main__":
    main()
