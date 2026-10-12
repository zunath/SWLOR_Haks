"""Point-pistol must raise the main hand in every humanoid clothing family."""
import json
from pathlib import Path
import unittest

from GenerateRobeRgbModels import file_digest, owned_animation_bytes
from RobePoseAudit import Model
from RobeAnimations import validate_animation_parts
from RobeAnimationBanks import CompiledBridge


class PointPistolAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.model = Model((cls.root / 'sw_cr_creature/a_ba_med_weap.mdl').read_bytes())

    def assert_main_hand_aim(self, model):
        # Native animation 79 (PointPistol) plays custom57, not custom59.
        pose = model.pose(model.clips['custom57lp'])
        right = pose['rhand'][0]
        left = pose['lhand'][0]
        clearance = pose['head_g'][0][2] * .2
        self.assertGreater(right[2], left[2] + clearance, 'the pistol hand must be raised above the idle offhand')
        self.assertGreater(right[1], left[1] + clearance, 'the pistol hand must extend in front of the body')

    def test_native_pose_raises_the_right_hand(self):
        self.assert_main_hand_aim(self.model)
        validate_animation_parts(self.model.data)

    def test_every_clothing_copy_aims_with_the_main_hand(self):
        count = 0
        for folder in ('sw_anim_m', 'sw_anim_f'):
            for path in (self.root / folder).glob('*.mdl'):
                data = path.read_bytes()
                if b'custom57lp\0' not in data.lower():
                    continue
                model = Model(data)
                count += 1
                with self.subTest(bank=path.name):
                    self.assert_main_hand_aim(model)
                    validate_animation_parts(data)
                    # Bank repacking requires each node to own contiguous controller arrays.
                    bank = CompiledBridge(data)
                    self.assertEqual(data, bank.pack(bank.name, bank.parent, bank.clips))
                    for phase in ('start', 'lp'):
                        name = 'custom57' + phase
                        length, nodes = model.clips[name]
                        self.assertAlmostEqual(length, self.model.clips[name][0], places=5)
                        reference = {n[0]: n[3] for n in self.model.clips[name][1]}
                        for node, _, _, controllers, _ in nodes:
                            if node not in reference or 20 not in reference[node]:
                                continue
                            expected_times, expected_values = reference[node][20]
                            times, values = controllers[20]
                            self.assertEqual(expected_times, times)
                            for expected, actual in zip(expected_values, values, strict=True):
                                self.assertAlmostEqual(abs(sum(a * b for a, b in zip(expected, actual))),
                                                       1, places=5, msg=f'{name}/{node}')
        self.assertEqual(count, 14, 'all seven male and seven female animation families must be checked')

    def test_manifest_tracks_the_changed_animation_sources(self):
        manifest = json.loads((self.root / 'tools/RobeRgbModels.json').read_text())
        source = 'sw_cr_creature/a_ba_med_weap.mdl'
        self.assertEqual(manifest['files'][source], file_digest(self.root / source))
        for name in manifest['animation_bank_sets']:
            with self.subTest(bank=name):
                owned_animation_bytes(name, manifest, {})


if __name__ == '__main__':
    unittest.main()
