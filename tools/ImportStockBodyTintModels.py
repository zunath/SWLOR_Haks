#!/usr/bin/env python3
"""Import omitted stock base-body meshes for tint conversion, preserving native palette lookup."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import GenerateTintMapAssets as g

PARTS = {"chest": "chest", "pelvis": "pelvis", "bicepl": "lbicep", "bicepr": "rbicep",
         "forel": "lfore", "forer": "rfore", "handl": "lhand", "handr": "rhand",
         "legl": "lthigh", "legr": "rthigh", "shinl": "lshin", "shinr": "rshin",
         "footl": "lfoot", "footr": "rfoot"}
PATTERN = re.compile(r"p[fm][adegho][02]_(pelvis001|chest00[12]|(?:bicep[lr]|fore[lr]|hand[lr]|leg[lr]|shin[lr])00[12]|foot[lr]001)")
MANIFEST = Path(__file__).with_name("StockBodyTintModels.json")


def directory(model):
    part = re.sub(r"\d+$", "", model.split("_", 1)[1])
    return g.REPOSITORY_ROOT / ("sw_pt_" + PARTS[part])


def geometry_hash(model, data):
    normalized = bytearray(data)
    path = directory(model) / (model + ".mdl")
    for bitmap_offset, _, material_offset, _ in g.read_binary_model_material_fields(path, data):
        normalized[bitmap_offset:bitmap_offset + 64] = bytes(64)
        normalized[material_offset:material_offset + 64] = bytes(64)
    return hashlib.sha256(normalized).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    stock = g.read_stock_key_models(args.game_data)
    palettes = g.read_stock_key_resources(args.game_data, 6)
    active = g.find_active_models()
    entries = g.load_source_manifest()
    records = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    pending_models, pending_palettes = {}, {}
    for model in sorted(stock):
        if not PATTERN.fullmatch(model) or model in active:
            continue
        source = next((name for name in g.modular_palette_candidates(model)
                       if name in entries or name in palettes), None)
        if source is None:
            raise ValueError(f"No native palette for {model}")
        data = g.extract_stock_bif_resource(*stock[model])
        pending_models[directory(model) / (model + ".mdl")] = data
        records[model] = {"sourceSha256": hashlib.sha256(data).hexdigest(),
                          "geometrySha256": geometry_hash(model, data), "palette": source}
        if source not in entries:
            pending_palettes[directory(source) / (source + ".plt")] = g.extract_stock_bif_resource(*palettes[source], 6)
    print(f"Missing base models: {len(pending_models)}; stock palettes to convert: {len(pending_palettes)}")
    if args.apply:
        for path, data in {**pending_models, **pending_palettes}.items():
            if path.exists() and path.read_bytes() != data:
                raise ValueError(f"Refusing to overwrite {path}")
            path.write_bytes(data)
        MANIFEST.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
