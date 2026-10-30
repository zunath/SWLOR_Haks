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
            bounds.check(root)
            model.write_bytes(binary_model(name=b"other"))
            with self.assertRaises(ValueError):
                bounds.check(root)


if __name__ == "__main__":
    unittest.main()
