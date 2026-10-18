"""Robe 236's generated skirts must hang from their coat helpers like the native robe."""
import json
from pathlib import Path
import unittest

import RobeAnimations as anim
import RobePoseAudit as poses
import RobeSkirtBindings as skirts

ROOT = Path(__file__).resolve().parents[1]


class RobeSkirtBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = json.loads((ROOT/'tools/RobeRgbModels.json').read_text())['garment_node_names']

    def cases(self):
        for source_name in sorted(skirts.SOURCES):
            name = source_name[:3] + '200'
            source = (ROOT/'sw_pt_robe'/f'{source_name}.mdl').read_bytes()
            wearer = (ROOT/'sw_pt_root'/f'{name}.mdl').read_bytes()
            yield name, source, wearer, self.names[name]

    def test_all_eight_normal_body_roots_use_the_native_coat_helper_bindings(self):
        rows = [line.split() for line in (ROOT/'sw_2da/roberender.2da').read_text().splitlines()[3:] if line.strip()]
        self.assertEqual(skirts.SOURCES, {row[1] for row in rows if row[1] in skirts.SOURCES and row[2:] == ['200', '0']})
        for name, source, wearer, names in self.cases():
            with self.subTest(model=name):
                native = poses.Model(source, False).skin_bindings()
                bindings = poses.Model(wearer, False).skin_bindings()
                for panel in skirts.PANELS:
                    bones = set(bindings[names[panel]])
                    self.assertEqual({names[bone] for bone in native[panel]}, bones)
                    self.assertTrue(any(bone.startswith('rg_coat_') for bone in bones))
                    self.assertFalse({'pelvis_g', 'lthigh_g', 'rthigh_g', 'lshin_g', 'rshin_g'} & bones)
                self.assertEqual(wearer, skirts.restore_native_skirts(source, wearer, names))

    def test_coat_helpers_are_animated_by_the_engine(self):
        """Every weighted helper must sit below numbered joints, or the engine never moves it."""
        for name, _, wearer, names in self.cases():
            with self.subTest(model=name):
                model = poses.Model(wearer, False)
                parts = anim.binary_parts(wearer)
                path_of = {}
                for index, (node, parent, *_) in enumerate(model.nodes):
                    path_of[index] = () if parent is None else (*path_of[parent], node)
                bindings = model.skin_bindings()
                indices = {node: index for index, (node, *_) in enumerate(model.nodes)}
                for panel in skirts.PANELS:
                    for bone in bindings[names[panel]]:
                        path = path_of[indices[bone]]
                        if not bone.startswith('rg_coat_'):
                            continue
                        self.assertGreaterEqual(parts[path], 0, bone)
                        for depth in range(1, len(path)):
                            ancestor = path[:depth]
                            if ancestor[-1].startswith('rg_'):
                                self.assertGreaterEqual(parts[ancestor], 0, '/'.join(ancestor))


if __name__ == '__main__':
    unittest.main()
