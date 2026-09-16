"""Keep static robe 236 torso panels off the cloth-physics renderer."""
import json
import unittest

import CompileModels as mdl
import MakeRobePanelsRigid as rigid
import RobePoseAudit as poses


class RigidRobePanelTests(unittest.TestCase):
    def source(self, constraint="0"):
        return f"""node danglymesh coat_top
parent torso_g
position 0 0.003 0.002
bitmap robe
materialname robe
verts 1
0 0 0.5
constraints 1
{constraint}
displacement 0.03
period 6
tightness 5
endnode
"""

    def test_conversion_keeps_geometry_material_and_parent(self):
        original = mdl.parse_nodes(self.source())[0][2]
        result, names = rigid.rigid_source(self.source())
        kind, name, props = mdl.parse_nodes(result)[0]
        self.assertEqual((kind, name, names), ("trimesh", "coat_top", ["coat_top"]))
        for field in ("constraints", "displacement", "period", "tightness"):
            original.pop(field)
        self.assertEqual(original, props)
        self.assertEqual((result, []), rigid.rigid_source(result))

    def test_real_cloth_motion_is_preserved(self):
        source = self.source("128")
        self.assertEqual((source, []), rigid.rigid_source(source))

    def test_missing_constraints_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "constraints"):
            rigid.rigid_source(self.source().replace("constraints 1\n0", "constraints 0"))

    def test_local_animation_node_keeps_its_transform_track(self):
        source = self.source() + """node danglymesh coat_top
parent torso_g
positionkey 2
0 0 0 0
1 0 0 0.01
period 6
tightness 5
endnode
"""
        result, names = rigid.rigid_source(source)
        nodes = mdl.parse_nodes(result)
        self.assertEqual(["coat_top"], names)
        self.assertEqual(["trimesh", "trimesh"], [node[0] for node in nodes])
        self.assertEqual(mdl.parse_nodes(source)[1][2]["positionkey"], nodes[1][2]["positionkey"])
        self.assertNotIn("period", nodes[1][2])

    def test_robe236_native_and_rgb_torso_panels_are_rigid(self):
        manifest = json.loads(rigid.MANIFEST.read_text())
        paths = rigid.targets(236, manifest)
        self.assertEqual(32, len(paths))
        checked = 0
        for path in paths:
            model = poses.Model(path.read_bytes(), False)
            for name, _, _, _, offset in model.nodes:
                if name.removeprefix("rm_").removeprefix("rg_") in ("coat_top", "coat_top2"):
                    self.assertEqual(0x21, model.uint(offset + 108), f"{path.name}/{name}")
                    checked += 1
        # Six body families needed conversion; both elf families were already rigid.
        self.assertEqual(24, checked)


if __name__ == "__main__":
    unittest.main()
