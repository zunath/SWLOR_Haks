"""Hang neck 32's cloak from the body's cloak bones (NeckCloakSkins.json).

Neck 32 is a hooded cloak modelled as a dangly mesh fixed to the neck. A dangly mesh
only sways around its attachment, so the long cloak stayed where the neck put it and
striding legs passed through its lower half (test-server report, October 2026).

This decompiles the original, turns the dangly mesh into an externally skinned mesh
with the same geometry, materials and UVs, and hangs it from the cloak bones the way
ChestSkirtBindings.py hangs neck 291's cape (cloak_weights): the hood and collar stay
on the neck, and the cloak eases onto the cloak columns below the shoulders, so the
walk, run and idle clips swing it like a stock cloak. CleanModels compiles the result
against the wearer's skeleton; every compiled vertex must sit where the original put
it in the bind pose. Bodies whose cloak bones never animate (the dwarf and half-orc
roots, ChestSkirtBindings.cloak_animated) keep the dangly original.

The originals come from the Git blobs recorded in NeckCloakSkins.json, so --apply is
repeatable. Run GenerateNeckModels.py --apply afterwards: it audits neck sources.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

import ChestSkirtBindings as skirts
import CompileModels as mdl

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools' / 'NeckCloakSkins.json'
# Dangly-mesh settings a skin node does not carry.
DANGLY_KEYS = ('displacement', 'tightness', 'period')


def convert(text: str, pose: dict) -> str:
    """Turn each dangly mesh of a decompiled neck part into a skin on the cloak bones."""
    lines = text.replace('\r\n', '\n').split('\n')
    neck = pose['neck_g'][0]
    if pose['neck_g'][1] != (0., 0., 0., 1.):
        raise ValueError('Expected an unrotated neck joint in the bind pose')
    result = []
    index = 0
    converted = 0
    while index < len(lines):
        match = re.match(r'(\s*)node danglymesh (\S+)$', lines[index])
        if not match:
            result.append(lines[index])
            index += 1
            continue
        indent, name = match.groups()
        result.append(f'{indent}node skin {name}')
        index += 1
        offset, vertices = (0., 0., 0.), []
        while lines[index].strip() != 'endnode':
            words = lines[index].split()
            key = words[0] if words else ''
            if key == 'position':
                # A skin's vertices are in the part's space: fold the mesh offset into them.
                offset = tuple(map(float, words[1:4]))
            elif key == 'orientation' and tuple(map(float, words[1:5])) != (0., 0., 1., 0.):
                raise ValueError(f'{name}: expected an unrotated dangly mesh')
            elif key == 'constraints':
                index += int(words[1])
            elif key == 'verts':
                result.append(lines[index])
                for row in lines[index + 1:index + 1 + int(words[1])]:
                    vertex = tuple(a + b for a, b in zip(map(float, row.split()), offset))
                    vertices.append(vertex)
                    result.append(f'{indent}        {vertex[0]:.7g} {vertex[1]:.7g} {vertex[2]:.7g}')
                index += int(words[1])
            elif key not in DANGLY_KEYS:
                result.append(lines[index])
            index += 1
        result.append(f'{indent}    weights {len(vertices)}')
        for vertex in vertices:
            weights = skirts.cloak_weights(tuple(a + b for a, b in zip(vertex, neck)), pose)
            # Above the cloak's second row the source binding, the neck, keeps its share.
            weights['neck_g'] = weights.pop(None, 0.)
            row = sorted(((bone, weight) for bone, weight in weights.items() if weight > 1e-6), key=lambda item: (-item[1], item[0]))
            if len(row) > 4:
                raise ValueError(f'{name}: a cloak vertex needs {len(row)} bones')
            result.append(f'{indent}        ' + ' '.join(f'{bone} {weight:.6g}' for bone, weight in row))
        result.append(lines[index])
        index += 1
        converted += 1
    if not converted:
        raise ValueError('Expected a dangly mesh to convert')
    return '\n'.join(result)


def original_positions(data: bytes, pose: dict) -> list[tuple[float, float, float]]:
    """The original's dangly vertices in the wearer's bind pose."""
    neck = pose['neck_g'][0]
    model = skirts.body_model(data)
    offsets = {name: controllers.get(8, ((0,), [(0., 0., 0.)]))[1][0] for name, _, _, controllers, _ in model.nodes}
    return [tuple(a + b + c for a, b, c in zip(vertex, offsets[name], neck))
            for name, _, mesh in mdl.binary_nodes(data) if mesh and mesh.get('verts') for vertex in mesh['verts']]


def build(entry: dict) -> bytes:
    original = skirts.source(entry['sourceBlob'])
    bones, body = skirts.skeleton(original)
    pose = body.pose()
    if not skirts.cloak_animated(body):
        raise ValueError(f"{entry['path']}: the wearer's cloak bones never animate")
    name = Path(entry['path']).stem
    with tempfile.TemporaryDirectory(prefix='neckcloak-') as staging:
        staging = Path(staging)
        for folder in ('binary', 'ascii', 'decompiled', 'compiled'):
            (staging / folder).mkdir()
        compiler, _ = mdl.prepare_compiler(staging)
        (staging / 'binary' / f'{name}.mdl').write_bytes(original)
        mdl.run_compiler(compiler, staging, ['-de', str(staging / 'binary' / f'{name}.mdl'), str(staging / 'decompiled')], 'decompile.log')
        text = (staging / 'decompiled' / f'{name}.mdl').read_text(encoding='latin1')
        (staging / 'ascii' / f'{name}.mdl').write_text(convert(text, pose), encoding='latin1', newline='\n')
        mdl.run_compiler(compiler, staging, ['-cne', str(staging / 'ascii' / f'{name}.mdl'), str(staging / 'compiled')], 'compile.log')
        compiled = (staging / 'compiled' / f'{name}.mdl').read_bytes()
    if mdl.external_skeleton_name(compiled) != bones[0]:
        raise ValueError(f"{entry['path']}: compiled cloak is not skinned to {bones[0]}")
    before = original_positions(original, pose)
    after = skirts.rest_positions(compiled)
    # The compiler splits vertices along UV seams; each one must sit on an original vertex.
    error = max(min(math.dist(point, vertex) for vertex in before) for point in after)
    if error > 2e-5 or len(after) < len(before):
        raise ValueError(f"{entry['path']}: bind shape moved by {error:.9g} m")
    return compiled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    stale = []
    for name, entry in manifest['models'].items():
        path = ROOT / entry['path']
        data = build(entry)
        digest = hashlib.sha256(data).hexdigest()
        if path.read_bytes() != data or entry['sha256'] != digest:
            stale.append(name)
            if args.apply:
                path.write_bytes(data)
                entry['sha256'] = digest
    if args.check and stale:
        raise ValueError(f'Neck cloak skins are stale: {stale}; run NeckCloakSkins.py --apply')
    if args.apply and stale:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"{'Rebuilt' if args.apply else 'Validated'} {len(manifest['models'])} neck cloak skins"
          f"{f' ({len(stale)} updated)' if args.apply else ''}.")


if __name__ == '__main__':
    main()
