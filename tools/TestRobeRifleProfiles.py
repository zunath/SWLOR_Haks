"""Native RF layers must preserve cloth, source float bytes and external body part IDs."""
import math
import struct
import unittest

import FillRobeEmoteMotion as native
import ModelLodAssets as identity
import RobeAnimationBanks as banks
import RobeRifleProfiles as owner
import RobePoseAudit as poses
from TestRobeAnimationBanks import NativeRobeAnimationBankTests


class RobeRifleProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NativeRobeAnimationBankTests.setUpClass.__func__(cls)
        original = banks.CompiledBridge(cls.original)
        header, events, root = native.read_clip(original.clips[0])
        changed = bytearray(header)
        changed[8:72] = b"xbowr".ljust(64, b"\0")
        template = native.write_clip(bytes(changed), events, root, original.name)
        arm_before = owner.nodes(root)["arm"]
        part_header = bytearray(arm_before.header)
        struct.pack_into("<i", part_header, 28, 200)
        arm_before.header = bytes(part_header)
        template = native.write_clip(bytes(changed), events, root, original.name)
        before = original.pack(original.name, original.parent, [template])
        before_data = bytearray(before)
        arm_offset = next(node[4] for node in poses.Model(before, False).nodes if node[0] == "arm")
        struct.pack_into("<i", before_data, arm_offset + 28, 200)
        struct.pack_into("<I", before_data, 88, 201)
        cls.before = bytes(before_data)
        header, events, root = native.read_clip(template)
        changed = bytearray(header)
        changed[8:72] = b"rf1_101_i".ljust(64, b"\0")
        arm = owner.nodes(root)["arm"]
        numbered = bytearray(arm.header)
        struct.pack_into("<i", numbered, 28, 200)
        arm.header = bytes(numbered)
        arm.keys = struct.pack("<IHHHBx", 20, 2, 0, 2, 4)
        arm.values = struct.pack("<10f", 0, 1, 0, 0, 0, 1, 0, 0,
                                 math.sin(math.pi / 4), math.cos(math.pi / 4))
        cls.arm_keys, cls.arm_values = arm.keys, arm.values
        def remove_cloth(node):
            node.children = [child for child in node.children if child.name != "rg_tail"]
            for child in node.children:
                remove_cloth(child)
        remove_cloth(root)
        fitted = native.write_clip(bytes(changed), events, root, original.name)
        base = identity.rename_identity((cls.stage / "basebody.mdl").read_bytes(), "a_rfl_pmh0")
        body_data = bytearray(base)
        arm_offset = next(node[4] for node in poses.Model(base, False).nodes if node[0] == "arm")
        struct.pack_into("<i", body_data, arm_offset + 28, 200)
        struct.pack_into("<I", body_data, 88, 201)
        cls.native_body = bytes(body_data)
        seed = banks.CompiledBridge(cls.native_body)
        cls.body = seed.pack(seed.name, "null", [fitted])

    def test_layer_keeps_own_cloth_and_exact_source_controllers_and_external_ids(self):
        layer, proof = owner.build_layer(self.before, self.body, "pmh_ra002", native_body=self.native_body)
        model = banks.CompiledBridge(layer)
        self.assertEqual("pmh_ra001", model.parent)
        self.assertEqual(["rf1_101_i"], model.clip_names)
        self.assertEqual(1, proof["addedClips"])
        prior = banks.CompiledBridge(self.before)
        self.assertEqual(prior.pack(prior.name, prior.parent, []),
                         model.pack(prior.name, prior.parent, []))
        _, _, fitted = native.read_clip(model.clips[0])
        _, _, original = native.read_clip(prior.clips[0])
        actual, before = owner.nodes(fitted), owner.nodes(original)
        self.assertEqual(200, struct.unpack_from("<i", actual["arm"].header, 28)[0])
        self.assertEqual(self.arm_keys, actual["arm"].keys)
        self.assertEqual(self.arm_values, actual["arm"].values)
        self.assertEqual(before["rg_tail"].keys, actual["rg_tail"].keys)
        self.assertEqual(before["rg_tail"].values, actual["rg_tail"].values)
        expected_header, expected_events, _ = native.read_clip(banks.CompiledBridge(self.body).clips[0])
        actual_header, actual_events, _ = native.read_clip(model.clips[0])
        self.assertEqual(expected_header[112:120], actual_header[112:120])
        self.assertEqual(expected_events, actual_events)

    def test_retained_layer_resolves_own_template_from_unchanged_robe_ancestor(self):
        original = banks.CompiledBridge(self.before)
        intermediate = original.pack("pmh_ra002", original.name, [])
        result, _ = owner.build_layer(intermediate, self.body, "pmh_ra003",
                                      lambda name: self.before if name == original.name else None, native_body=self.native_body)
        model = banks.CompiledBridge(result)
        self.assertEqual("pmh_ra002", model.parent)
        self.assertEqual(["rf1_101_i"], model.clip_names)
        with self.assertRaisesRegex(ValueError, "ancestor loader"):
            owner.build_layer(intermediate, self.body, "pmh_ra003", native_body=self.native_body)

    def test_inherited_template_rejects_changed_native_rig_metadata(self):
        original = banks.CompiledBridge(self.before)
        intermediate = original.pack("pmh_ra002", original.name, [])
        corrupt = bytearray(self.before)
        corrupt[12 + 104] ^= 1
        with self.assertRaisesRegex(ValueError, "owned skeleton"):
            owner.build_layer(intermediate, self.body, "pmh_ra003", lambda _: bytes(corrupt), native_body=self.native_body)

    def test_numeric_body_binding_survives_different_source_joint_labels(self):
        changed = bytearray(self.body)
        model = poses.Model(self.body)
        offsets = [node[4] for node in model.nodes if node[0] == "arm"]
        offsets += [node[4] for _, nodes in model.clips.values() for node in nodes if node[0] == "arm"]
        for offset in offsets:
            changed[offset + 32:offset + 64] = b"source_arm".ljust(32, b"\0")
        layer, _ = owner.build_layer(self.before, bytes(changed), "pmh_ra002",
                                     native_body=self.native_body)
        _, _, root = native.read_clip(banks.CompiledBridge(layer).clips[0])
        actual = owner.nodes(root)
        self.assertNotIn("source_arm", actual)
        self.assertEqual(self.arm_values, actual["arm"].values)
        self.assertEqual(200, struct.unpack_from("<i", actual["arm"].header, 28)[0])

    def test_foreign_body_is_rejected(self):
        other = identity.rename_identity(self.body, "a_rfl_pfe0")
        with self.assertRaisesRegex(ValueError, "body differ"):
            owner.build_layer(self.before, other, "pmh_ra002", native_body=self.native_body)


if __name__ == "__main__":
    unittest.main()
