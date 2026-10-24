"""Chest 154 and 269 coat skirts must hang from the hips, not split between the legs."""
import hashlib
import json
import math
from pathlib import Path
import struct
import unittest

import ChestSkirtBindings as skirts

ROOT = Path(__file__).resolve().parents[1]


class ChestSkirtBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = json.loads(skirts.MANIFEST.read_text())['models']
        cls.sources = {name: skirts.source(entry['sourceBlob']) for name, entry in cls.models.items()}
        cls.current = {name: (ROOT / entry['path']).read_bytes() for name, entry in cls.models.items()}

    def weights(self, data):
        bones, _ = skirts.skeleton(data)
        return [skirts.influences(data, skin, vertex) for skin in skirts.skins(data, bones) for vertex in range(skin['count'])]

    def test_catalog_covers_every_skinned_body_of_both_styles(self):
        expected = {f'p{gender}h0_chest154' for gender in 'fm'} | {f'p{gender}{race}0_chest269' for gender in 'fm' for race in 'deho'}
        self.assertEqual(expected, set(self.models))
        for name, entry in self.models.items():
            with self.subTest(model=name):
                self.assertEqual(f'sw_pt_chest/{name}.mdl', entry['path'])
                self.assertEqual(entry['sha256'], hashlib.sha256(self.current[name]).hexdigest())
                self.assertEqual(self.current[name], skirts.build(entry))

    def test_leg_weights_move_to_the_pelvis_and_nothing_else_changes(self):
        for name in self.models:
            with self.subTest(model=name):
                rebound = 0
                for before, after in zip(self.weights(self.sources[name]), self.weights(self.current[name]), strict=True):
                    self.assertFalse(skirts.LEG_BONES & set(after))
                    legs = sum(weight for bone, weight in before.items() if bone in skirts.LEG_BONES)
                    rebound += bool(legs)
                    self.assertAlmostEqual(after.get('pelvis_g', 0.), before.get('pelvis_g', 0.) + legs, delta=2e-6)
                    others = {bone: weight for bone, weight in before.items() if bone not in skirts.LEG_BONES | {'pelvis_g'}}
                    self.assertEqual(others, {bone: weight for bone, weight in after.items() if bone != 'pelvis_g'})
                self.assertGreater(rebound, 500)

    def test_bind_shape_and_every_other_byte_are_unchanged(self):
        for name in self.models:
            with self.subTest(model=name):
                before, after = self.sources[name], self.current[name]
                error = max(math.dist(a, b) for a, b in zip(skirts.rest_positions(before), skirts.rest_positions(after), strict=True))
                self.assertLess(error, 2e-5)
                bones, _ = skirts.skeleton(before)
                tables = bytearray(len(before))
                for skin in skirts.skins(before, bones):
                    for vertex in range(skin['count']):
                        weights = skin['weights'] + vertex * (skin['stride'] or 16)
                        indices = skin['indices'] + vertex * (skin['stride'] or 8)
                        tables[weights:weights + 16] = b'\1' * 16
                        tables[indices:indices + 8] = b'\1' * 8
                self.assertEqual(len(before), len(after))
                self.assertFalse([offset for offset in range(len(before)) if before[offset] != after[offset] and not tables[offset]])

    def test_robe_body_aliases_carry_the_same_weights(self):
        aliases = json.loads((ROOT / 'tools/PartSkinModels.json').read_text())['models']
        found = set()
        for alias, entry in aliases.items():
            if entry['source'] not in self.models:
                continue
            found.add(entry['source'])
            with self.subTest(alias=alias):
                data = (ROOT / entry['path']).read_bytes()
                self.assertEqual(skin_weight_rows(self.current[entry['source']]), skin_weight_rows(data))
        self.assertEqual(set(self.models), found)


def skin_weight_rows(data):
    """Every skin's raw vertex weights, without resolving the wearer's skeleton."""
    uint = lambda offset: struct.unpack_from('<I', data, offset)[0]
    raw = 12 + uint(4)
    rows = []

    def visit(pointer):
        node = 12 + pointer
        flags = uint(node + 108)
        if flags & 0x20 and flags & 0x40:
            mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
            count, stride = struct.unpack_from('<H', data, mesh + 448)[0], uint(mesh + 440)
            weights = raw + uint(mesh + 512 + 12)
            rows.append([struct.unpack_from('<4f', data, weights + vertex * (stride or 16)) for vertex in range(count)])
        for index in range(uint(node + 76)):
            visit(uint(12 + uint(node + 72) + index * 4))

    visit(uint(84))
    return rows


if __name__ == '__main__':
    unittest.main()
