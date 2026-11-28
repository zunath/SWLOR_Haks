"""Skin pick boxes must follow the standing pose and change nothing else."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import SkinPickBounds as bounds

# One bone at the root and a skin bound 3 m above the ground, like the Behir rig.
ASCII = """newmodel beast
setsupermodel beast null
beginmodelgeom beast
node dummy beast
  parent NULL
endnode
node dummy spine
  parent beast
  position 0 0 3
  orientation 0 0 1 0
endnode
node skin body
  parent beast
  position 0 0 3
  orientation 0 0 1 0
  verts 2
    0 -2 0
    0 2 0
  weights 2
    spine 1
    spine 1
endnode
endmodelgeom beast
newanim cpause1 beast
  length 1
  node dummy beast
    parent NULL
  endnode
  node dummy spine
    parent beast
    positionkey
      0 0 0 0.5
    endlist
    orientationkey
      0 0 0 1 1.5707963
    endlist
  endnode
doneanim cpause1 beast
donemodel beast
"""


def binary_model(name=b"body", flags=bounds.MESH_FLAG | bounds.SKIN_FLAG):
    """Smallest compiled layout the tool reads: a root node with no children."""
    data = bytearray(12 + 112 + 112 + 512)
    struct.pack_into("<I", data, 12 + 72, 112)
    node = 12 + 112
    data[node + 32:node + 32 + len(name)] = name
    struct.pack_into("<I", data, node + 108, flags)
    struct.pack_into("<7f", data, node + 132, -1, -2, -3, 1, 2, 3, 9)
    struct.pack_into("<3f", data, node + 160, 0, 0, 0)
    return bytes(data)


class SkinPickBoundsTests(unittest.TestCase):
    def test_box_follows_the_standing_pose(self):
        low, high = bounds.pose_boxes(ASCII.splitlines(), [])["body"]
        # The bone drops 2.5 m and turns the body sideways; the box is in the skin's own space.
        for actual, expected in zip(low + high, [-2, 0, -2.5, 2, 0, -2.5]):
            self.assertAlmostEqual(actual, expected, places=4)

    def test_supermodel_animation_is_used_when_the_model_has_none(self):
        geometry = ASCII.split("newanim")[0].splitlines() + ["donemodel beast"]
        animations = ["newmodel parent"] + ("newanim" + ASCII.split("newanim")[1]).splitlines()
        self.assertEqual(bounds.pose_boxes(geometry, [animations]), bounds.pose_boxes(ASCII.splitlines(), []))

    def test_animation_scale_shrinks_borrowed_positions(self):
        scaled = ASCII.replace("setsupermodel beast null", "setsupermodel beast null\nsetanimationscale 0.5")
        low, high = bounds.pose_boxes(scaled.splitlines(), [])["body"]
        self.assertAlmostEqual(low[2], -2.75, places=4)
        self.assertAlmostEqual(high[2], -2.75, places=4)

    def test_missing_standing_animation_is_an_error(self):
        with self.assertRaises(ValueError):
            bounds.pose_boxes(ASCII.split("newanim")[0].splitlines(), [])

    def test_patch_rewrites_only_box_and_radius(self):
        before = binary_model()
        after = bounds.patch(before, {"body": ([0, 0, -2.5], [2, 1, -2])})
        node = 12 + 112
        self.assertEqual(before[:node + 132], after[:node + 132])
        self.assertEqual(before[node + 160:], after[node + 160:])
        low, high, radius = bounds.skin_boxes(after)["body"]
        self.assertEqual((low, high), ((0, 0, -2.5), (2, 1, -2)))
        self.assertAlmostEqual(radius, (4 + 1 + 6.25) ** 0.5, places=5)

    def test_constrained_fit_keeps_original_box_and_vertex_radius(self):
        before = binary_model()
        vertices = {"body": [(-1, 0, 0), (0, 2, 0), (1, 0, 0), (0, 0, 1)]}
        posed = {"body": ([-3, 0.5, -2], [0.5, 6, 4])}
        fitted = bounds.patch(before, posed, limit_to_vertices=vertices)
        self.assertEqual(bounds.skin_boxes(fitted)["body"], ((-1, 0.5, 0), (0.5, 2, 1), 2))
        self.assertEqual(bounds.patch(fitted, posed, limit_to_vertices=vertices), fitted)
        with self.assertRaisesRegex(ValueError, "disjoint"):
            bounds.patch(before, {"body": ([3, 4, 5], [4, 5, 6])}, limit_to_vertices=vertices)

    def test_idle_is_resolved_before_a_missing_ancestor(self):
        child = ASCII.split("newanim")[0].replace("setsupermodel beast null", "setsupermodel beast parent")
        parent = ASCII.replace("setsupermodel beast null", "setsupermodel beast absent")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "parent-source.mdl"
            for animation in ("cpause1", "pause1"):
                source.write_text(parent.replace("cpause1", animation))
                lines = bounds.decompiled_chain("beast", child.encode(), None, {"parent": source}, {}, root, None)
                self.assertEqual(len(lines), 2)
                self.assertEqual(bounds.pose_boxes(lines[0], lines[1:]), bounds.pose_boxes(ASCII.splitlines(), []))

    def test_static_geometry_requires_the_named_missing_parent(self):
        geometry = ASCII.split("newanim")[0].replace("setsupermodel beast null", "setsupermodel beast absent")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(FileNotFoundError, "absent"):
                bounds.decompiled_chain("beast", geometry.encode(), None, {}, {}, root, None, "other")
            lines = bounds.decompiled_chain("beast", geometry.encode(), None, {}, {}, root, None, "absent")
            self.assertEqual(bounds.pose_boxes(lines[0], [], allow_static=True)["body"],
                             ([0, -2, 0], [0, 2, 0]))

    def test_rigid_meshes_keep_their_box(self):
        before = binary_model(flags=bounds.MESH_FLAG)
        self.assertEqual(bounds.patch(before, {"body": ([0, 0, 0], [1, 1, 1])}), before)

    def test_ascii_models_must_be_compiled_first(self):
        with self.assertRaises(ValueError):
            list(bounds.mesh_nodes(ASCII.encode()))

    def test_check_flags_models_changed_after_fitting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "models").mkdir()
            model = root / "models/beast.mdl"
            model.write_bytes(binary_model())
            manifest = {"models": {"beast": {"path": "models/beast.mdl",
                                             "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}}}
            (root / bounds.MANIFEST).write_text(json.dumps(manifest))
            (root / "sw_2da").mkdir()
            (root / bounds.APPEARANCE).write_text("2DA V2.0\n\n   LABEL   RACE   MODELTYPE\n0   Beast   beast   S\n")
            active = {"beast": model}
            bounds.check(root, active)
            model.write_bytes(binary_model(name=b"other"))
            with self.assertRaises(ValueError):
                bounds.check(root, active)

    def test_check_flags_ascii_skinned_appearance_models(self):
        table = ("2DA V2.0\n\n   LABEL   RACE   MODELTYPE\n"
                 "0   Beast   beast   S\n1   \"Some Body\"   pmh0   P\n2   Statue   statue   F\n")
        statue = ASCII.split("node skin")[0] + "endmodelgeom beast\ndonemodel beast\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "sw_2da").mkdir()
            (root / bounds.APPEARANCE).write_text(table)
            (root / bounds.MANIFEST).write_text(json.dumps({"models": {}}))
            for name, text in (("beast", ASCII), ("pmh0", ASCII), ("statue", statue)):
                (root / f"{name}.mdl").write_text(text)
            active = {name: root / f"{name}.mdl" for name in ("beast", "pmh0", "statue")}
            # Only the creature row with an ASCII skin fails; part rows and skinless models pass.
            with self.assertRaisesRegex(ValueError, r"\['beast'\]"):
                bounds.check(root, active)
            (root / "beast.mdl").write_bytes(binary_model())
            bounds.check(root, active)
            (root / "beast.mdl").write_text(ASCII)
            (root / bounds.MANIFEST).write_text(json.dumps({"models": {}, "ascii_exempt": {"beast": "reason"}}))
            bounds.check(root, active)


if __name__ == "__main__":
    unittest.main()
