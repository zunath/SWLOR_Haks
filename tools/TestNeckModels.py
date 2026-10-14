"""Audit every neck binding, including negative controls for the reported styles."""
import argparse
import json
import math
from pathlib import Path
import struct

import GenerateTintMapAssets as tint
from NeckSkinBindings import body_model, mapping_for, skins
from RobePoseAudit import Model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(args.game_data)
    manifest = json.loads((root / 'tools/NeckModels.json').read_text())
    models, bodies, external = {}, {}, {}
    def load(name):
        if name not in models:
            models[name] = active[name].read_bytes() if name in active else tint.extract_stock_bif_resource(*stock[name])
        return models[name]
    def body(name):
        if name not in bodies:
            bodies[name] = body_model(load(name))
        return bodies[name]
    # Validate every active authored neck, including local skins and every body type.
    sources = [n for n in active if '_neck' in n and n not in manifest['aliases']]
    for name in sources:
        external[name] = skins(load(name))
    for name, entry in manifest['aliases'].items():
        original = load(entry['source'])
        output = active[name].read_bytes()
        old, new = Model(original, False), Model(output, False)
        assert original[12 + old.uint(4):] == output[12 + new.uint(4):], (name, 'raw geometry changed')
        old_skins, new_skins = skins(original), skins(output)
        assert len(old_skins) == len(new_skins) == len(entry['mapping']), name
        # Everything in the preexisting model section except the names and relocated
        # skin tables must be identical (materials, topology, controllers, UV metadata).
        expected = bytearray(original[:12 + old.uint(4)])
        actual = bytearray(output[:len(expected)])
        for start, size in [(4, 4), (20, 64), (old.nodes[0][4] + 32, 32)]:
            actual[start:start + size] = expected[start:start + size]
        for (skin, _), mapping in zip(old_skins, entry['mapping']):
            for start, size in [(skin + 20, 36), (skin + 64, 34)]:
                actual[start:start + size] = expected[start:start + size]
            for slot, before, after in mapping:
                assert struct.unpack_from('<h', output, skin + 64 + slot * 2)[0] == after, name
                for pointer, size in [(28, 16), (40, 12)]:
                    a = 12 + old.uint(skin + pointer) + before * size
                    b = 12 + new.uint(skin + pointer) + after * size
                    assert original[a:a + size] == output[b:b + size], (name, 'inverse bind changed')
        assert actual == expected, (name, 'unrelated model fields changed')
    coverage = set()
    extents = {}
    for name in manifest['aliases']:
        data = active[name].read_bytes()
        model = Model(data, False)
        extents[name] = min(model.uint(skin + 24) for skin, _ in skins(data))
    rows = (root / 'sw_2da/neckrender.2da').read_text().splitlines()[3:]
    for row in rows:
        _, target, source, alias = row.split()
        mapping = mapping_for(load(source), body(source.split('_')[0]), body(target), external[source])
        assert json.loads(json.dumps(mapping)) == manifest['aliases'][alias]['mapping'], (target, source)
        assert extents[alias] >= len(body(target).nodes), (alias, 'bone array shorter than body traversal')
        coverage.add(target[:3])
    assert {'pfh', 'pmh', 'pfe', 'pme', 'pfd', 'pmd', 'pfo', 'pmo'} <= coverage, coverage
    # The four photographed styles must fail without rebinding on the photographed
    # female/robe187 root. Correct bindings must still follow canonical head/neck motion.
    for style in (257, 260, 264, 267):
        source = f'pfh0_neck{style}'
        target = 'pfh110'
        mapping = mapping_for(load(source), body('pfh0'), body(target))
        assert any(a != b for mesh in mapping for _, a, b in mesh), source
        reference, generated = body('pfh0'), body(target)
        before, after = reference.pose(), generated.pose()
        for mesh in mapping:
            for _, a, b in mesh:
                bone = reference.nodes[a][0]
                assert generated.nodes[b][0] == bone, (source, bone)
                assert math.dist(before[bone][0], after[bone][0]) < 2e-5, (source, bone)
    assert 'pfd0_neck159' in sources
    assert skins(load('pfd0_neck159')), 'The repaired dwarf neck must retain a valid external skin'
    print(f'PASS: {len(sources)} authored necks, {len(manifest["aliases"])} unchanged meshes/materials, '
          f'{len(rows)} skeleton/style bindings; all four screenshot negative controls.')


if __name__ == '__main__':
    main()
