"""Bind Miris Aven's shin skins to the male human robe 236 body."""
from __future__ import annotations

import argparse
from pathlib import Path

from ModelLodAssets import ModelPath
from NeckSkinBindings import body_model, mapping_for, remap

ROOT = Path(__file__).resolve().parents[1]


def outputs(root=ROOT):
    donor = body_model(ModelPath(root / 'sw_pt_root/pmh0.mdl').read_bytes())
    wearer = body_model(ModelPath(root / 'sw_pt_root/pmh200.mdl').read_bytes())
    for side in ('l', 'r'):
        source = ModelPath(root / f'sw_pt_{side}shin/pmh0_shin{side.upper()}161.mdl').read_bytes()
        name = f'pmh200_shin{side}161'
        yield root / f'sw_pt_{side}shin/{name}.mdl', remap(
            source, name, mapping_for(source, donor, wearer), bone_count=len(wearer.nodes))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    for path, expected in outputs():
        if args.apply:
            path.write_bytes(expected)
        if not path.is_file() or path.read_bytes() != expected:
            raise ValueError(f'Stale robe shin model: {path.name}; run with --apply')
        print(f'Validated {path.name}')


if __name__ == '__main__':
    main()
