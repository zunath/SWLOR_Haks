"""Restore large-body robe animation inheritance from its normal-body variant.

Only the supermodel name and native animation part IDs change. Geometry, skin
weights, inverse binds, transforms and local animation controllers stay intact.
"""
import argparse
from pathlib import Path
import struct

import RobePoseAudit as poses

ROOT = Path(__file__).resolve().parents[1]


def restore_parent(data, reference, parent):
    if not parent or len(parent.encode('ascii')) > 16:
        raise ValueError('Expected a native supermodel resref')
    original, normal = poses.Model(data), poses.Model(reference, False)
    expected = {name: part for name, _, part, _, _ in normal.nodes[1:]}
    if len(expected) != len(normal.nodes) - 1:
        raise ValueError('Reference has ambiguous joint names')
    names = [name for name, _, _, _, _ in original.nodes[1:]]
    if len(names) != len(set(names)) or set(names) != set(expected):
        raise ValueError('Robe variants must have the same unambiguous joints')
    used = [part for part in expected.values() if part >= 0]
    if len(used) != len(set(used)):
        raise ValueError('Reference has colliding animation part IDs')
    result = bytearray(data)
    result[180:244] = parent.encode('ascii').ljust(64, b'\0')
    for nodes in [original.nodes, *(nodes for _, nodes in original.clips.values())]:
        for name, ancestor, _, _, offset in nodes:
            part = 0 if ancestor is None else expected.get(name)
            if part is None:
                raise ValueError(f'Local animation references an absent joint: {name}')
            struct.pack_into('<i', result, offset + 28, part)
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robe', type=int, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    folder = ROOT / 'sw_pt_robe'
    targets = sorted(folder.glob(f'p??22_robe{args.robe:03d}.mdl'))
    if not targets:
        raise ValueError(f'No large-body variants found for robe {args.robe}')
    changes = {}
    for path in targets:
        reference = folder / (path.name[:3] + '0' + path.name[5:])
        before = path.read_bytes()
        after = restore_parent(before, reference.read_bytes(), reference.stem)
        if after != before:
            changes[path] = after
            print(f'{path.name}: {poses.Model(before, False).parent} -> {reference.stem}')
    # Validate every variant before replacing any source.
    if args.apply:
        for path, data in changes.items():
            path.write_bytes(data)
    print(f"{'Restored' if args.apply else 'Would restore'} {len(changes)} robe variants")


if __name__ == '__main__':
    main()
