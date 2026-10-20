"""Regression checks for garment animation overlays and body attachment tracks."""
import unittest
from pathlib import Path
import struct
import tempfile

import CompileModels as mdl
import RobeAnimations as robe
import RobePoseAudit as poses


def model(name, parent, nodes, clips=""):
    return (f"newmodel {name}\nsetsupermodel {name} {parent}\nbeginmodelgeom {name}\n"
            f"node dummy {name}\nparent null\nendnode\n{nodes}"
            f"endmodelgeom {name}\n{clips}donemodel {name}\n")


def node(name, parent, fields=""):
    return f"node dummy {name}\nparent {parent}\n{fields}\nendnode\n"


def clip(owner, length, nodes):
    return f"newanim walk {owner}\nlength {length}\ntranstime 0.2\nanimroot rootdummy\nevent 0.5 step\n{nodes}doneanim walk {owner}\n"


class RobeAnimationTests(unittest.TestCase):
    def fixtures(self):
        body_nodes = node("rootdummy", "body") + node("head_g", "rootdummy", "position 0 0 2") + node("lthigh_g", "rootdummy")
        body_tracks = node("body", "null") + node("rootdummy", "body") + node("head_g", "rootdummy", "orientationkey 2\n0 1 0 0 0\n1 1 0 0 0.2") + node("lthigh_g", "rootdummy", "positionkey 2\n0 0 0 0\n1 0 1 0")
        overlay_nodes = node("rootdummy", "coat") + node("coat_tail", "rootdummy")
        overlay_tracks = node("coat", "null") + node("rootdummy", "coat") + node("coat_tail", "rootdummy", "orientationkey 2\n0 0 1 0 0\n2 0 1 0 1\nposition 0 0 1")
        return {"body": model("body", "null", body_nodes, clip("body", 1, body_tracks)),
                "coat": model("coat", "body", overlay_nodes, clip("coat", 2, overlay_tracks)),
                "garment": model("garment", "coat", node("rootdummy", "garment"))}

    def test_incomplete_garment_preserves_body_and_coat_tracks(self):
        result = robe.bridge("body", "garment", self.fixtures().get, "combined").decode()
        geometry = {n: p for _, n, p in robe.geometry(result)}
        self.assertEqual(geometry["head_g"]["position"], ["0", "0", "2"])
        animation = next(robe.ANIMATION.finditer(result))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertIn("orientationkey", tracks["head_g"])
        # A bridge must not bake body bind offsets into the inherited animation:
        # wearers have different proportions and native idle only rotates bones.
        for bone in ("head_g", "lthigh_g"):
            for channel in ("position", "positionkey", "scale", "scalekey"):
                self.assertNotIn(channel, tracks[bone], bone)
        # Garment helpers are not wearer bones and keep their authored offsets.
        self.assertIn("position", tracks["coat_tail"])
        self.assertIn("orientationkey", tracks["coat_tail"])
        self.assertEqual(tracks["coat_tail"]["parent"], ["rootdummy"])

    def test_coat_tracks_follow_body_duration_and_events(self):
        result = robe.bridge("body", "garment", self.fixtures().get, "combined").decode()
        animation = next(robe.ANIMATION.finditer(result))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertIn("length 1", animation[3])
        self.assertIn("event 0.5 step", animation[3])
        self.assertEqual(tracks["coat_tail"]["orientationkey"][-1], ["1", "0", "1", "0", "1"])

    def test_ordinary_garment_inherits_complete_body_directly(self):
        fixtures = self.fixtures()
        fixtures["garment"] = model("garment", "body", node("rootdummy", "garment"))
        self.assertIsNone(robe.bridge("body", "garment", fixtures.get, "combined"))

    def test_ordinary_garment_with_missing_hands_uses_body_animations(self):
        fixtures = self.fixtures()
        fixtures["body"] = fixtures["body"].replace("endmodelgeom body", node("lhand_g", "rootdummy") + node("rhand_g", "rootdummy") + "endmodelgeom body")
        fixtures["garment"] = model("garment", "body", node("rootdummy", "garment"))
        policy = robe.BodyAnimationInheritance(fixtures.get)
        self.assertTrue(policy.can_inherit("body", fixtures["garment"]))

    def test_custom_animation_overlay_is_not_replaced_with_body_animations(self):
        fixtures = self.fixtures()
        policy = robe.BodyAnimationInheritance(fixtures.get)
        self.assertFalse(policy.can_inherit("body", fixtures["garment"]))

    def test_different_garment_hierarchy_is_not_automatically_retargeted(self):
        fixtures = self.fixtures()
        fixtures["garment"] = model("garment", "body", node("helper", "garment") + node("rootdummy", "helper"))
        policy = robe.BodyAnimationInheritance(fixtures.get)
        self.assertFalse(policy.can_inherit("body", fixtures["garment"]))

    def test_missing_animation_source_does_not_allow_automatic_retargeting(self):
        fixtures = self.fixtures()
        fixtures["body"] = fixtures["body"].replace("setsupermodel body null", "setsupermodel body missing")
        fixtures["garment"] = model("garment", "body", node("rootdummy", "garment"))
        policy = robe.BodyAnimationInheritance(fixtures.get)
        self.assertFalse(policy.can_inherit("body", fixtures["garment"]))

    def test_invalid_parent_does_not_silently_retarget_body(self):
        fixtures = self.fixtures()
        fixtures["coat"] = fixtures["coat"].replace("parent coat\n", "parent head_g\n", 1)
        with self.assertRaisesRegex(ValueError, "incompatible animation parent"):
            robe.bridge("body", "garment", fixtures.get, "combined")


class StaticBodyPartPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        stage = Path(temporary.name)
        compiler, _ = mdl.prepare_compiler(stage)
        (stage / "binary").mkdir()
        geometry = (node("rootdummy", "body") + node("belt_g", "rootdummy", "position 0 0 1") +
                    node("arm_g", "rootdummy") + node("static_parent", "rootdummy") +
                    node("cloth", "static_parent"))
        tracks = (node("body", "null") + node("rootdummy", "body") +
                  node("arm_g", "rootdummy", "orientationkey 2\n0 0 0 1 0\n1 0 0 1 0.5") +
                  node("static_parent", "rootdummy") +
                  node("cloth", "static_parent", "positionkey 2\n0 0 0 0\n1 0 1 0"))
        source = stage / "body.mdl"
        source.write_text(model("body", "null", geometry, clip("body", 1, tracks)))
        mdl.run_compiler(compiler, stage, ["-cne", str(source), str(stage / "binary") + "/"], "compile.log")
        cls.compiled = (stage / "binary/body.mdl").read_bytes()

    def with_geometry_id(self, data, name, part):
        changed = bytearray(data)
        offset = next(offset for node, _, _, _, offset in poses.Model(data, False).nodes if node == name)
        struct.pack_into("<i", changed, offset + 28, part)
        return bytes(changed), offset + 28

    def test_untracked_leaf_restores_exact_native_id_and_preserves_every_other_byte(self):
        base, offset = self.with_geometry_id(self.compiled, "belt_g", -1)
        generated, _ = self.with_geometry_id(base, "belt_g", 900)
        restored = robe.preserve_static_body_parts(generated, base)
        self.assertEqual(base, restored)
        self.assertEqual(generated[:offset], restored[:offset])
        self.assertEqual(generated[offset + 4:], restored[offset + 4:])
        self.assertEqual(restored, robe.preserve_static_body_parts(restored, base))
        robe.validate_animation_parts(restored)
        poses.validate_body_skeleton(restored, base)
        actual, expected = poses.Model(restored), poses.Model(base)
        self.assertEqual(actual.pose(), expected.pose())
        for name, animation in actual.clips.items():
            for fraction in (0, .25, .5, 1):
                self.assertEqual(actual.pose(animation, animation[0] * fraction),
                                 expected.pose(expected.clips[name], animation[0] * fraction))

    def test_positive_native_ids_are_not_changed(self):
        generated, _ = self.with_geometry_id(self.compiled, "arm_g", 900)
        self.assertEqual(generated, robe.preserve_static_body_parts(generated, self.compiled))
        with self.assertRaisesRegex(ValueError, "animation part"):
            robe.validate_body_parts(generated, self.compiled, self.compiled)

    def test_animated_formerly_static_leaf_is_rejected(self):
        base, _ = self.with_geometry_id(self.compiled, "arm_g", -1)
        with self.assertRaisesRegex(ValueError, "animated body ID"):
            robe.preserve_static_body_parts(self.compiled, base)

    def test_static_ancestor_above_cloth_is_rejected(self):
        base, _ = self.with_geometry_id(self.compiled, "static_parent", -1)
        generated, _ = self.with_geometry_id(base, "static_parent", 900)
        with self.assertRaisesRegex(ValueError, "above descendants"):
            robe.preserve_static_body_parts(generated, base)


if __name__ == "__main__":
    unittest.main()
