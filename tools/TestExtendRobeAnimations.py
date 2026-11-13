"""Guard new animation layers against changes to installed rigs or old clips."""
import unittest
import tempfile
import math
import struct
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

    def changed_rotation(self):
        model = extend.poses.Model(self.candidate)
        node = next(n for n in model.clips['clip3'][1] if n[0] == 'arm')
        data = bytearray(self.candidate)
        for index in range(model.uint(node[4] + 88)):
            kind, _, _, start, _ = struct.unpack_from('<IHHHB', data, 12 + model.uint(node[4] + 84) + index * 12)
            if kind == 20:
                q = extend.poses.multiply((0, 0, math.sin(.1), math.cos(.1)), node[3][20][1][0])
                struct.pack_into('<4f', data, 12 + model.uint(node[4] + 96) + start * 4, *q)
        return bytes(data)

    def test_rotation_revision_changes_only_selected_existing_quaternion_values(self):
        changes = extend.rotation_changes(self.candidate, self.changed_rotation(), {'clip3'})
        layer = self.source.pack('pmh_ra003', 'pmh_ra001', self.source.clips[2:])
        revised, count = extend.revise_rotations(layer, changes)
        self.assertEqual(1, count)
        banks.validate_motion_revision(layer, revised, {'clip3'})
        self.assertEqual(len(layer), len(revised))
        with self.assertRaises(ValueError):
            extend.revise_rotations(revised, changes)  # A prior correction cannot compound.

    def test_rotation_revision_rejects_unselected_body_changes(self):
        with self.assertRaises(ValueError):
            extend.rotation_changes(self.candidate, self.changed_rotation(), {'clip2'})

    def test_static_rotation_can_gain_motion_without_changing_other_native_data(self):
        layer = self.source.pack('pmh_ra003', 'pmh_ra001', self.source.clips[2:])
        original = extend.poses.Model(layer)
        length, nodes = original.clips['clip3']
        arm = next(node for node in nodes if node[0] == 'arm')
        prior = arm[3][20]
        q = prior[1][0]
        moved = extend.poses.multiply((0, 0, math.sin(.2), math.cos(.2)), q)
        track = ([0., length/2, length], [q, moved, q])
        revised, count = extend.revise_rotations(layer, {'clip3': {'arm': (prior, track)}})
        self.assertEqual(1, count)
        self.assertGreater(len(revised), len(layer))
        banks.validate_motion_revision(layer, revised, {'clip3'})
        actual = extend.poses.Model(revised)
        for before_node, after_node in zip(nodes, actual.clips['clip3'][1]):
            self.assertEqual(before_node[:3], after_node[:3])
            untouched = {kind: values for kind, values in before_node[3].items() if kind != 20}
            self.assertTrue(extend.sharing.controllers_equal(untouched,
                {kind: values for kind, values in after_node[3].items() if kind != 20}))
        after_arm = next(node for node in actual.clips['clip3'][1] if node[0] == 'arm')
        self.assertTrue(extend.sharing.controllers_equal({20: track}, {20: after_arm[3][20]}))
        with self.assertRaises(ValueError):
            extend.revise_rotations(revised, {'clip3': {'arm': (prior, track)}})

    def test_actual_inherited_clip_owner_is_selected_independently_of_body_prefix(self):
        child = self.source.pack('pfd_ra003', 'pmh_ra001', [])
        resources = {'pfd_ra003': child, 'pmh_ra001': self.old,
                     'basebody': self.source.pack('pmh_ra004', 'null', [])}
        owners = extend.clip_owners('pfd_ra003', resources.get, {'clip0', 'clip1'})
        self.assertEqual({'clip0': 'pmh_ra001', 'clip1': 'pmh_ra001'}, owners)


if __name__ == "__main__":
    unittest.main()
