"""Fail HAK packaging when source edits have left neck skins stale."""
import hashlib
import json
from pathlib import Path


def check(root):
    manifest = json.loads((root / 'tools/NeckModels.json').read_text())
    paths = {p.stem: p for directory in ('sw_pt_neck', 'sw_pt_root')
             for p in (root / directory).glob('*.mdl')}
    failures = []
    for name, expected in manifest['inputs'].items():
        if name in paths and hashlib.sha256(paths[name].read_bytes()).hexdigest() != expected:
            failures.append(name)
    for name, entry in manifest['aliases'].items():
        path = paths.get(name)
        if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            failures.append(name)
    for name, expected in manifest['tables'].items():
        if hashlib.sha256((root / 'sw_2da' / name).read_text().encode()).hexdigest() != expected:
            failures.append(name)
    if failures:
        raise ValueError(f'Stale neck assets: {failures[:20]}. Run GenerateNeckModels.py --game-data <NWN/data> --apply.')
    print(f'Neck catalog: {len(manifest["aliases"])} generated models match their audited sources.')


if __name__ == '__main__':
    check(Path(__file__).resolve().parents[1])
