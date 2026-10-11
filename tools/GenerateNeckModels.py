#!/usr/bin/env python3
"""Generate shared, compiled neck skins for altered body skeletons.

The server projects the neck only while writing appearance packets. Saved armor
and editor style numbers stay canonical. Equal used-bone layouts share one alias.
Run with --game-data <NWN/data> --apply, or --check to validate the entire catalog.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import GenerateTintMapAssets as tint
from ModelLodAssets import content
from NeckSkinBindings import body_model, mapping_for, needs_remap, remap, skins
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools/NeckModels.json'
TABLE = ROOT / 'sw_2da/neckrender.2da'
PART = re.compile(r'^(p[fm][a-z])(0|2)_neck(\d{3})$')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plan(game_data):
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(game_data)
    def load(name):
        # ASCII sources hash and parse identically under any Git line-ending setting.
        if name in active:
            return content(active[name].read_bytes())
        return content(tint.extract_stock_bif_resource(*stock[name]))
    # Include every authored neck, not merely the current editor's choices.
    parts = {}
    inputs = {}
    for name, path in sorted(active.items()):
        match = PART.fullmatch(name)
        if not match:
            continue
        data = content(path.read_bytes())
        external = skins(data)
        inputs[name] = digest(data)
        if external:
            parts[name] = (data, external)
    bodies = {}
    for name in sorted(set(stock) | set(active)):
        if not re.fullmatch(r'p[fm][a-z]\d+', name):
            continue
        # Generated robe roots and the ordinary large/mounted bodies can all
        # fall back to phenotype-0 necks. Unknown reserved phenotypes have no root.
        data = load(name)
        bodies[name] = body_model(data)
        inputs[name] = digest(data)
    defaults = {}
    for line in (ROOT / 'sw_2da/phenotype.2da').read_text().splitlines()[3:]:
        fields = line.split()
        if len(fields) >= 4 and fields[3].isdigit():
            defaults[int(fields[0])] = int(fields[3])
    groups = {}
    rows = []
    for body_name, body in bodies.items():
        prefix = body_name[:3]
        phenotype = int(body_name[3:])
        if phenotype == 0:
            continue
        for source, (data, external) in parts.items():
            if source[:3] != prefix:
                continue
            style = int(source.rsplit('neck', 1)[1])
            current = phenotype
            visited = set()
            resolved = None
            while current not in visited:
                visited.add(current)
                candidate = f'{prefix}{current}_neck{style:03}'
                if candidate in active or candidate in stock:
                    resolved = candidate
                    break
                current = defaults.get(current, 0)
            if resolved != source:
                continue
            donor = bodies[source.split('_', 1)[0]]
            mapping = mapping_for(data, donor, body, external)
            if not needs_remap(mapping):
                continue
            key = (source, mapping)
            groups.setdefault(key, []).append(body_name)
    # Stable allocation survives later additions; aliases are never repurposed.
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {'aliases': {}}
    aliases = dict(previous['aliases'])
    next_id = max([999, *(int(name.rsplit('neck', 1)[1]) for name in aliases)]) + 1
    by_signature = {(entry['source'], json.dumps(entry['mapping'])): name for name, entry in aliases.items()}
    outputs = {}
    for (source, mapping), roots in sorted(groups.items()):
        key = (source, json.dumps(mapping))
        name = by_signature.get(key)
        if name is None:
            if next_id > 65535:
                raise ValueError('Neck aliases exceed native armor part range')
            name = f'{source[:3]}0_neck{next_id}'
            next_id += 1
        data, external = parts[source]
        # Cover the full traversal of every root sharing the alias, including
        # unused bones. Native CPU and GPU paths can both index these tables.
        output = remap(data, name, mapping, external, max(len(bodies[root].nodes) for root in roots))
        aliases[name] = {'source': source, 'mapping': mapping, 'sha256': digest(output)}
        outputs[name] = output
        rows.extend((root, source, name) for root in roots)
    table = '2DA V2.0\n\n    BODY SOURCE RENDER\n'
    table += ''.join(f'{i} {body} {source} {render}\n' for i, (body, source, render) in enumerate(sorted(rows)))
    # Normalize text line endings so the guard also works after a Git checkout
    # using core.autocrlf. Binary model hashes remain byte-exact.
    manifest = {'aliases': dict(sorted(aliases.items())), 'inputs': dict(sorted(inputs.items())),
                'tables': {'phenotype.2da': digest((ROOT / 'sw_2da/phenotype.2da').read_text().encode()),
                           'neckrender.2da': digest(table.encode())}}
    return outputs, table, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    outputs, table, manifest = plan(args.game_data)
    failures = []
    for name, data in outputs.items():
        path = ROOT / 'sw_pt_neck' / (name + '.mdl')
        if args.apply:
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
        elif not path.exists() or path.read_bytes() != data:
            failures.append(name)
    metadata = json.dumps(manifest, indent=2) + '\n'
    for path, content in ((TABLE, table), (MANIFEST, metadata)):
        if args.apply:
            path.write_text(content, encoding='utf8', newline='\n')
        elif not path.exists() or path.read_text() != content:
            failures.append(path.name)
    if failures:
        raise SystemExit(f'Stale or missing neck resources: {failures[:20]} ({len(failures)} total)')
    print(f'Validated {len(manifest["inputs"])} source models; {len(outputs)} shared neck aliases, '
          f'{len(table.splitlines()) - 3} body/style combinations, {sum(map(len, outputs.values())):,} bytes.')


if __name__ == '__main__':
    main()
