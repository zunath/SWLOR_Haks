#!/usr/bin/env python3
"""Restore equipment channels on female chest 209 without changing its shading.

The original male and female palettes share the same UV atlas. The female
palette incorrectly painted the straps and other equipment as hair. Only those
hair pixels are reassigned; existing female metal and tattoo pixels stay intact.
Run this script, then GenerateTintMapAssets.py --generate-preserving.
"""
import hashlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION = "de1e6b21f00^"
FEMALE_SHA = "79912d83a5bde8edb1439707664bc364c1b6de0173963bb650feec1b55e3c8af"
MALE_SHA = "f2f3c1cba4248841c7d11ed9de1a698a67fc3126abf6bf0486eec6c4a12fd991"


def repair_palette(female: bytes, male: bytes) -> bytes:
    if hashlib.sha256(female).hexdigest() != FEMALE_SHA or hashlib.sha256(male).hexdigest() != MALE_SHA:
        raise ValueError("Expected the original matching chest 209 UV atlases")
    result = bytearray(female)
    for offset in range(25, len(result), 2):
        if female[offset] == 1 and male[offset] in range(2, 8):
            result[offset] = male[offset]
    return bytes(result)


if __name__ == "__main__":
    palettes = [subprocess.check_output(
        ["git", "show", f"{SOURCE_REVISION}:sw_pt_chest/p{sex}e0_chest209.plt"], cwd=ROOT)
        for sex in "fm"]
    target = ROOT / "sw_pt_chest/pfe0_chest209.plt"
    repaired = repair_palette(*palettes)
    if target.exists() and target.read_bytes() not in (palettes[0], repaired):
        raise ValueError("Refusing to replace a different authored palette")
    target.write_bytes(repaired)
    print(f"Wrote {target}; shade bytes unchanged")
