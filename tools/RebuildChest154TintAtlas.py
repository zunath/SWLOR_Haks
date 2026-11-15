#!/usr/bin/env python3
"""Bit-exact rebuild of the approved 512px chest154-157 BC5 tint atlas.

The shade-source PGM and mip source capture the *approved* BC5 shading at
integer sample precision, not the unavailable historical editor layers.
The categorical dye channel comes from the original unedited PLT. The
repository's existing tint encoder rebuilds ATI2/BC5 and its mip chain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import GenerateTintMapAssets as tint


ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).resolve().parent / "chest154_atlas"
ORIGINAL_PLT = ASSETS / "original_chest154.plt"
APPROVED_PGM = ASSETS / "approved_shades_512.pgm"
APPROVED_MIPS = ASSETS / "approved_mip_shades.u8"
RECIPE = ASSETS / "recipe.json"
EXPECTED_MATERIALS = {
    f"p{sex}h0_chest{number}"
    for sex in ("f", "m")
    for number in range(154, 158)
}
PGM_HEADER = b"P5\n512 512\n255\n"


def git_blob_sha1(content: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    ).hexdigest()


def rebuild() -> tuple[bytes, dict]:
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    width, height, original_shades, original_layers, source_hash = tint.read_plt(ORIGINAL_PLT)
    if (width, height) != (512, 512):
        raise ValueError("The original chest154 PLT must be exactly 512x512")
    if source_hash != recipe["basePLT"]["sha256"]:
        raise ValueError("Archived original PLT does not match recorded SHA-256")

    pgm = APPROVED_PGM.read_bytes()
    if not pgm.startswith(PGM_HEADER) or len(pgm) != len(PGM_HEADER) + width * height:
        raise ValueError("Approved shade PGM must be binary P5, 512x512, 8-bit")
    base_shades = np.frombuffer(pgm, dtype=np.uint8, offset=len(PGM_HEADER)).reshape(height, width)
    if np.array_equal(original_shades, base_shades):
        raise ValueError("Approved shade source contains no edits relative to the original PLT")

    remaining = APPROVED_MIPS.read_bytes()
    mip_sizes = tint.mip_sizes(width, height)
    if len(remaining) != sum(w * h for w, h in mip_sizes[1:]):
        raise ValueError("Approved mip source has an unexpected number of shade samples")

    layers = original_layers
    position = 0
    levels = []
    for mip, (w, h) in enumerate(mip_sizes):
        if mip == 0:
            shades = base_shades
        else:
            count = w * h
            shades = np.frombuffer(
                remaining, dtype=np.uint8, count=count, offset=position
            ).reshape(h, w)
            position += count
        if layers.shape != shades.shape:
            raise ValueError(f"Unexpected layer/shade dimensions at mip {mip}")
        # Use the existing production encoder; lower-mip category samples are
        # included in the source PLT but are not read by the runtime shader.
        data = (tint.packed_level(shades, layers) if mip == 0
                else tint.packed_lower_level(shades, layers))
        levels.append(data)
        if mip + 1 < len(mip_sizes):
            next_width, next_height = mip_sizes[mip + 1]
            layers = layers[::2, ::2][:next_height, :next_width]

    payload = b"".join(levels)
    rebuilt = (
        tint.dds_header(width, height, tint.bc5_level_length(width, height), len(mip_sizes))
        + payload
    )
    if git_blob_sha1(rebuilt) != recipe["outputDDS"]["gitBlobSha1"]:
        raise ValueError(
            "Rebuilt DDS differs from the approved 512px artifact. "
            "The shade sources or encoder changed."
        )
    return rebuilt, recipe


def validate_catalog(recipe: dict) -> None:
    manifest = ROOT / "tools" / "TintMapSources.json"
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    atlas_path = recipe["outputDDS"]["path"]
    affected = [x for x in entries if x.get("output") == atlas_path]
    materials = {x.get("material") for x in affected}
    if materials != EXPECTED_MATERIALS or len(affected) != len(EXPECTED_MATERIALS):
        raise ValueError("TintMapSources.json must map all eight chest154-157 materials to the atlas")
    for entry in affected:
        if (entry.get("sourceSha256") != recipe["basePLT"]["sha256"]
                or (entry.get("width"), entry.get("height")) != (512, 512)
                or entry.get("texture") != recipe["outputDDS"]["texture"]):
            raise ValueError(f"Unexpected atlas provenance for {entry.get('material')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="regenerate in memory and compare with shipped atlas")
    group.add_argument("--apply", action="store_true", help="regenerate only the approved 512px DDS from recorded inputs")
    args = parser.parse_args()
    rebuilt, recipe = rebuild()
    validate_catalog(recipe)
    target = ROOT / recipe["outputDDS"]["path"]
    txi = target.with_suffix(".txi")
    if args.apply:
        target.write_bytes(rebuilt)
        txi.write_text("mipmap 1\n", encoding="ascii", newline="\n")
        print(f"Rebuilt {target.relative_to(ROOT)} from source PLT and approved shade edits")
    else:
        if not target.is_file() or target.read_bytes() != rebuilt:
            raise SystemExit("FAIL: committed DDS differs from reproducible approved shade sources")
        if not txi.is_file() or txi.read_bytes() != b"mipmap 1\n":
            raise SystemExit("FAIL: chest atlas TXI must be 'mipmap 1'")
        print("PASS: approved chest154-157 512px BC5 DDS reproduced byte-for-byte (10 mips)")


if __name__ == "__main__":
    main()
