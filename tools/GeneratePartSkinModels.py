#!/usr/bin/env python3
"""Bind externally skinned armor parts to every robe phenotype's skeleton.

External skins index the wearer's bones by depth-first traversal. Robe body
roots add garment joints and rp_ spacers, so a part compiled against the base
body binds to the wrong joints there. Compiled aliases keep each source part's
vertices, weights, inverse binds and materials and relocate only the external
bone tables. The server projects them in appearance packets; saved style IDs
stay authored.

Necks (GenerateNeckModels.py) and male human shoulders
(GenerateShoulderModels.py) keep their own aliases. Every other modular slot
of every robe body family is covered here.

Robe bodies of one family share a body-node layout, so each source needs one
alias. partlayout.2da names each robe body's layout and partrender.2da lists
the aliases of each layout, keeping the tables small.
Run with --apply after changing robe bodies, base bodies or part meshes, then --check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import CompileModels as mdl
import math
import struct
from ModelLodAssets import content, directories, model_paths
from NeckSkinBindings import body_model, mapping_for, needs_remap, remap
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'tools/PartSkinModels.json'
LAYOUTS = 'sw_2da/partlayout.2da'
RENDERS = 'sw_2da/partrender.2da'
IMPLEMENTATION = ('tools/GeneratePartSkinModels.py', 'tools/NeckSkinBindings.py', 'tools/RobePoseAudit.py', 'tools/CompileModels.py',
                  'tools/CleanModels.py', 'tools/ModelLodAssets.py')
# Model part name -> HAK directory. Necks and heads are not armor slots handled here.
DIRECTORIES = {
    'chest': 'sw_pt_chest', 'pelvis': 'sw_pt_pelvis', 'belt': 'sw_pt_belt',
    'handl': 'sw_pt_lhand', 'handr': 'sw_pt_rhand', 'shinl': 'sw_pt_lshin', 'shinr': 'sw_pt_rshin',
    'legl': 'sw_pt_lthigh', 'legr': 'sw_pt_rthigh', 'footl': 'sw_pt_lfoot', 'footr': 'sw_pt_rfoot',
    'forel': 'sw_pt_lfore', 'forer': 'sw_pt_rfore', 'bicepl': 'sw_pt_lbicep', 'bicepr': 'sw_pt_rbicep',
    'shol': 'sw_pt_lshoul', 'shor': 'sw_pt_rshoul',
}
PART = re.compile(r'^(p[fm][a-z])(\d+)_(' + '|'.join(DIRECTORIES) + r')(\d{3})$')
# GenerateShoulderModels.py owns these sources.
OWNED_ELSEWHERE = re.compile(r'^pmh0_sho[lr]\d{3}$')


def digest(data):
    return hashlib.sha256(content(data)).hexdigest()


def source_digest(path):
    # Git may check out tool sources with CRLF line endings.
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def part_skins(data):
    """Return each external skin's used (slot, bone) pairs without touching mesh bytes.

    Legacy exports carry unnormalized weights and weighted out-of-range slots
    (-1, or garbage in pfo0_pelvis109). Aliases keep every vertex byte, so they
    reproduce the source's handling of both exactly; only valid slots are bound.
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
            if any(not math.isfinite(w) for w in weights):
                raise ValueError(f'{name}: invalid weights at vertex {vertex}')
            for weight, slot in zip(weights, indices):
                if weight and 0 <= slot < 17:
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


def active_models(root):
    # Later HAKs have higher resource priority.
    active = {}
    for directory in directories(root):
        active.update({path.stem.lower(): path for path in model_paths(directory)})
    return active


def read_table(root, relative):
    return [line.split()[1:] for line in (root / relative).read_text().splitlines()[3:] if line.strip()]


