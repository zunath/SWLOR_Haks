"""Original garment parents must use the game's current meaning of emote slots."""
import unittest
import tempfile
import struct
from pathlib import Path

import AlignRobeAnimations as alignment
import CompileModels as mdl
import RobeAnimations as anim
import RobePoseAudit as poses
from TestRobeAnimations import model, node, clip


class OriginalRobeAnimationAlignmentTests(unittest.TestCase):
    def test_descendant_remap_preserves_native_data_and_moves_private_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            compiler, _ = mdl.prepare_compiler(stage)
            (stage / "binary").mkdir()
            def compile_model(name, parent, geometry, animations=""):
                source = model(name, parent, geometry, animations).encode()
                path = stage / f"{name}.mdl"
                path.write_bytes(source)
                mdl.run_compiler(compiler, stage, ["-cne", str(path), str(stage / "binary") + "/"], name + ".log")
                data = (stage / "binary" / path.name).read_bytes()
                path.write_bytes(data)
                return data
            old_geometry = node("rootdummy", "oldparent", "position 0 0 1")
            old_geometry += node("cloth", "rootdummy", "position 0 0 0.1")
            old_parent = compile_model("oldparent", "null", old_geometry)
            new_geometry = node("rootdummy", "newparent", "position 0 0 1")
            new_geometry += node("extra", "rootdummy", "position 0 0 0.2")
            new_geometry += node("cloth", "rootdummy", "position 0 0 0.1")
            new_parent = compile_model("newparent", "null", new_geometry)
            child_geometry = old_geometry.replace("oldparent", "child")
            child_geometry += node("private", "rootdummy", "position 0 0 0.3")
            motion = node("child", "null") + node("rootdummy", "child")
            motion += node("cloth", "rootdummy", "orientation 0 0 1 0.2")
            motion += node("private", "rootdummy", "orientationkey 2\n0 0 0 1 0.4\n1 0 0 1 0.6")
            before = compile_model("child", "oldparent", child_geometry, clip("child", 1, motion))
            # The compiler omits IDs for standalone dummy helpers. Legacy
            # exporters also assign them IDs; exercise that valid native case.
            fixture = poses.Model(before)
            assigned = bytearray(before)
            for name, _, _, _, offset in [*fixture.nodes, *fixture.clips["walk"][1]]:
                if name == "private":
                    struct.pack_into("<i", assigned, offset + 28, 3)
            before = bytes(assigned)
            after = alignment.remap_inherited_parts(before, old_parent, new_parent)
            old_parts, new_parts = anim.binary_parts(before), anim.binary_parts(after)
            self.assertEqual(2, old_parts[("rootdummy", "cloth")])
            self.assertEqual(3, new_parts[("rootdummy", "cloth")])
            self.assertEqual(4, new_parts[("rootdummy", "private")])
            old_model, new_model = poses.Model(before), poses.Model(after)
            for old_node, new_node in zip(old_model.nodes, new_model.nodes):
                self.assertEqual(old_node[:2] + old_node[3:], new_node[:2] + new_node[3:])
            self.assertEqual(old_model.clips["walk"][0], new_model.clips["walk"][0])
            for old_node, new_node in zip(old_model.clips["walk"][1], new_model.clips["walk"][1]):
                self.assertEqual(old_node[:2] + old_node[3:], new_node[:2] + new_node[3:])
            # Only the two native part IDs (in geometry and animation nodes)
            # may differ; no array, controller, pointer, or resource data moves.
            self.assertEqual(4, sum(a != b for a, b in zip(before, after)))
            self.assertEqual(len(before), len(after))

    def fixtures(self):
        geometry = node("rootdummy", "body", "position 0 0 1.2")
        geometry += node("torso_g", "rootdummy", "position 0 0 0.1")
        body_tracks = node("body", "null") + node("rootdummy", "body")
        body_tracks += node("torso_g", "rootdummy", "orientationkey 2\n0 0 0 1 0\n1 0 0 1 0.2")
        body = model("body", "null", geometry, clip("body", 1, body_tracks).replace("walk", "custom1lp"))
        coat_geometry = geometry.replace("parent body", "parent coat") + node("tail", "torso_g")
        coat_tracks = node("coat", "null") + node("rootdummy", "coat", "position 0.8 0 1.9")
        coat_tracks += node("torso_g", "rootdummy", "position 9 9 9\nscale 2\norientation 1 0 0 2")
        coat_tracks += node("tail", "torso_g", "orientationkey 2\n0 1 0 0 0\n2 1 0 0 0.4")
        coat = model("coat", "body", coat_geometry, clip("coat", 2, coat_tracks).replace("walk", "custom1lp"))
        return {"body": body, "coat": coat}

    def test_pointing_replaces_jump_and_preserves_cloth_and_geometry(self):
        fixtures = self.fixtures()
        before = fixtures["coat"]
        after, changed = alignment.align(before, "body", fixtures.get)
        self.assertEqual(["custom1lp"], changed)
        self.assertEqual(list(anim.geometry(before)), list(anim.geometry(after)))
        animation = next(anim.ANIMATION.finditer(after))
        tracks = {name: props for _, name, props in mdl.parse_nodes(animation[3])}
        self.assertNotIn("position", tracks["rootdummy"])
        self.assertNotIn("position", tracks["torso_g"])
        self.assertNotIn("scale", tracks["torso_g"])
        self.assertEqual("0.2", tracks["torso_g"]["orientationkey"][-1][-1])
        self.assertEqual("1", tracks["tail"]["orientationkey"][-1][0])
        self.assertIn("length 1", animation[3])
        self.assertEqual((after, []), alignment.align(after, "body", fixtures.get))

    def test_garment_only_animation_is_preserved(self):
        fixtures = self.fixtures()
        before = fixtures["coat"].replace("custom1lp", "garment_only")
        self.assertEqual((before, []), alignment.align(before, "body", fixtures.get))

    def test_animroot_header_remains_separate_from_first_node(self):
        fixtures = self.fixtures()
        fixtures["body"] = fixtures["body"].replace("animroot rootdummy", "animroot body").replace("event 0.5 step\n", "")
        after, _ = alignment.align(fixtures["coat"], "body", fixtures.get)
        animation = next(anim.ANIMATION.finditer(after))
        self.assertIn("animroot coat\nnode", animation[3])
        self.assertIn("coat", [name for _, name, _ in mdl.parse_nodes(animation[3])])

    def test_exported_clip_root_can_have_its_ancestors_original_model_name(self):
        fixtures = self.fixtures()
        # Keep the geometry intact; only an animation imported from another
        # model carries this alias in real coat parents.
        start = fixtures["coat"].index("newanim")
        before = fixtures["coat"][:start] + fixtures["coat"][start:].replace(
            "node dummy coat", "node dummy source").replace("parent coat", "parent source")
        after, changed = alignment.align(before, "body", fixtures.get)
        self.assertEqual(["custom1lp"], changed)
        animation = next(anim.ANIMATION.finditer(after))
        tracks = {name: props for _, name, props in mdl.parse_nodes(animation[3])}
        self.assertEqual(["null"], tracks["coat"]["parent"])
        self.assertEqual(["coat"], tracks["rootdummy"]["parent"])


if __name__ == "__main__":
    unittest.main()
