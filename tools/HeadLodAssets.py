"""Validate authored three-level player heads without treating them as redirects."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'tools/HeadLodAssets.json'
HEAD = re.compile(r'p[fm][heodz]0_head\d{3}\Z')
RESREF = re.compile(r'[a-z0-9_]{1,16}\Z')


def catalog(root=ROOT):
    path = Path(root) / MANIFEST
    if not path.is_file():
        return {}
    document = json.loads(path.read_text())
    if document.get('version') != 1 or not isinstance(document.get('heads'), dict):
        raise ValueError('Invalid authored head LOD manifest')
    return document['heads']


def production_paths(root=ROOT):
    root = Path(root)
    return {root / 'sw_pt_head' / (name + '.mdl') for name in catalog(root)}


def _inspect(path):
    import GenerateTintMapAssets as tint
    from ModelLodAssets import model_name
    data = path.read_bytes()
    if data[:4] != bytes(4):
        raise ValueError(f'{path.name}: expected a compiled native MDL')
    fields = tint.read_binary_model_material_fields(path, data)
    triangles = sum(struct.unpack_from('<I', data, offset - tint.MESH_TEXTURE0_OFFSET + 12)[0]
                    for offset, _, _, _ in fields)
    return {'sha256': hashlib.sha256(data).hexdigest(), 'triangles': triangles,
            'identity': model_name(data), 'materials': tint.read_model_material_bindings(path)}


def check(root=ROOT):
    root = Path(root)
    try:
        heads = catalog(root)
    except (ValueError, OSError) as error:
        return [str(error)]
    errors = []
    targets = set(heads)
    for name, record in heads.items():
        try:
            if not HEAD.fullmatch(name):
                raise ValueError(f'{name}: expected a playable normal-phenotype head')
            close = _inspect(root / 'sw_pt_head' / (name + '.mdl'))
            if close['identity'] != name or close['sha256'] != record['closeSha256']:
                raise ValueError(f'{name}: approved close model changed')
            if not 0 < close['triangles'] <= 30000:
                raise ValueError(f'{name}: close model exceeds 30000 triangles')
            levels = record['levels']
            if len(levels) != 2:
                raise ValueError(f'{name}: LOD 1 and LOD 2 are both required')
            definition = (root / 'sw_pt_head' / (name + '.lod')).read_text().splitlines()
            actual = [line.split() for line in definition if line.strip()]
            if len(actual) != 3 or actual[0] != [name]:
                raise ValueError(f'{name}: definition must retain its own close MDL')
            previous = 0
            omitted = record.get('omittedEyelashMaterials', [])
            close_names = {material or texture for texture, material in close['materials']}
            if any(not re.search(r'(?:eyelashes|eyelash|lash)$', name) or name not in close_names
                   for name in omitted):
                raise ValueError(f'{name}: only existing separate eyelash materials may be omitted')
            expected_materials = [(texture, material) for texture, material in close['materials']
                                  if (material or texture) not in omitted]
            for index, (level, line, cap) in enumerate(zip(levels, actual[1:], (10000, 3000)), 1):
                target = level['model']
                if not RESREF.fullmatch(target) or target in targets:
                    raise ValueError(f'{name}: duplicate or invalid LOD model {target}')
                targets.add(target)
                distance = float(level['distance'])
                if not math.isfinite(distance) or distance <= previous:
                    raise ValueError(f'{name}: LOD distances must increase')
                previous = distance
                if len(line) != 2 or line[1] != target or float(line[0]) != distance:
                    raise ValueError(f'{name}: LOD {index} differs from its manifest')
                low = _inspect(root / 'sw_pt_head' / (target + '.mdl'))
                if low['identity'] != target or low['sha256'] != level['sha256']:
                    raise ValueError(f'{name}: LOD {index} compiled geometry changed')
                if not 0 < low['triangles'] <= cap or low['triangles'] != level['triangles']:
                    raise ValueError(f'{name}: LOD {index} exceeds {cap} triangles or count differs')
                if set(low['materials']) != set(expected_materials):
                    raise ValueError(f'{name}: LOD {index} material bindings differ')
        except (KeyError, TypeError, ValueError, OSError, struct.error) as error:
            errors.append(str(error))
    return errors


if __name__ == '__main__':
    failures = check()
    for failure in failures:
        print(failure)
    print(f'Checked {len(catalog())} authored head LOD sets; {len(failures)} errors')
    raise SystemExit(bool(failures))
