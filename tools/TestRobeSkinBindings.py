"""Check actual compiled skin placement, not just preservation of bind bytes."""
import math
from pathlib import Path
import struct
import tempfile
import unittest

import CompileModels as mdl
import RobePoseAudit as poses


class RotatedSkinBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        stage = Path(cls.temporary.name)
        compiler, _ = mdl.prepare_compiler(stage)
        source = """newmodel skinfit
setsupermodel skinfit NULL
classification CHARACTER
beginmodelgeom skinfit
node dummy skinfit
 parent NULL
endnode
node dummy anchor
 parent skinfit
 position 0 0 2
endnode
node dummy joint
 parent anchor
 position 0 0 1
endnode
node skin cloth
 parent anchor
 position 0 0 -1
 orientation 1 0 0 1.5707963267948966
 bitmap NULL
 verts 3
  0 0 0
  1 0 0
  0 1 0
 tverts 3
  0 0 0
  1 0 0
  0 1 0
 faces 1
  0 1 2 1 0 1 2 0
 weights 3
  joint 1
  joint 1
  joint 1
endnode
endmodelgeom skinfit
donemodel skinfit
"""
        (stage / "skinfit.mdl").write_text(source)
        (stage / "binary").mkdir()
        mdl.run_compiler(compiler, stage, ["-cne", str(stage / "skinfit.mdl"), str(stage / "binary") + "/"], "compile.log")
        cls.original = (stage / "binary/skinfit.mdl").read_bytes()

    def test_rotated_mesh_with_translated_parent_returns_to_its_authored_place(self):
        repairs = poses.compiler_skin_binding_repairs(self.original)
        self.assertEqual({row[:2] for row in repairs}, {("cloth", "joint")})
        self.assertGreater(repairs[0][-1], 2)
        corrected = poses.repair_compiler_skin_bindings(self.original)
        model = poses.Model(corrected, False)
        qoffset, toffset = model.skin_bindings()["cloth"]["joint"]
        w, x, y, z = struct.unpack_from("<4f", corrected, qoffset)
        translation = struct.unpack_from("<3f", corrected, toffset)
        # An independently known bind pose: the mesh is at Z=1, with a 90
        # degree X rotation; its bone is at Z=3. Skinning must reproduce it.
        for vertex, expected in [((0, 0, 0), (0, 0, 1)), ((1, 0, 0), (1, 0, 1)), ((0, 1, 0), (0, 0, 2))]:
            local = poses.rotate((x, y, z, w), vertex)
            actual = tuple(a+b+c for a, b, c in zip((0, 0, 3), translation, local))
            self.assertLess(math.dist(actual, expected), 1e-6)
        self.assertEqual(corrected, poses.repair_compiler_skin_bindings(corrected))
        allowed = {i for _, _, q, t, *_ in repairs for i in (*range(q, q+16), *range(t, t+12))}
        self.assertTrue(all(i in allowed for i, (a, b) in enumerate(zip(self.original, corrected)) if a != b))

    def test_noncompiler_authored_bind_is_preserved(self):
        custom = bytearray(self.original)
        offset = poses.Model(custom, False).skin_bindings()["cloth"]["joint"][1]
        struct.pack_into("<3f", custom, offset, 4, 5, 6)
        self.assertEqual(bytes(custom), poses.repair_compiler_skin_bindings(custom))

    def test_all_shipped_robe_meshes_are_free_of_this_compiler_fault(self):
        root = Path(__file__).resolve().parents[1]
        failures = []
        paths = list((root / "sw_pt_robe").glob("*.mdl"))
        roots = {row.split()[1][:3] + row.split()[2] for row in
                 (root / "sw_2da/roberender.2da").read_text().splitlines()[3:] if row.strip()}
        paths.extend(root / "sw_pt_root" / (name + ".mdl") for name in sorted(roots))
        for path in paths:
            data = path.read_bytes()
            if mdl.binary(data) and poses.compiler_skin_binding_repairs(data):
                failures.append(path.name)
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