def plan(root=ROOT):
    root = Path(root)
    active = active_models(root)
    previous_path = root / MANIFEST
    previous = json.loads(previous_path.read_text()) if previous_path.is_file() else {'models': {}}
    generated = set(previous['models']) | set(previous.get('retired', []))
    defaults = {}
    for line in (root / 'sw_2da/phenotype.2da').read_text().splitlines()[3:]:
        fields = line.split()
        if len(fields) >= 4 and fields[0].isdigit() and fields[3].isdigit():
            defaults[int(fields[0])] = int(fields[3])
    robes = sorted({f'{model[:3]}{phenotype}' for model, phenotype, _ in read_table(root, 'sw_2da/roberender.2da')})
    inputs = {}

    def load(name):
        data = content(active[name].read_bytes())
        inputs[active[name].relative_to(root).as_posix()] = digest(data)
        return data

    bodies = {}

    def body(name):
        if name not in bodies:
            bodies[name] = body_model(load(name))
        return bodies[name]

    parts = {}
    for name in sorted(active):
        match = PART.fullmatch(name)
        if match and name not in generated and not OWNED_ELSEWHERE.fullmatch(name):
            parts.setdefault((match[1], match[3], int(match[4])), {})[int(match[2])] = name
    skins = {}
    groups = {}
    for robe in robes:
        prefix, phenotype = robe[:3], int(robe[3:])
        target = body(robe)
        for (family, kind, style), variants in parts.items():
            if family != prefix:
                continue
            # The client resolves phenotype-specific parts first, then each default phenotype.
            current, visited, source = phenotype, set(), None
            while current not in visited:
                visited.add(current)
                if current in variants:
                    source = variants[current]
                    break
                current = defaults.get(current, 0)
            if source is None:
                continue
            if source not in skins:
                data = load(source)
                # ASCII parts compile locally in the engine; none are external skins.
                try:
                    skins[source] = (data, part_skins(data) if mdl.binary(data) else [])
                except ValueError as error:
                    raise ValueError(f'{source}: {error}') from error
            data, external = skins[source]
            if not external:
                continue
            mapping = mapping_for(data, body(source.split('_', 1)[0]), target, external)
            if needs_remap(mapping):
                groups.setdefault((source, mapping), []).append(robe)
    retired = set(previous.get('retired', []))
    by_signature = {(record['source'], json.dumps(record['mapping'])): name
                    for name, record in previous['models'].items()}
    # Stable allocation survives later additions; IDs are never repurposed.
    used = [int(re.search(r'(\d+)$', name)[1]) for name in [*previous['models'], *retired]]
    next_id = max([999, *used]) + 1
    outputs, models, renders = {}, {}, {}
    for (source, mapping), roots in sorted(groups.items()):
        match = PART.fullmatch(source)
        name = by_signature.get((source, json.dumps(mapping)))
        if name is None:
            if next_id > 65535:
                raise ValueError('Part skin aliases exceed the native armor part range')
            name = f'{match[1]}0_{match[3]}{next_id}'
            next_id += 1
        if len(name) > 16:
            raise ValueError(f'Part skin alias resref is too long: {name}')
        data, external = skins[source]
        # Cover the full traversal of every root sharing the alias, including unused bones.
        output = remap(data, name, mapping, external, max(len(bodies[robe].nodes) for robe in roots))
        relative = f'{DIRECTORIES[match[3]]}/{name}.mdl'
        selected = active.get(name)
        if selected is not None and selected.relative_to(root).as_posix() != relative:
            raise ValueError(f'Part skin alias is shadowed or redirected: {relative}')
        outputs[relative] = output
        models[name] = {'source': source, 'path': relative, 'mapping': mapping, 'sha256': digest(output)}
        for robe in roots:
            renders.setdefault(robe, {})[source] = name
    retired |= previous['models'].keys() - models.keys()
    # Robe bodies with identical source -> alias maps share one layout.
    layouts = {}
    for robe, mapping in sorted(renders.items()):
        layouts.setdefault(tuple(sorted(mapping.items())), []).append(robe)
    layout_rows, render_rows = [], []
    for index, (mapping, roots) in enumerate(sorted(layouts.items(), key=lambda item: item[1][0])):
        layout_rows.extend((robe, index) for robe in roots)
        render_rows.extend((index, source, name) for source, name in mapping)
    layout_table = '2DA V2.0\n\n    BODY LAYOUT\n' + ''.join(
        f'{i} {robe} {layout}\n' for i, (robe, layout) in enumerate(sorted(layout_rows)))
    render_table = '2DA V2.0\n\n    LAYOUT SOURCE RENDER\n' + ''.join(
        f'{i} {layout} {source} {name}\n' for i, (layout, source, name) in enumerate(render_rows))
    for relative in ('sw_2da/roberender.2da', 'sw_2da/phenotype.2da', 'hakbuilder.json'):
        inputs[relative] = digest((root / relative).read_bytes())
    manifest = {'models': dict(sorted(models.items())), 'retired': sorted(retired),
                # --check re-plans from the sources; one digest records what this plan read.
                'inputs': {'count': len(inputs), 'sha256': hashlib.sha256(
                    json.dumps(sorted(inputs.items())).encode()).hexdigest()},
                'implementation': {relative: source_digest(root / relative) for relative in IMPLEMENTATION},
                'tables': {LAYOUTS: digest(layout_table.encode()), RENDERS: digest(render_table.encode())}}
    return outputs, {LAYOUTS: layout_table, RENDERS: render_table}, manifest


def dump(manifest):
    """One line per alias keeps the manifest small and its diffs readable."""
    models = [f'    {json.dumps(name)}: {json.dumps(record)}' for name, record in manifest['models'].items()]
    rest = json.dumps({key: value for key, value in manifest.items() if key != 'models'}, indent=2)
    return '{\n  "models": {\n' + ',\n'.join(models) + '\n  },\n' + rest[2:] + '\n'


def run(root=ROOT, apply=False):
    root = Path(root)
    outputs, tables, manifest = plan(root)
    failures = []
    for name in manifest['retired']:
        for directory in set(DIRECTORIES.values()):
            path = root / directory / f'{name}.mdl'
            if apply:
                path.unlink(missing_ok=True)
            elif path.exists():
                failures.append(f'retired {name}')
    for relative, data in outputs.items():
        path = root / relative
        if apply:
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
        elif not path.exists() or path.read_bytes() != data:
            failures.append(relative)
    texts = {**tables, MANIFEST: dump(manifest)}
    for relative, text in texts.items():
        path = root / relative
        if apply:
            path.write_text(text, encoding='utf8', newline='\n')
        elif not path.is_file() or path.read_text() != text:
            failures.append(relative)
    if failures:
        raise ValueError(f'Stale part skin assets: {failures[:20]} ({len(failures)} total); '
                         'run GeneratePartSkinModels.py --apply')
    print(f'Validated {len(outputs)} part skin aliases for '
          f'{len(tables[LAYOUTS].splitlines()) - 3} robe bodies, {sum(map(len, outputs.values())):,} bytes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    run(apply=args.apply)


if __name__ == '__main__':
    main()
