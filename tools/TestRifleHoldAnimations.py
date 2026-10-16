"""Every playable body grips rifles where the male human does."""
from pathlib import Path
import unittest

import numpy as np

import CompileModels as mdl
import GenerateRifleHoldAnimations as rifle
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]
# The female dwarf arm is about 17 mm short of the reference support grip at the
# start of the ready clip; its IK stops at full reach.
REACH_LIMITED = {("pfd0", "xbowrdy"), ("pfd2", "xbowrdy")}


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

    def test_the_reference_body_keeps_its_inherited_hold(self):
        root = (ROOT / rifle.ROOT_DIRECTORY / f"{rifle.REFERENCE}.mdl").read_bytes()
        self.assertFalse(mdl.supermodel(root).startswith(rifle.BANK_PREFIX))

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
                    limit = 0.02 if (body, clip) in REACH_LIMITED else 0.003
                    self.assertLess(np.linalg.norm(palm), limit)


if __name__ == "__main__":
    unittest.main()
