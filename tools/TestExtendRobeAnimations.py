"""Guard new animation layers against changes to installed rigs or old clips."""
import unittest
import tempfile
from pathlib import Path

import RobeAnimationBanks as banks
import ExtendRobeAnimations as extend
from TestRobeAnimationBanks import NativeRobeAnimationBankTests


class ExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NativeRobeAnimationBankTests.setUpClass()
        cls.addClassCleanup(NativeRobeAnimationBankTests.doClassCleanups)
        cls.source = NativeRobeAnimationBankTests.model
        cls.old = cls.source.pack("pmh_ra001", "basebody", cls.source.clips[:2])
        cls.candidate = cls.source.pack("pmh_ra002", "basebody", cls.source.clips)

    def test_addition_keeps_the_old_bank_inherited_and_copies_only_selected_native_components(self):
        layer = extend.make_layer(self.old, self.candidate, {"clip3", "clip4"}, "pmh_ra003")
        result = banks.CompiledBridge(layer)
        self.assertEqual("pmh_ra001", result.parent)
        self.assertEqual(["clip3", "clip4"], result.clip_names)
        self.assertEqual(self.source.pack("pmh_ra001", "basebody", []),
                         result.pack("pmh_ra001", "basebody", []))
        for source, actual in zip(self.source.clips[3:], result.clips):
            self.assertEqual(source.relocated(0, "pmh_ra003"), actual.relocated(0, "pmh_ra003"))
        self.assertEqual(layer, banks.join(list(banks.split(layer).values())))

    def test_existing_or_missing_clips_cannot_be_added(self):
        for names in ({"clip0"}, {"missing"}, set()):
            with self.subTest(names=names), self.assertRaises(ValueError):
                extend.make_layer(self.old, self.candidate, names, "pmh_ra003")

    def test_changed_native_rig_is_rejected(self):
        candidate = bytearray(self.candidate)
        # Model animation scale is part of the required exact static rig.
        candidate[176:180] = b"\x00\x00\x00\x40"
        with self.assertRaises(ValueError):
            extend.make_layer(self.old, bytes(candidate), {"clip3"}, "pmh_ra003")

    def test_wearer_reparenting_preserves_every_other_native_byte(self):
        result = extend.reparent(self.old, "basebody", "pmh_ra003")
        self.assertEqual(self.old[:180], result[:180])
        self.assertEqual(self.old[244:], result[244:])
        self.assertEqual("pmh_ra003", extend.robes.mdl.supermodel(result))
        with self.assertRaises(ValueError):
            extend.reparent(self.old, "unexpected", "pmh_ra003")

    def test_packaged_candidate_is_rejoined_before_selecting_new_clips(self):
        target = NativeRobeAnimationBankTests.target
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            (stage / "binary").mkdir()
            for name, data in banks.split(self.candidate, target).items():
                (stage / "binary" / f"{name}.mdl").write_bytes(data)
            self.assertEqual(self.candidate, extend.read_candidate(stage, "pmh_ra002"))


if __name__ == "__main__":
    unittest.main()
