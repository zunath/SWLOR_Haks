"""Regression coverage for externally skinned male human shoulders."""
import json
import math
from pathlib import Path
import struct
import unittest
import GenerateShoulderModels as shoulders
import GenerateTintMapAssets as tint
from NeckSkinBindings import body_model, mapping_for, needs_remap
from RobePoseAudit import Model

skins = shoulders.shoulder_skins


class ShoulderModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = shoulders.ROOT
        cls.manifest = json.loads(shoulders.MANIFEST.read_text())
        cls.active = tint.find_active_models()
        cls.donor = body_model(cls.active['pmh0'].read_bytes())
        cls.sources = {}
        cls.bodies = {}
        cls.rows = [row.split()[1:] for row in (cls.root / 'sw_2da/shoulderrender.2da').read_text().splitlines()[3:]]
        for body, source, _ in cls.rows:
            if source not in cls.sources:
                data = cls.active[source].read_bytes()
                cls.sources[source] = (data, Model(data, False), skins(data))
            if body not in cls.bodies:
                cls.bodies[body] = body_model(cls.active[body].read_bytes())

    def test_every_external_shoulder_has_a_projection_on_every_current_male_human_robe(self):
        roots = {f'pmh{row.split()[2]}'
                 for row in (self.root / 'sw_2da/roberender.2da').read_text().splitlines()[3:]
                 if row.split()[1].startswith('pmh0_robe')}
        mappings = {(body, source): render for body, source, render in self.rows}
        sources = shoulders.shoulder_sources(self.active)
        self.assertEqual({source for _, source, _ in self.rows},
                         {f'pmh0_sho{side}{style:03}'
                          for side in 'lr' for style in (6, 13, 140, 141, 185, 256, 258, 260)})
        for body in roots:
            for source, (data, external) in sources.items():
                if needs_remap(mapping_for(data, self.donor, self.bodies[body], external)):
                    self.assertIn((body, source), mappings)
                    self.assertIn(mappings[body, source], self.manifest['models'])
                else:
                    self.assertNotIn((body, source), mappings)
        self.assertTrue(all(body.startswith('pmh') and body != 'pmh0' for body, _, _ in self.rows))

    def test_all_outputs_preserve_raw_meshes_materials_controllers_and_inverse_binds(self):
        # The generator keeps aliases whose source or mapping has since changed; only the render table's are live.
        live = {render for _, _, render in self.rows}
        for name, record in self.manifest['models'].items():
            if name not in live:
                continue
            with self.subTest(model=name):
                original, old, old_skins = self.sources[record['source']]
                data = (self.root / record['path']).read_bytes()
                new = Model(data, False)
                self.assertEqual(original[12 + old.uint(4):], data[12 + new.uint(4):])
                self.assertEqual(len(old.nodes), len(new.nodes))
                expected = bytearray(original[:12 + old.uint(4)])
                actual = bytearray(data[:len(expected)])
                for start, size in [(4, 4), (20, 64), (old.nodes[0][4] + 32, 32)]:
                    actual[start:start + size] = expected[start:start + size]
                self.assertEqual(len(old_skins), len(skins(data)))
                for (skin, slots), mapped in zip(old_skins, record['mapping']):
                    for start, size in [(skin + 20, 36), (skin + 64, 34)]:
                        actual[start:start + size] = expected[start:start + size]
                    for slot, before, after in mapped:
                        self.assertIn((slot, before), slots)
                        self.assertEqual(struct.unpack_from('<h', data, skin + 64 + slot * 2)[0], after)
                        for pointer, size in [(28, 16), (40, 12)]:
                            a = 12 + old.uint(skin + pointer) + before * size
                            b = 12 + new.uint(skin + pointer) + after * size
                            self.assertEqual(original[a:a + size], data[b:b + size])
                    self.assertGreaterEqual(new.uint(skin + 24), max(len(self.bodies[body].nodes) for body, _, render in self.rows if render == name))
                self.assertEqual(expected, actual)

    def test_bindings_follow_the_same_body_joints_instead_of_shifted_traversal_indices(self):
        donor_pose = self.donor.pose()
        for body_name, source, render in self.rows:
            with self.subTest(body=body_name, model=render):
                record = self.manifest['models'][render]
                original, _, external = self.sources[source]
                body = self.bodies[body_name]
                actual = mapping_for(original, self.donor, body, external)
                self.assertEqual(json.loads(json.dumps(actual)), record['mapping'])
                self.assertTrue(any(before != after for mesh in actual for _, before, after in mesh))
                target_pose = body.pose()
                for mesh in actual:
                    for _, before, after in mesh:
                        joint = self.donor.nodes[before][0]
                        target_joint = body.nodes[after][0]
                        if before == 0:
                            self.assertEqual(after, 0)  # Root identity changes with the body resref.
                        else:
                            self.assertEqual(joint, target_joint)
                        self.assertLess(math.dist(donor_pose[joint][0], target_pose[target_joint][0]), 2e-5)

    def test_packaging_hashes_and_tint_source_scopes_are_current(self):
        shoulders.check()
        self.assertTrue(set(self.manifest['models']).isdisjoint(tint.find_tint_source_models()))


if __name__ == '__main__':
    unittest.main()
