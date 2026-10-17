"""Bind the reported male human shoulders to each robe phenotype's skeleton.

Shared compiled aliases preserve materials, geometry, weights and inverse binds.
The server projects them only in appearance packets; saved style IDs stay authored.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import GenerateTintMapAssets as tint
from ModelLodAssets import content
from NeckSkinBindings import body_model, mapping_for, needs_remap, remap, skins

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools/ShoulderModels.json'
STYLES = (13, 140, 141, 185, 256, 260)
IMPLEMENTATION = ('tools/GenerateShoulderModels.py', 'tools/NeckSkinBindings.py',
                  'tools/RobePoseAudit.py', 'tools/CompileModels.py')


def digest(data):
    return hashlib.sha256(content(data)).hexdigest()


def plan():
    active = tint.find_active_models()
    source_body = body_model(active['pmh0'].read_bytes())
    roots = sorted({f'pmh{row.split()[2]}'
                    for row in (ROOT / 'sw_2da/roberender.2da').read_text().splitlines()[3:]
                    if row.split()[1].startswith('pmh0_robe')})
    bodies = {name: body_model(active[name].read_bytes()) for name in roots}
    outputs, models, inputs, groups, sources = {}, {}, {}, {}, {}
    for name in ['pmh0', *roots]:
        path = active[name]
        inputs[path.relative_to(ROOT).as_posix()] = digest(path.read_bytes())
    for side in 'lr':
        for style in STYLES:
            source = f'pmh0_sho{side}{style:03}'
            path = active[source]
            data = path.read_bytes()
            external = skins(data)
            if not external:
                raise ValueError(f'{source}: expected an externally bound shoulder skin')
            inputs[path.relative_to(ROOT).as_posix()] = digest(data)
            sources[source] = (data, external)
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
    implementation = {path: hashlib.sha256((ROOT / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                      for path in IMPLEMENTATION}
    return outputs, {'models': dict(sorted(models.items())), 'inputs': dict(sorted(inputs.items())),
                     'implementation': implementation, 'table_sha256': digest(table.encode())}, table


def check(root=ROOT):
    manifest = json.loads((root / 'tools/ShoulderModels.json').read_text())
    failures = []
    for relative, expected in {**manifest['inputs'], **manifest['implementation']}.items():
        path = root / relative
        if not path.is_file() or digest(path.read_bytes()) != expected:
            failures.append(relative)
    for record in manifest['models'].values():
        path = root / record['path']
        if not path.is_file() or digest(path.read_bytes()) != record['sha256']:
            failures.append(record['path'])
    table = root / 'sw_2da/shoulderrender.2da'
    if not table.is_file() or digest(table.read_bytes()) != manifest['table_sha256']:
        failures.append('shoulderrender.2da')
    if set(manifest['implementation']) != set(IMPLEMENTATION):
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
    for relative, data in outputs.items():
        path = ROOT / relative
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
