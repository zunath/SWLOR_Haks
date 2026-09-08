"""Regression cases for body attachments and independent garment animation."""
import unittest
import struct

import CompileModels as mdl
import RobeAnimations as anim
import RobeSkeleton as rig
import RobePoseAudit as poses
from TestRobeAnimations import model, node, clip


class IndependentRobeSkeletonTests(unittest.TestCase):
    def fixtures(self):
        body = node("rootdummy", "body") + node("arm_g", "rootdummy", "position 1 0 0") + node("hand_g", "arm_g", "position 0 0 -1")
        body_clip = clip("body", 1, node("body", "null") + node("rootdummy", "body") +
                         node("arm_g", "rootdummy", "orientationkey 2\n0 0 1 0 0\n1 0 1 0 1") +
                         node("hand_g", "arm_g", "position 0 0 -1"))
        robe = node("rootdummy", "garment") + node("arm_g", "rootdummy", "position 2 0 0\norientation 0 1 0 1")
        robe += "node skin sleeve\nparent rootdummy\nweights 1\narm_g 1\nendnode\n"
        return {"body": model("body", "null", body, body_clip), "garment": model("garment", "body", robe)}

    def build(self, fixtures):
        families = rig.Families(fixtures.get)
        key = families.add("body", fixtures["garment"])
        output, names = families.body_root("garment", "result", "bridge")
        return families, key, output.decode(), names

    def test_robe_bind_pose_cannot_displace_missing_body_hand(self):
        families, key, output, names = self.build(self.fixtures())
        nodes = {n: p for _, n, p in anim.geometry(output)}
        self.assertEqual(nodes["arm_g"]["position"], ["1", "0", "0"])
        self.assertEqual(nodes["hand_g"]["parent"], ["arm_g"])
        self.assertEqual(nodes[names["arm_g"]]["position"], ["2", "0", "0"])
        self.assertEqual(nodes[names["sleeve"]]["weights"], [[names["arm_g"], "1"]])
        self.assertNotEqual(nodes[names["arm_g"]]["parent"], ["rootdummy"])

    def test_overlay_cannot_replace_wearer_arm_track(self):
        fixtures = self.fixtures()
        overlay = clip("coat", 2, node("coat", "null") + node("rootdummy", "coat") +
                       node("arm_g", "rootdummy", "orientationkey 2\n0 1 0 0 1\n2 1 0 0 2"))
        fixtures["coat"] = model("coat", "body", node("rootdummy", "coat") + node("arm_g", "rootdummy"), overlay)
        fixtures["garment"] = fixtures["garment"].replace("setsupermodel garment body", "setsupermodel garment coat")
        families, key, _, names = self.build(fixtures)
        bridge = families.bridge(key, "bridge").decode()
        animation = next(anim.ANIMATION.finditer(bridge))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertEqual(tracks["arm_g"]["orientationkey"][0][1:], ["0", "1", "0", "0"])
        self.assertEqual(tracks[names["arm_g"]]["orientationkey"][0][1:], ["1", "0", "0", "1"])
        self.assertEqual(float(tracks[names["arm_g"]]["orientationkey"][-1][0]), 1)
        self.assertIn("position", tracks["hand_g"])

    def test_different_garment_hierarchies_get_distinct_animation_paths(self):
        fixtures = self.fixtures()
        fixtures["other"] = fixtures["garment"].replace("garment", "other").replace("parent rootdummy\nposition 2", "parent other\nposition 2")
        families = rig.Families(fixtures.get)
        first = families.add("body", fixtures["garment"])
        second = families.add("body", fixtures["other"])
        self.assertEqual(first, second)
        _, left = families.body_root("garment", "result", "bridge")
        _, right = families.body_root("other", "second", "bridge")
        self.assertNotEqual(left["arm_g"], right["arm_g"])

    def test_unresolved_source_uses_body_clips_and_records_the_fallback(self):
        fixtures = self.fixtures()
        fixtures["garment"] = fixtures["garment"].replace("setsupermodel garment body", "setsupermodel garment missing")
        families, key, _, _ = self.build(fixtures)
        self.assertEqual(families.fallbacks, {"garment": ["missing"]})
        self.assertIn("newanim walk", families.bridge(key, "bridge").decode())

    def test_local_small_body_translations_are_not_scaled_twice(self):
        fixtures = self.fixtures()
        fixtures["body"] = fixtures["body"].replace("beginmodelgeom", "setanimationscale 0.5\nbeginmodelgeom")
        families, key, _, names = self.build(fixtures)
        animation = next(anim.ANIMATION.finditer(families.bridge(key, "bridge").decode()))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertEqual([float(v)*.5 for v in tracks["hand_g"]["position"]], [0, 0, -1])

    def test_native_body_track_ids_take_precedence_over_helper_names(self):
        fixtures = self.fixtures()
        families = rig.Families(fixtures.get, lambda base, owner, clip: {"hand_g": "hand_g"})
        key = families.add("body", fixtures["garment"])
        _, names = families.body_root("garment", "result", "bridge")
        animation = next(anim.ANIMATION.finditer(families.bridge(key, "bridge").decode()))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertNotIn("orientationkey", tracks["arm_g"])
        self.assertIn("orientationkey", tracks[names["arm_g"]])

    def test_resource_name_can_differ_from_internal_model_name(self):
        fixtures = self.fixtures()
        fixtures["export"] = fixtures.pop("garment")
        families = rig.Families(fixtures.get)
        key = families.add("body", fixtures["export"], "export")
        output, _ = families.body_root("export", "result", "bridge")
        self.assertIn(b"rm_sleeve", output)
        self.assertIn(b"newanim walk", families.bridge(key, "bridge"))

    def test_single_time_zero_keys_preserve_static_values_and_animated_curves(self):
        fixtures = self.fixtures()
        fixtures["body"] = fixtures["body"].replace("position 0 0 -1", "positionkey 1\n0 0 0 -1\nscalekey 1\n0 1.25")
        families, key, _, names = self.build(fixtures)
        animation = next(anim.ANIMATION.finditer(families.bridge(key, "bridge").decode()))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertEqual(tracks["hand_g"]["position"], ["0", "0", "-1"])
        self.assertEqual(tracks["hand_g"]["scale"], ["1.25"])
        self.assertNotIn("scalekey", tracks["hand_g"])
        self.assertEqual(len(tracks["arm_g"]["orientationkey"]), 2)

    def test_native_tiny_rotation_survives_decompiler_rounding(self):
        data = bytearray(512)
        struct.pack_into("<III", data, 0, 0, 500, 0)
        struct.pack_into("<I", data, 84, 200)
        data[244:249] = b"joint"
        struct.pack_into("<II", data, 296, 400, 1)
        struct.pack_into("<I", data, 308, 440)
        struct.pack_into("<IHHHB", data, 412, 20, 1, 0, 1, 4)
        struct.pack_into("<5f", data, 452, 0, .0002, 0, 0, 1)
        recovered = poses.accurate_rotations(node("joint", "null", "orientation 0 0 0 0"), bytes(data))
        rotation = mdl.parse_nodes(recovered)[0][2]["orientation"]
        self.assertTrue(mdl.equivalent_quaternion(mdl.quaternion(rotation), (.0002, 0, 0, 1)))
        self.assertGreater(abs(float(rotation[3])), .0003)

    def test_duplicate_helpers_keep_distinct_bind_pose_references(self):
        source = object.__new__(poses.Model)
        source.nodes = [("root", None, 0, {}, 0),
                        ("helper", 0, -1, {8: ((0,), [(1, 0, 0)])}, 0),
                        ("helper", 0, -1, {8: ((0,), [(-1, 0, 0)])}, 0)]
        result = source.pose()
        self.assertEqual(result["helper"][0], (1, 0, 0))
        self.assertEqual(result["helper_copy2"][0], (-1, 0, 0))


if __name__ == "__main__":
    unittest.main()
