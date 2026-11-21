"""Build the ProgFX coat atlas that tint materials sample for stoneskin-style effects.

ProgFX environment-map effects (progfx.2da Type 1: stoneskin, barkskin, Shadow
Shield and the like) make the client draw a legacy mesh with the effect's
vdu_tex_* texture in place of its diffuse. Tint-map MTR materials never receive
that swap, so every tint material binds this atlas as texture8 (texture4 on the
cloak) and the server writes the coat's cell number plus one to progFxCoat.

Cells are 256 texels in a 4 x 2 grid, in COAT_TEXTURES order, left to right and
then upward (cell 0 at UV 0,0). SWLOR.Game.Server's ProgFxCoat lists the same
order. Hak overrides win over stock textures, as they do in the game.

Run: python tools/ProgFxCoatAtlas.py --game-data ".../Neverwinter Nights/data"
     python tools/ProgFxCoatAtlas.py --check
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess

import numpy as np
from PIL import Image

import GenerateTintMapAssets as tint

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "sw_vfx" / f"{tint.PROGFX_COAT_TEXTURE}.tga"
ATLAS_TXI = ATLAS.with_suffix(".txi")
MANIFEST = ROOT / "tools" / "ProgFxCoatAtlas.json"
COAT_TEXTURES = ("vdu_tex_stone", "vdu_tex_grstone", "vdu_tex_bark", "vdu_tex_shade", "vdu_tex_ice")
CELL = 256
COLUMNS, ROWS = 4, 2
TGA_TYPE, DDS_TYPE = 3, 2033
# Match the coat sources: full resolution at every texture quality setting.
TXI_TEXT = "downsamplemax 0\ndownsamplemin 0\n"


def hak_override(resref: str) -> Path | None:
    # Git's index also covers sparse checkouts that omit unrelated hak folders.
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "sw_*/*"],
                             check=True, capture_output=True).stdout.decode().split("\0")
    matches = [ROOT / name for name in tracked
               if Path(name).stem.lower() == resref and Path(name).suffix.lower() in {".tga", ".dds"}]
    if len(matches) > 1:
        raise ValueError(f"{resref} has more than one hak texture: {matches}")
    return matches[0] if matches else None


def read_coat_source(resref: str, game_data: Path) -> tuple[Image.Image, str]:
    override = hak_override(resref)
    if override is not None:
        if override.suffix.lower() != ".tga":
            raise ValueError(f"{override} must be a TGA")
        return Image.open(override), override.relative_to(ROOT).as_posix()
    stock = tint.read_stock_key_resources(game_data, TGA_TYPE)
    if resref not in stock:
        raise ValueError(f"{resref} is not a stock TGA")
    path, index = stock[resref]
    data = tint.extract_stock_bif_resource(path, index, TGA_TYPE)
    return Image.open(io.BytesIO(data)), f"stock:{path.name}"


def cell_pixels(image: Image.Image) -> np.ndarray:
    """RGBA rows with row 0 at v = 0, the texture's bottom edge."""
    rgba = image.convert("RGBA").resize((CELL, CELL), Image.Resampling.LANCZOS)
    return np.flipud(np.asarray(rgba, dtype=np.uint8))


def tga_bytes(rows_from_bottom: np.ndarray) -> bytes:
    height, width, _ = rows_from_bottom.shape
    # Uncompressed 32-bit truecolor, bottom-left origin with 8 alpha bits.
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, width, height, 32, 0x08)
    bgra = rows_from_bottom[..., [2, 1, 0, 3]]
    return header + np.ascontiguousarray(bgra).tobytes()


def build(game_data: Path) -> tuple[bytes, list[dict[str, str]]]:
    atlas = np.zeros((ROWS * CELL, COLUMNS * CELL, 4), dtype=np.uint8)
    sources = []
    for cell, resref in enumerate(COAT_TEXTURES):
        image, origin = read_coat_source(resref, game_data)
        column, row = cell % COLUMNS, cell // COLUMNS
        atlas[row * CELL:(row + 1) * CELL, column * CELL:(column + 1) * CELL] = cell_pixels(image)
        sources.append({"texture": resref, "source": origin})
    return tga_bytes(atlas), sources


def generate(game_data: Path) -> None:
    data, sources = build(game_data)
    ATLAS.write_bytes(data)
    ATLAS_TXI.write_text(TXI_TEXT, newline="\n")
    MANIFEST.write_text(json.dumps({
        "cell": CELL,
        "columns": COLUMNS,
        "rows": ROWS,
        "cells": sources,
        "sha256": hashlib.sha256(data).hexdigest(),
    }, indent=2) + "\n", newline="\n")
    print(f"Wrote {ATLAS.relative_to(ROOT).as_posix()} with {len(sources)} coat cells.")


def check() -> list[str]:
    errors = []
    manifest = json.loads(MANIFEST.read_text())
    if [cell["texture"] for cell in manifest["cells"]] != list(COAT_TEXTURES):
        errors.append("ProgFxCoatAtlas.json cell order differs from COAT_TEXTURES; rerun with --game-data")
    if (manifest["cell"], manifest["columns"], manifest["rows"]) != (CELL, COLUMNS, ROWS):
        errors.append("ProgFxCoatAtlas.json layout differs from the shader's 4 x 2 grid of 256 texels")
    if len(COAT_TEXTURES) > COLUMNS * ROWS:
        errors.append("More coat textures than atlas cells")
    if not ATLAS.is_file():
        errors.append(f"Missing {ATLAS.relative_to(ROOT).as_posix()}")
    elif hashlib.sha256(ATLAS.read_bytes()).hexdigest() != manifest["sha256"]:
        errors.append(f"{ATLAS.name} differs from ProgFxCoatAtlas.json; rerun with --game-data")
    if not ATLAS_TXI.is_file() or ATLAS_TXI.read_text().replace("\r\n", "\n") != TXI_TEXT:
        errors.append(f"{ATLAS_TXI.name} must keep the atlas at full resolution")
    for cell in manifest["cells"]:
        override = hak_override(cell["texture"])
        expected = override.relative_to(ROOT).as_posix() if override else None
        recorded = None if cell["source"].startswith("stock:") else cell["source"]
        if expected != recorded:
            errors.append(f"{cell['texture']} source changed ({recorded} -> {expected}); rerun with --game-data")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--game-data", type=Path, help="NWN data directory containing nwn_base.key")
    action.add_argument("--check", action="store_true", help="validate the checked-in atlas")
    args = parser.parse_args()
    if args.game_data:
        generate(args.game_data)
        return
    errors = check()
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(1)
    print("ProgFX coat atlas is current.")


if __name__ == "__main__":
    main()
