"""Regression cases for body attachments and independent garment animation."""
import unittest
import struct

import CompileModels as mdl
import RobeAnimations as anim
import RobeSkeleton as rig
import RobePoseAudit as poses
import SharedRobeFamilies as shared
from TestRobeAnimations import model, node, clip


class IndependentRobeSkeletonTests(unittest.TestCase):
    def test_garment_wearer_bones_are_static_children_at_their_retargeted_bind(self):
        for scale in (1, 0.5):
            with self.subTest(scale=scale):
                geometry = node("rootdummy", "body", "position 0 0 1.2")
                geometry += node("torso_g", "rootdummy", "position 0 0.1 0.2\norientation 1 0 0 0.3")
                sitting = clip("body", 1, node("body", "null") + node("rootdummy", "body",
                    "positionkey 2\n0 0 0 1.2\n1 0.8 0 0.4\norientation 0 1 0 0.8\nscale 1.1"))
                pointing = clip("body", 1, node("body", "null") + node("rootdummy", "body") +
                                node("torso_g", "rootdummy", "orientationkey 2\n0 0 0 1 0\n1 0 0 1 0.5"))
                fixtures = {"body": model("body", "null", geometry,
                                          sitting.replace("walk", "sitcross") + pointing.replace("walk", "custom1lp"))
                            .replace("beginmodelgeom body", f"setanimationscale {scale}\nbeginmodelgeom body")}
                for name, height in (("coat_a", 1.1), ("coat_b", 0.9)):
                    garment = node("rootdummy", name, f"position 0.05 0 {height}\norientation 0 0 1 0.1\nscale 1.02")
                    garment += node("torso_g", "rootdummy", "position 0 0.2 0.1\norientation 0 1 0 0.2")
                    garment += node("coat_tail", "torso_g", "position 0 -0.1 -0.5")
                    fixtures[name] = model(name, "body", garment)
                families = shared.Families(fixtures.get)
                keys = {name: families.add("body", fixtures[name]) for name in ("coat_a", "coat_b")}
                for name in ("coat_a", "coat_b"):
                    output, aliases = families.body_root(name, "wearer", "bridge")
                    generated = {n: p for _, n, p in anim.geometry(output.decode())}
                    for bone in ("rootdummy", "torso_g"):
                        self.assertEqual([bone], generated[aliases[bone]]["parent"])
                    self.assertEqual([aliases["torso_g"]], generated[aliases["coat_tail"]]["parent"])
                    body = {n: p for _, n, p in anim.geometry(fixtures["body"])}
                    before = rig.retargeted_binds({n: p for _, n, p in anim.geometry(fixtures[name])}, body)
                    after = rig.bind_transforms(generated)
                    body_binds = rig.bind_transforms(body)
                    # The garment rig takes the wearer's bind rotations on its own
                    # bone lengths, as the native robe does once a clip plays.
                    self.assertTrue(mdl.equivalent_quaternion(body_binds["torso_g"][1], after[aliases["torso_g"]][1]))
                    self.assertEqual(body_binds["rootdummy"][0], after[aliases["rootdummy"]][0])
                    for original in ("rootdummy", "torso_g", "coat_tail"):
                        (bp, bq, bs), (ap, aq, az) = before[original], after[aliases[original]]
                        for left, right in zip(bp, ap):
                            self.assertAlmostEqual(left, right, places=6)
                        self.assertTrue(mdl.equivalent_quaternion(bq, aq), original)
                        self.assertAlmostEqual(bs, az, places=6)
                    motions = {match[1]: {n: p for _, n, p in mdl.parse_nodes(match[3])}
                               for match in anim.ANIMATION.finditer(families.bridge(keys[name], "bridge").decode())}
                    for clip_name, tracks in motions.items():
                        for bone in ("rootdummy", "torso_g"):
                            channels = {k for k in tracks.get(aliases[bone], {}) if k != "parent"}
                            self.assertEqual(set(), channels, (clip_name, bone))

    def test_body_motion_controls_garment_skeleton_in_every_emote_phase(self):
        geometry = node("rootdummy", "body", "position 0 0 1.2")
        geometry += node("torso_g", "rootdummy", "position 0 0 0.1")
        body_clips, coat_clips = "", ""
        for phase in ("start", "lp", "end"):
            body_tracks = node("body", "null") + node("rootdummy", "body")
            body_tracks += node("torso_g", "rootdummy", "orientationkey 2\n0 0 0 1 0\n1 0 0 1 0.2")
            if phase == "end":
                body_tracks = body_tracks.replace("parent body\n", "parent body\nposition 0 0 1.2\n")
            body_clips += clip("body", 1, body_tracks).replace("walk", f"custom1{phase}")
            # The stock coat's same-numbered slot jumps instead of pointing.
            coat_tracks = node("coat", "null") + node("rootdummy", "coat", "position 0.8 0 1.9")
            coat_tracks += node("torso_g", "rootdummy", "position 9 9 9\nscale 2\norientation 1 0 0 2")
            coat_tracks += node("tail", "torso_g", "orientationkey 2\n0 1 0 0 0\n2 1 0 0 0.4")
            coat_clips += clip("coat", 2, coat_tracks).replace("walk", f"custom1{phase}")
        coat_geometry = geometry.replace("parent body", "parent coat") + node("tail", "torso_g")
        fixtures = {"body": model("body", "null", geometry, body_clips),
                    "coat": model("coat", "body", coat_geometry, coat_clips),
                    "garment": model("garment", "coat", coat_geometry.replace("parent coat", "parent garment"))}
        for family_type in (rig.Families, shared.Families):
            with self.subTest(family=family_type.__module__):
                families = family_type(fixtures.get)
                key = families.add("body", fixtures["garment"])
                _, names = families.body_root("garment", "wearer", "bridge")
                animations = list(anim.ANIMATION.finditer(families.bridge(key, "bridge").decode()))
                self.assertEqual(3, len(animations))
                for animation in animations:
                    tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
                    for bone in ("rootdummy", "torso_g"):
                        actual = {k: v for k, v in tracks.get(names[bone], {}).items() if k != "parent"}
                        if family_type is shared.Families:
                            # Bound beneath the wearer bone: the stock coat's
                            # jump can never move it, and it has no tracks.
                            self.assertEqual({}, actual, (animation[1], bone))
                        else:
                            expected = {k: v for k, v in tracks[bone].items() if k != "parent"}
                            self.assertEqual(expected, actual, (animation[1], bone))
                    self.assertIn("length 1", animation[3])
                    self.assertEqual("1", tracks[names["tail"]]["orientationkey"][-1][0])

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

    @staticmethod
    def carry_overlay_source():
        return ("newmodel a_ba_casts\nsetsupermodel a_ba_casts NULL\nbeginmodelgeom a_ba_casts\n"
                "node dummy a_ba_casts\nparent NULL\nendnode\nendmodelgeom a_ba_casts\n"
                "newanim sw_nohold a_ba_casts\nlength 1\ntranstime 0\nanimroot a_ba_casts\n"
                "node dummy a_ba_casts\nparent NULL\nendnode\ndoneanim sw_nohold a_ba_casts\ndonemodel a_ba_casts\n")

    def test_inherited_overlay_preserves_exact_family_and_bridge_source(self):
        helper = self
        fixtures = helper.fixtures()
        before, key, body, names = helper.build(fixtures)
        source = before.bridge(key, "bridge")
        fixtures["tail"] = self.carry_overlay_source().replace("a_ba_casts", "tail")
        fixtures["body"] = fixtures["body"].replace("setsupermodel body null", "setsupermodel body tail")
        after, new_key, new_body, new_names = helper.build(fixtures)
        self.assertIn("sw_nohold", after.clips("body"))
        self.assertEqual(new_key, key)
        self.assertEqual(after.bridge(new_key, "bridge"), source)
        self.assertEqual((new_body, new_names), (body, names))

    def test_reserved_overlay_cannot_hide_real_controllers_or_events(self):
        helper = self
        for field in ("position 0 0 0", "orientationkey 1\n0 0 0 1 0", "scale 1", "alpha 1"):
            fixtures = helper.fixtures()
            bad = self.carry_overlay_source().replace("a_ba_casts", "tail").replace("parent NULL\nendnode\ndoneanim", f"parent NULL\n{field}\nendnode\ndoneanim")
            fixtures["tail"] = bad
            fixtures["body"] = fixtures["body"].replace("setsupermodel body null", "setsupermodel body tail")
            with self.subTest(field=field), self.assertRaises(ValueError):
                helper.build(fixtures)
        fixtures["tail"] = self.carry_overlay_source().replace("a_ba_casts", "tail").replace("length 1", "length 1\nevent 0.5 hit")
        with self.assertRaises(ValueError):
            helper.build(fixtures)

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
