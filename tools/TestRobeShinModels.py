"""Robe 236 must retain Miris's authored shin meshes on the correct leg bones."""
import struct
import unittest

import GenerateRobeShinModels as shins
from ModelLodAssets import ModelPath, model_name
from NeckSkinBindings import body_model, skins
from RobePoseAudit import Model


class RobeShinModelsTests(unittest.TestCase):
    def test_checked_in_models_match_current_body_and_sources(self):
        for path, expected in shins.outputs():
            with self.subTest(model=path.name):
                self.assertEqual(path.read_bytes(), expected)

    def test_mesh_weights_and_materials_are_unchanged(self):
        for path, data in shins.outputs():
            with self.subTest(model=path.name):
                side = path.parent.name.removeprefix('sw_pt_')[0]
                original = ModelPath(path.parent / f'pmh0_shin{side}161.mdl').read_bytes()
                old_raw = 12 + struct.unpack_from('<I', original, 4)[0]
                new_raw = 12 + struct.unpack_from('<I', data, 4)[0]
                self.assertEqual(original[old_raw:], data[new_raw:])
                self.assertEqual(model_name(data), path.stem)
                old, new = Model(original, False), Model(data, False)
                self.assertEqual(len(old.nodes), len(new.nodes))
                for before, after in zip(old.nodes[1:], new.nodes[1:]):
                    self.assertEqual(before[:4], after[:4])
                    if old.uint(before[4] + 108) & 32:
                        self.assertEqual(original[before[4] + 200:before[4] + 264],
                                         data[after[4] + 200:after[4] + 264])

    def test_external_skin_slots_follow_same_named_leg_bones(self):
        donor = body_model(ModelPath(shins.ROOT / 'sw_pt_root/pmh0.mdl').read_bytes())
        wearer = body_model(ModelPath(shins.ROOT / 'sw_pt_root/pmh200.mdl').read_bytes())
        for path, data in shins.outputs():
            side = path.parent.name.removeprefix('sw_pt_')[0]
            original = ModelPath(path.parent / f'pmh0_shin{side}161.mdl').read_bytes()
            with self.subTest(model=path.name):
                old_skins, new_skins = skins(original), skins(data)
                self.assertEqual(len(old_skins), len(new_skins))
                moved = False
                for (old_offset, old_slots), (new_offset, new_slots) in zip(old_skins, new_skins):
                    for (old_slot, old_bone), (slot, bone) in zip(old_slots, new_slots):
                        self.assertEqual(old_slot, slot)
                        self.assertEqual(donor.nodes[old_bone][0], wearer.nodes[bone][0])
                        self.assertIn(wearer.nodes[bone][0], (f'{side}shin_g', f'{side}foot_g'))
                        moved |= bone != old_bone
                        for field, size in ((28, 16), (40, 12)):
                            old_start = 12 + struct.unpack_from('<I', original, old_offset + field)[0] + old_bone * size
                            new_start = 12 + struct.unpack_from('<I', data, new_offset + field)[0] + bone * size
                            self.assertEqual(original[old_start:old_start + size], data[new_start:new_start + size])
                self.assertTrue(moved)


if __name__ == '__main__':
    unittest.main()
