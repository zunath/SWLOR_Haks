"""Every playable body supports rifles under the barrel, where the male human's target is."""
from pathlib import Path
import unittest

import numpy as np

import CompileModels as mdl
import GenerateRifleHoldAnimations as rifle
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
# Fitted rifles' barrel axis at the support hand (weapon-space x), and how far the
# centre of the open palm may sit from it. The stock poses left it 4.5 cm (idle) to 8 cm (ready) off it.
BARREL_AXIS_X = -0.005
PALM_CENTRE_TOLERANCE = 0.01


class RifleHoldAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = rifle.Library(None)

    def test_each_body_root_inherits_its_rifle_bank(self):
        for body in rifle.BODIES:
            with self.subTest(body=body):
                root = (ROOT / rifle.ROOT_DIRECTORY / f"{body}.mdl").read_bytes()
                self.assertEqual(rifle.bank_name(body), mdl.supermodel(root))
                bank = Model((ROOT / rifle.BANK_DIRECTORY / f"{rifle.bank_name(body)}.mdl").read_bytes())
                self.assertEqual(set(rifle.CLIPS), set(bank.clips))
                self.assertNotIn(bank.parent, ("", rifle.bank_name(body)))

    def test_the_reference_support_palm_sits_under_the_barrel(self):
        palm_points = rifle.hand_points(self.library, rifle.REFERENCE, "l")
        for clip in ("xbowr", "xbowrdy"):
            with self.subTest(clip=clip):
                _, palm_frame = rifle.body_rig(self.library, rifle.REFERENCE, clip).hand_frames(0.0)
                palm = rifle.placed(palm_frame, palm_points)
                # The lower half is the palm itself; the upper half is the raised thumb side.
                body = palm[palm[:, 1] < np.median(palm[:, 1])]
                self.assertLess(abs(body[:, 0].mean() - BARREL_AXIS_X), PALM_CENTRE_TOLERANCE)

    def test_banks_only_rotate_bones(self):
        for body in rifle.BODIES:
            bank = Model((ROOT / rifle.BANK_DIRECTORY / f"{rifle.bank_name(body)}.mdl").read_bytes())
            for clip, (_, nodes) in bank.clips.items():
                for name, _, _, controllers, _ in nodes:
                    with self.subTest(body=body, clip=clip, node=name):
                        self.assertNotIn(36, controllers)
                        if name != "rootdummy":
                            self.assertNotIn(8, controllers)

    def test_banks_repeat_inherited_clips_exactly(self):
        # The engine maps tracks by native part ID and robe bridges copy them by name,
        # hashing garment tracks exactly; only the support arm may differ.
        for body in rifle.BODIES:
            for clip in rifle.CLIPS:
                with self.subTest(body=body, clip=clip):
                    bank = self.library.clip(body, clip)[1].clips[clip][1]
                    self.assertNotIn(8, next(n[3] for n in bank if n[0] == rifle.HOOK))
                    try:
                        _, source = self.library.clip(body, clip, self.library.inherited_parent(body))
                    except RuntimeError:
                        continue  # The inherited clip lives in stock data.
                    inherited = source.clips[clip][1]
                    self.assertEqual([n[:3] for n in inherited[1:]], [n[:3] for n in bank[1:]])
                    for ours, theirs in zip(bank[1:], inherited[1:]):
                        if ours[0] not in rifle.ARM:
                            self.assertEqual(theirs[3], ours[3], ours[0])

    def test_support_hand_meets_the_reference_grip(self):
        for body in rifle.BODIES:
            for clip in ("xbowr", "xbowrdy"):
                with self.subTest(body=body, clip=clip):
                    _, palm = rifle.placement_error(self.library, body, clip)
                    self.assertLess(np.linalg.norm(palm), 0.003)

    def test_support_hand_follows_the_rifle_move_of_each_body(self):
        self.assertEqual(set(rifle.WEAPON_MOVES) - set(rifle.BODIES), set())
        self.assertNotIn(rifle.REFERENCE, rifle.WEAPON_MOVES)
        for body in rifle.BODIES:
            if body.endswith("2"):
                self.assertNotIn(body, rifle.WEAPON_MOVES, "large phenotypes get no rifle move from the server")
            self.assertLess(np.linalg.norm(rifle.weapon_move(body)), 0.04)

    def robe_bank_sets(self):
        """(head, body, joined bank bytes) of every robe animation bank set built for a rifle body."""
        import json
        import GenerateRobeRgbModels as generator
        manifest = json.loads((ROOT / "tools" / "RobeRgbModels.json").read_bytes())
        active = generator.fresh_active_models()
        bases = {}
        for key, head in manifest["animation_bridges"].items():
            bases.setdefault(head, set()).add(key.split("/")[0])
        for head in sorted(manifest["animation_bank_sets"]):
            body = next(iter(bases.get(head, ())), None)
            if len(bases.get(head, ())) == 1 and body in rifle.BODIES:
                yield head, body, generator.owned_animation_bytes(head, manifest, active)

    def test_robe_banks_carry_each_bodys_support_arm(self):
        checked = 0
        for head, body, bridge in self.robe_bank_sets():
            bank = self.library.data(rifle.bank_name(body))
            for clip in rifle.CLIPS:
                ours, theirs = rifle.arm_rotations(bridge, clip), rifle.arm_rotations(bank, clip)
                for joint in rifle.ARM:
                    with self.subTest(head=head, clip=clip, joint=joint):
                        (rows, _, values, _), (bank_rows, _, bank_values, _) = ours[joint], theirs[joint]
                        self.assertEqual(bank_rows, rows)
                        a = np.frombuffer(bridge[values:values + rows * 16], "<f4")
                        b = np.frombuffer(bank[bank_values:bank_values + rows * 16], "<f4")
                        self.assertLess(np.abs(a - b).max(), 1e-6)
                        checked += 1
        self.assertGreater(checked, 0)

    def test_a_base_robe_bank_is_preserved_only_up_to_recorded_arm_rotations(self):
        import ExtendRobeAnimations as extend
        _, _, bridge = next(self.robe_bank_sets())
        record = {"base_rotation_revisions": [{"clips": list(rifle.CLIPS), "joints": list(rifle.ARM)}]}
        self.assertTrue(extend.base_bank_preserved(bridge, bridge, {}))
        _, _, values, _ = rifle.arm_rotations(bridge, "xbowr")["lhand_g"]
        turned = bytearray(bridge)
        turned[values] ^= 1
        self.assertFalse(extend.base_bank_preserved(bridge, bytes(turned), {}))
        self.assertTrue(extend.base_bank_preserved(bridge, bytes(turned), record))
        turned[200] ^= 1
        self.assertFalse(extend.base_bank_preserved(bridge, bytes(turned), record))


if __name__ == "__main__":
    unittest.main()
