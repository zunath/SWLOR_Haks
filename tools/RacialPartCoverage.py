#!/usr/bin/env python3
"""Find playable-body armor parts left on stock geometry under a replaced design.

Stock NWN draws every body's modular part with the human part's texture, so
tintmap.2da binds pfo0_pelvis014 to pfh0_pelvis014's tint map. When the human
part is replaced by a new design with its own texture, a body that still ships
the stock mesh renders that texture on the stock UVs: the part shows scrambled
or missing patches on that species. GenerateRacialPartVariants.py fixes such a
body by refitting the donor (list it in RacialPartVariants.json).

A part is reported when, on a playable body (p[mf][heod]0):
  * its mesh is the stock base-game mesh of the same name,
  * its palette donor (GenerateRacialPartVariants.donor_for) is a new design,
    whose mesh nodes or vertex counts differ from the stock donor, and
  * tintmap.2da binds it to the donor's tint map.

Heads are species models, not shared armor, and are not checked.
RacialPartCoverage.json lists the known gaps still waiting on an art decision;
--check fails on any other gap and on a listed gap that is gone.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np

import CompileModels as cm
import GenerateRacialPartVariants as variants
import GenerateTintMapAssets as tint
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
KNOWN_GAPS = ROOT / "tools/RacialPartCoverage.json"
TINT_TABLE = ROOT / "sw_2da/tintmap.2da"
DEFAULT_GAME_DATA = Path(os.environ.get(
    "NWN_GAME_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights\data"))
PLAYABLE = re.compile(r"p[mf][heod]0_(?!head)[a-z]+?_?\d{3}")
ASCII_MESH = re.compile(rb"^\s*node\s+(?:trimesh|skin|danglymesh)\s+(\S+)", re.IGNORECASE | re.MULTILINE)
TOLERANCE = 1e-4


def tint_rows() -> dict[str, str]:
    rows = {}
    for line in TINT_TABLE.read_text(encoding="latin-1").splitlines():
        columns = line.split()
        if len(columns) == 4:
            rows[columns[1].lower()] = columns[2].lower()
    return rows


def meshes(data: bytes):
    """(name, vertex positions) of every mesh, or names only for an ASCII model."""
    if not cm.binary(data):
        return [(name.decode("latin-1").lower(), None) for name in ASCII_MESH.findall(data)]
    model = Model(data, False)
    return [(mesh.name, np.array(mesh.read(data, mesh.verts, 3))) for mesh in variants.meshes(model)]


class Coverage:
    def __init__(self, resources: variants.Resources):
        self.resources = resources
        self.cache = {}

    def stock_meshes(self, model: str):
        return meshes(tint.extract_stock_bif_resource(*self.resources.stock[model]))

    def active_meshes(self, model: str):
        return meshes(self.resources.load(model))

    def compare(self, model: str) -> str:
        """'stock' (stock mesh), 'tweak' (stock layout, moved vertices) or 'design' (new mesh)."""
        if model not in self.cache:
            if model not in self.resources.stock:
                result = "design"
            elif model not in self.resources.active:
                result = "stock"
            else:
                active, stock = self.active_meshes(model), self.stock_meshes(model)
                same_layout = [name for name, _ in active] == [name for name, _ in stock] and all(
                    a is not None and b is not None and a.shape == b.shape for (_, a), (_, b) in zip(active, stock))
                if not same_layout:
                    result = "design"
                elif all(np.allclose(a, b, atol=TOLERANCE) for (_, a), (_, b) in zip(active, stock)):
                    result = "stock"
                else:
                    result = "tweak"
            self.cache[model] = result
        return self.cache[model]


def gaps(resources: variants.Resources) -> dict[str, str]:
    """Stale target -> palette donor."""
    rows = tint_rows()
    refits = set(json.loads(variants.MANIFEST.read_text(encoding="utf-8"))["targets"])
    coverage = Coverage(resources)
    result = {}
    names = {name for name in list(resources.active) + list(resources.stock) if PLAYABLE.fullmatch(name)}
    for target in sorted(names - refits):
        donor = variants.donor_for(target, resources)
        if donor is None or rows.get(target) is None or rows.get(target) != rows.get(donor):
            continue
        if coverage.compare(target) == "stock" and coverage.compare(donor) == "design":
            result[target] = donor
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail on gaps not listed in RacialPartCoverage.json")
    parser.add_argument("--game-data", type=Path, default=DEFAULT_GAME_DATA, help="NWN data directory")
    args = parser.parse_args()
    if not (args.game_data / "nwn_base.key").is_file():
        raise SystemExit(f"Set NWN_GAME_DATA or --game-data to the game's data directory (missing {args.game_data})")
    found = gaps(variants.Resources(args.game_data))
    if not args.check:
        for target, donor in found.items():
            print(f"{target} renders {donor}'s texture on stock geometry")
        print(f"{len(found)} stale racial part variants")
        return
    known = set(json.loads(KNOWN_GAPS.read_text(encoding="utf-8"))["knownGaps"])
    new, fixed = sorted(set(found) - known), sorted(known - set(found))
    if new or fixed:
        raise SystemExit(
            f"Stale racial part variants: {new} (refit them with GenerateRacialPartVariants.py, ItemAppearanceRules.md). "
            f"No longer stale, remove from RacialPartCoverage.json: {fixed}")
    print(f"No new stale racial part variants ({len(known)} known gaps).")


if __name__ == "__main__":
    main()
