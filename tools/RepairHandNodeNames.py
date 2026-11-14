"""Repair colliding child-node names on the four authored Droid 218 hands.

Only fixed-width child name fields change. Geometry, cuffs, materials, UVs,
controllers, part IDs and parent indices remain byte-for-byte identical.
Rebuild from the pinned pre-repair blobs, never from a previous output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_suffix('.json')


def build(entry):
    path = entry['path']
    match = re.fullmatch(r'sw_pt_(?P<side>[rl])hand/p[fm]z0_hand(?P=side)218\.mdl', path.lower())
    if not match:
        raise ValueError('Node repair owns only normal Droid hand 218')
    original = subprocess.check_output(['git', '-C', str(ROOT), 'cat-file', 'blob', entry['sourceBlob']])
    if hashlib.sha256(original).hexdigest() != entry['sourceSha256']:
        raise ValueError('Pinned hand source fingerprint differs')
    before = Model(original, False)
    names = [node[0] for node in before.nodes]
    if len(set(names)) == len(names):
        raise ValueError('Pinned source no longer has the diagnosed node-name collision')
    output = bytearray(original)
    for index, (_, parent, _, _, offset) in enumerate(before.nodes):
        if parent is None:
            continue
        name = match[1] + '218_mesh' + str(index).zfill(2)
        output[offset + 32:offset + 64] = name.encode('ascii').ljust(32, b'\0')
    after = Model(output, False)
    if len({node[0] for node in after.nodes}) != len(after.nodes):
        raise ValueError('Repaired hand still contains colliding nodes')
    if hashlib.sha256(output).hexdigest() != entry['outputSha256']:
        raise ValueError('Hand repair differs from its client-reviewed payload')
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    entries = json.loads(MANIFEST.read_text())['models']
    paths = [entry['path'] for entry in entries]
    if len(paths) != 4 or len(set(paths)) != 4:
        raise ValueError('Expected exactly four owned Droid 218 hands')
    outputs = [(entry, build(entry)) for entry in entries]
    for entry, output in outputs:
        path = ROOT / entry['path']
        if args.apply:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
            if current not in (entry['sourceSha256'], entry['outputSha256']):
                raise ValueError('Authored source changed; review before repairing its names')
            path.write_bytes(output)
        elif path.read_bytes() != output:
            raise ValueError(f"Stale hand node repair: {entry['path']}")
    print('Droid 218 node-name repairs: 4 pass; all other bytes unchanged.')


if __name__ == '__main__':
    main()
