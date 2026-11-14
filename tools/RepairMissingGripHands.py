"""Restore the missing fingers on cuff-only hands 100/111.

The manifest pins the released cuff and same-body style-110 donor Git blobs.
Only the donor below the cuff's distal edge (with 3 mm overlap) is appended.
Existing cuff geometry, UVs, normals, materials and transforms stay unchanged.
No other hand style is owned by this recipe. --check needs no model compiler;
--apply rebuilds from the pinned originals, never from its previous output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np

import CleanModels
import CompileModels as mdl
import GenerateTintMapAssets as tint
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_suffix('.json')
OVERLAP = .003


def source(blob):
    return subprocess.check_output(['git', '-C', str(ROOT), 'cat-file', 'blob', blob])


def meshes(data):
    model = Model(data, False)
    poses = model.pose()
    result = []
    for name, _, geometry in mdl.binary_nodes(data):
        if geometry is None:
            continue
        position, rotation, scale = poses[name]
        # All diagnosed cuffs and style-110 donors are rigid in model space.
        # Fail instead of silently flattening a future rigged replacement.
        if position != (0., 0., 0.) or rotation != (0., 0., 0., 1.) or scale != 1 or 'weights' in geometry:
            raise ValueError(f'{name}: expected a rigid identity-space mesh')
        result.append((name, geometry))
    return result


def clip_triangle(vertices, uvs, normals, cutoff):
    polygon = [np.concatenate([v, uv, n]) for v, uv, n in zip(vertices, uvs, normals)]
    result = []
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if first[2] <= cutoff:
            result.append(first)
        if (first[2] <= cutoff) != (second[2] <= cutoff):
            weight = (cutoff - first[2]) / (second[2] - first[2])
            result.append(first + weight * (second - first))
    return result


def fingers(cuff, donor):
    cuff_meshes = meshes(cuff)
    if len(cuff_meshes) != 1:
        raise ValueError('Expected exactly one original cuff mesh')
    cutoff = min(v[2] for v in cuff_meshes[0][1]['verts']) + OVERLAP
    output = []
    for _, mesh in meshes(donor):
        vertices, uvs, normals = (np.array(mesh[key]) for key in ('verts', 'tverts', 'normals'))
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        for face in mesh['faces']:
            indices = face[:3]
            polygon = clip_triangle(vertices[indices], uvs[indices], normals[indices], cutoff)
            for index in range(1, len(polygon) - 1):
                triangle = np.array([polygon[0], polygon[index], polygon[index + 1]])
                if np.linalg.norm(np.cross(triangle[1, :3] - triangle[0, :3], triangle[2, :3] - triangle[0, :3])) < 1e-10:
                    continue
                triangle[:, 5:] /= np.linalg.norm(triangle[:, 5:], axis=1)[:, None]
                output.append(triangle)
    if not output:
        raise ValueError('Donor does not reach the cuff')
    return np.array(output), cutoff


def added_node(name, data, bitmap, material):
    packed = data.reshape(-1, 8)
    lines = ['node trimesh grip_hand_0', f'  parent {name}', '  position 0 0 0',
             '  orientation 0 0 1 0', '  ambient 1 1 1', '  diffuse 1 1 1',
             '  specular 0 0 0', '  shininess 1', f'  bitmap {bitmap}',
             f'  materialname {material}', '  render 1', '  shadow 1']
    for key, rows in [('verts', packed[:, :3]), ('normals', packed[:, 5:]),
                      ('tverts', np.column_stack([packed[:, 3:5], np.zeros(len(packed))]))]:
        lines.append(f'  {key} {len(rows)}')
        lines.extend('    ' + ' '.join(format(float(v), '.9g') for v in row) for row in rows)
    lines.append(f'  faces {len(data)}')
    for index in range(len(data)):
        a, b, c = (3 * index + offset for offset in range(3))
        lines.append(f'    {a} {b} {c} 1 {a} {b} {c} 0')
    return '\n'.join([*lines, 'endnode'])


def validate(entry, current):
    cuff, donor = source(entry['sourceBlob']), source(entry['donorBlob'])
    original = meshes(cuff)
    selected = meshes(current)
    if len(selected) != len(original) + 1:
        raise ValueError(f"{entry['path']}: expected one added finger mesh")
    for (name, before), (other_name, after) in zip(original, selected):
        if name != other_name or before.keys() != after.keys():
            raise ValueError(f'{name}: original mesh inventory changed')
        for key in before.keys() - {'_mesh_offset'}:
            if before[key] != after[key]:
                raise ValueError(f'{name}: original {key} changed')
    original_materials = [(v[1], v[3]) for v in tint.read_binary_model_material_fields(Path(entry['path']), cuff)]
    current_materials = [(v[1], v[3]) for v in tint.read_binary_model_material_fields(Path(entry['path']), current)]
    if not all(item in current_materials for item in original_materials):
        raise ValueError('An original cuff material changed')
    expected, cutoff = fingers(cuff, donor)
    mesh = selected[-1][1]
    actual = []
    for face in mesh['faces']:
        indices = face[:3]
        actual.append(np.column_stack([np.array(mesh[key])[indices] for key in ('verts', 'tverts', 'normals')]))
    if np.array(actual).shape != expected.shape or not np.allclose(actual, expected, atol=1e-7, rtol=0):
        raise ValueError('Added fingers differ from the pinned donor recipe')
    if max(v[2] for v in mesh['verts']) > cutoff + 1e-7:
        raise ValueError('Added fingers extend into the protected cuff')
    if hashlib.sha256(current).hexdigest() != entry['outputSha256']:
        raise ValueError('Compiled output differs from the reviewed hand')


def apply(entries):
    compiler = CleanModels.find_compiler()
    with tempfile.TemporaryDirectory(prefix='swlor-grip-hands-') as temporary:
        stage = Path(temporary)
        decompiled, compiled = stage / 'decompiled', stage / 'compiled'
        decompiled.mkdir(); compiled.mkdir()
        pending = []
        for entry in entries:
            name = Path(entry['path']).stem.lower()
            cuff, donor = source(entry['sourceBlob']), source(entry['donorBlob'])
            base = stage / (name + '.mdl'); base.write_bytes(cuff)
            CleanModels.run(compiler, stage, 'decompile', [base], decompiled, name + '-decompile.json')
            text = (decompiled / base.name).read_text()
            data, _ = fingers(cuff, donor)
            material_source = cuff if name.endswith('100') else donor
            fields = tint.read_binary_model_material_fields(base, material_source)
            if len(fields) != 1:
                raise ValueError('Expected a single donor material')
            bitmap = fields[0][1]
            material = fields[0][3] if name.endswith('100') else 'griphand_' + name[9]
            text = re.sub(r'(?im)^\s*endmodelgeom\b', '\n' + added_node(name, data, bitmap, material) + '\nendmodelgeom', text, count=1)
            authored = stage / ('authored-' + name); authored.mkdir()
            target = authored / base.name; target.write_text(text, encoding='ascii')
            CleanModels.run(compiler, stage, 'compile', [target], compiled, name + '-compile.json')
            output = (compiled / base.name).read_bytes()
            validate(entry, output)
            pending.append((ROOT / entry['path'], output))
        for path, data in pending:
            path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true'); mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    entries = json.loads(MANIFEST.read_text())['models']
    if args.apply:
        apply(entries)
    for entry in entries:
        validate(entry, (ROOT / entry['path']).read_bytes())
    print(f'Missing-hand repair: {len(entries)} pinned models pass; original cuffs unchanged.')


if __name__ == '__main__':
    main()
