"""Bind externally skinned male human shoulders to each robe phenotype's skeleton.

Shared compiled aliases preserve materials, geometry, weights and inverse binds.
The server projects them only in appearance packets; saved style IDs stay authored.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import GenerateTintMapAssets as tint
from ModelLodAssets import ModelPath, content, directories, model_paths
from NeckSkinBindings import body_model, mapping_for, needs_remap, remap
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools/ShoulderModels.json'
IMPLEMENTATION = ('tools/GenerateShoulderModels.py', 'tools/NeckSkinBindings.py',
                  'tools/RobePoseAudit.py', 'tools/CompileModels.py', 'tools/CleanModels.py',
                  'tools/GenerateTintMapAssets.py', 'tools/ModelLodAssets.py')


def digest(data):
    return hashlib.sha256(content(data)).hexdigest()


def shoulder_skins(data):
    """Read used external bones without repairing legacy shoulder weight bytes.

    Style 258 has finite, positive but unnormalized authored weights. Relocating
    its bone tables does not require normalization; all raw mesh bytes survive.
    Keep the stricter neck importer unchanged.
    """
    model = Model(data, False)
    raw = 12 + model.uint(4)
    if raw + model.uint(8) != len(data):
        raise ValueError('Invalid model section lengths')
    result = []
    for name, _, _, _, node in model.nodes:
        flags = model.uint(node + 108)
        if not flags & 64:
            continue
        mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
        skin = mesh + 512
        count = struct.unpack_from('<H', data, mesh + 448)[0]
        stride = model.uint(mesh + 440)
        used = set()
        for vertex in range(count):
            weights = struct.unpack_from('<4f', data, raw + model.uint(skin + 12) + vertex * (stride or 16))
            indices = struct.unpack_from('<4h', data, raw + model.uint(skin + 16) + vertex * (stride or 8))
            if any(not math.isfinite(w) or w < 0 or w > 1.00001 for w in weights):
                raise ValueError(f'{name}: invalid weights at vertex {vertex}')
            for weight, slot in zip(weights, indices):
                if not weight:
                    continue
                if not 0 <= slot < 17:
                    raise ValueError(f'{name}: invalid bone slot {slot}')
                used.add(slot)
        bones = struct.unpack_from('<17h', data, skin + 64)
        pointer, length = model.uint(skin + 20), model.uint(skin + 24)
        for slot in used:
            bone = bones[slot]
            if not 0 <= bone < min(length, model.uint(skin + 32), model.uint(skin + 44)):
                raise ValueError(f'{name}: invalid external bone {bone}')
            if struct.unpack_from('<h', data, 12 + pointer + bone * 2)[0] != slot:
                raise ValueError(f'{name}: inconsistent bone map for slot {slot}')
        if length > len(model.nodes):
            result.append((skin, tuple((slot, bones[slot]) for slot in sorted(used))))
    return result


def shoulder_sources(active):
    sources = {}
    for name, path in sorted(active.items()):
        if not re.fullmatch(r'pmh0_sho[lr]\d{3}', name):
            continue
        data = path.read_bytes()
        external = shoulder_skins(data)
        if external:
            sources[name] = (data, external)
    return sources


def plan():
    active = tint.find_active_models()
    source_body = body_model(active['pmh0'].read_bytes())
    roots = sorted({f'pmh{row.split()[2]}'
                    for row in (ROOT / 'sw_2da/roberender.2da').read_text().splitlines()[3:]
                    if row.split()[1].startswith('pmh0_robe')})
    bodies = {name: body_model(active[name].read_bytes()) for name in roots}
    outputs, models, inputs, groups = {}, {}, {}, {}
    sources = shoulder_sources(active)
    for name in ['pmh0', *roots]:
        path = active[name]
        inputs[path.relative_to(ROOT).as_posix()] = digest(path.read_bytes())
    for source, (data, external) in sources.items():
        inputs[active[source].relative_to(ROOT).as_posix()] = digest(data)
        for root, body in bodies.items():
            mapping = mapping_for(data, source_body, body, external)
            if needs_remap(mapping):
                groups.setdefault((source, mapping), []).append(root)
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {'models': {}}
    by_signature = {(record['source'], json.dumps(record['mapping'])): name
                    for name, record in previous['models'].items()}
    next_id = max([999, *(int(name.split('_sho')[1][1:]) for name in previous['models'])]) + 1
    rows = []
    for (source, mapping), roots in sorted(groups.items()):
        name = by_signature.get((source, json.dumps(mapping)))
        if name is None:
            if next_id > 65535:
                raise ValueError('Shoulder aliases exceed the native armor part range')
            name = f'pmh0_sho{source[8]}{next_id}'
            next_id += 1
        data, external = sources[source]
        output = remap(data, name, mapping, external, max(len(bodies[root].nodes) for root in roots))
        directory = 'sw_pt_lshoul' if source[8] == 'l' else 'sw_pt_rshoul'
        relative = f'{directory}/{name}.mdl'
        outputs[relative] = output
        models[name] = {'source': source, 'path': relative, 'mapping': mapping, 'sha256': digest(output)}
        rows.extend((root, source, name) for root in roots)
    table = '2DA V2.0\n\n    BODY SOURCE RENDER\n'
    table += ''.join(f'{i} {body} {source} {render}\n' for i, (body, source, render) in enumerate(sorted(rows)))
    inputs['sw_2da/roberender.2da'] = digest((ROOT / 'sw_2da/roberender.2da').read_bytes())
    inputs['hakbuilder.json'] = digest((ROOT / 'hakbuilder.json').read_bytes())
    implementation = {path: hashlib.sha256((ROOT / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                      for path in IMPLEMENTATION}
    return outputs, {'models': dict(sorted(models.items())), 'inputs': dict(sorted(inputs.items())),
                     'sources': sorted(sources),
                     'implementation': implementation, 'table_sha256': digest(table.encode())}, table


def check(root=ROOT):
    manifest = json.loads((root / 'tools/ShoulderModels.json').read_text())
    failures = []
    # Discover afresh: a newly added override must invalidate the old selected
    # source even when every previously fingerprinted file is unchanged.
    active = {path.stem.lower(): path for directory in directories(root) for path in model_paths(directory)}
    if sorted(shoulder_sources(active)) != manifest.get('sources'):
        failures.append('active shoulder sources')
    if 'hakbuilder.json' not in manifest['inputs']:
        failures.append('hakbuilder.json')
    implementation = {**manifest['implementation'], **manifest.get('validation_implementation', {})}
    if not set(manifest.get('validation_implementation', {})) <= set(IMPLEMENTATION):
        failures.append('validation implementation')
        implementation = manifest['implementation']
    for relative, expected in {**manifest['inputs'], **implementation}.items():
        path = ModelPath(root / relative)
        if path.suffix == '.mdl' and active.get(path.stem.lower()) != path:
            failures.append(f'active model {relative}')
        if not path.is_file() or digest(path.read_bytes()) != expected:
            failures.append(relative)
    for record in manifest['models'].values():
        path = ModelPath(root / record['path'])
        if active.get(path.stem.lower()) != path:
            failures.append(f'active model {record["path"]}')
        if not path.is_file() or digest(path.read_bytes()) != record['sha256']:
            failures.append(record['path'])
    table = root / 'sw_2da/shoulderrender.2da'
    if not table.is_file() or digest(table.read_bytes()) != manifest['table_sha256']:
        failures.append('shoulderrender.2da')
    if set(implementation) != set(IMPLEMENTATION):
        failures.append('implementation')
    if failures:
        raise ValueError(f'Stale shoulder assets: {failures[:20]}; run GenerateShoulderModels.py --apply')
    print(f'Validated {len(manifest["models"])} shoulder models and their source skeletons.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        check()
        return
    outputs, manifest, table = plan()
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {'models': {}}
    owned = {record['path'] for record in previous['models'].values()}
    active = tint.find_active_models()
    for relative, data in outputs.items():
        path = ROOT / relative
        selected = active.get(path.stem.lower())
        if selected is not None and (selected != path or path.with_suffix('.lod').is_file()):
            raise ValueError(f'Shoulder resource is shadowed or redirected: {relative}')
        if path.exists() and relative not in owned and path.read_bytes() != data:
            raise ValueError(f'Unowned shoulder resource already exists: {relative}')
    for relative, data in outputs.items():
        path = ROOT / relative
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)
    # Keep old resources: saved creatures can refer to retired robe phenotypes.
    manifest['models'] = {**previous['models'], **manifest['models']}
    (ROOT / 'sw_2da/shoulderrender.2da').write_text(table, encoding='utf8', newline='\n')
    MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8', newline='\n')
    check()


if __name__ == '__main__':
    main()
