"""Mesh nodes must not draw different texels on the same surface, which z-fights with the camera."""
from pathlib import Path
import tempfile
import unittest

import CoplanarFaces as coplanar

ROOT = Path(__file__).resolve().parents[1]


def model(second_position="0 0 0", second_z=0.0, second_uv=(0.5, 1.0)):
    """A wall quad and a second quad parented to it; the second node's position offsets its vertices."""
    low, high = second_uv
    return f"""newmodel probe
setsupermodel probe null
beginmodelgeom probe
node dummy probe
  parent NULL
endnode
node trimesh screen
  parent probe
  position 0 0 1
  bitmap probe_tex
  verts 4
    0 0 0
    1 0 0
    1 0 1
    0 0 1
  faces 2
    0 1 2 0 0 1 2 1
    2 3 0 0 2 3 0 1
  tverts 4
    0 0 0
    0.5 0 0
    0.5 0.5 0
    0 0.5 0
endnode
node trimesh panel
  parent screen
  position {second_position}
  bitmap probe_tex
  verts 4
    0 0 {second_z}
    1 0 {second_z}
    1 0 {second_z + 1}
    0 0 {second_z + 1}
  faces 2
    0 1 2 0 0 1 2 1
    2 3 0 0 2 3 0 1
  tverts 4
    {low} {low} 0
    {high} {low} 0
    {high} {high} 0
    {low} {high} 0
endnode
endmodelgeom probe
donemodel probe
"""


class CoplanarFaceTests(unittest.TestCase):
    def conflicts(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.mdl"
            path.write_text(text, encoding="latin1")
            return coplanar.conflicts(coplanar.read_meshes(path))

    def test_duplicate_quad_with_other_texels_is_reported(self):
        found = self.conflicts(model())
        self.assertEqual([("screen", "panel")], [(first, second) for first, second, _ in found])
        self.assertAlmostEqual(1.0, found[0][2], places=3)

    def test_parent_offset_cancelled_by_vertices_is_still_reported(self):
        # The Blacksite station's duplicate panel sat 1.69 m up, with vertices moved 1.69 m down.
        self.assertEqual(1, len(self.conflicts(model(second_position="0 0 1.68974", second_z=-1.68974))))

    def test_identical_copy_and_separated_faces_pass(self):
        self.assertEqual([], self.conflicts(model(second_uv=(0.0, 0.5))))
        self.assertEqual([], self.conflicts(model(second_position="0 -0.01 0")))
        self.assertEqual([], self.conflicts(model(second_position="1 0 0")))

    def test_blacksite_analysis_station_screen_has_one_surface(self):
        path = ROOT / "sw_plc" / "plc_comppnl2.mdl"
        if not path.is_file():
            self.skipTest("sw_plc is not checked out")
        meshes = coplanar.read_meshes(path)
        self.assertIn("object28", [mesh.name for mesh in meshes])
        self.assertEqual([], coplanar.conflicts(meshes))


if __name__ == "__main__":
    unittest.main()
