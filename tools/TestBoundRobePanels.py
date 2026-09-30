"""Robe 236 coverings follow the torso during movement, skills and idle."""
import json
from pathlib import Path
import unittest

import BindRobePanels as bound
import CompileModels as mdl
import MakeRobePanelsRigid as rigid
import RobePoseAudit as poses


PANELS = ("coat_top", "coat_top2", "hood002", "beltjr")


class BoundRobePanelTests(unittest.TestCase):
    def source(self, name="coat_top", parent="torso_g"):
        return f"""node trimesh {name}
parent {parent}
position 0 0.003 0.002
orientation 0 0 1 0.1
bitmap robe
materialname robe
verts 3
0 0 0.5
0.1 0 0.5
0 0.1 0.5
endnode
"""

    def test_selected_panel_gets_only_a_torso_weight_and_keeps_its_shape(self):
        for name, bone in (("coat_top", "torso_g"), ("rm_coat_top", "rg_torso_g_2"),
                           ("rg_coat_top2", "rg_torso_g_4")):
            with self.subTest(name=name):
                source = self.source(name, bone)
                result, bindings = bound.bind_source(source, PANELS)
                kind, actual_name, props = mdl.parse_nodes(result)[0]
                self.assertEqual(("skin", name, {name: bone}), (kind, actual_name, bindings))
                self.assertEqual([[bone, "1"]] * 3, props.pop("weights"))
                self.assertEqual(mdl.parse_nodes(source)[0][2], props)
                self.assertEqual((result, {}), bound.bind_source(result, PANELS))

    def test_unselected_meshes_and_animation_controllers_are_unchanged(self):
        other = self.source("other_panel")
        animation = "node dummy coat_top\nparent torso_g\npositionkey 1\n0 0 0 1\nendnode\n"
        source = self.source() + other + animation
        result, bindings = bound.bind_source(source, PANELS)
        self.assertEqual({"coat_top": "torso_g"}, bindings)
        self.assertEqual(mdl.parse_nodes(source)[1:], mdl.parse_nodes(result)[1:])

    def test_wrong_attachment_and_nonrigid_panels_fail_closed(self):
        for source in (self.source(parent="head_g"), self.source().replace("trimesh", "danglymesh"),
                       self.source().replace("bitmap robe", "positionkey 1\n0 0 0 1\nbitmap robe")):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    bound.bind_source(source, PANELS)

    def test_all_native_and_rgb_coverings_are_skinned_to_their_torso(self):
        manifest = json.loads(rigid.MANIFEST.read_text())
        checked = 0
        for path in rigid.targets(236, manifest):
            data = path.read_bytes()
            model = poses.Model(data, False)
            geometry = {name: props for name, _, props in mdl.binary_nodes(data) if props}
            bindings = model.skin_bindings()
            for name, parent, _, _, offset in model.nodes:
                if name.removeprefix("rm_").removeprefix("rg_") not in PANELS:
                    continue
                bone = model.nodes[parent][0]
                self.assertEqual(0x61, model.uint(offset + 108), f"{path.name}/{name}")
                self.assertEqual({bone}, set(bindings[name]), f"{path.name}/{name}")
                self.assertTrue(all(row == [bone, 1.0] for row in geometry[name]["weights"]),
                                f"{path.name}/{name}")
                checked += 1
        self.assertEqual(72, checked, "Three coverings in every native and RGB body variant")

    def test_compiled_coverings_keep_their_position_through_movement_skills_and_idle(self):
        manifest = json.loads(rigid.MANIFEST.read_text())
        prefixes = ("pfd", "pfe", "pfh", "pfo", "pmd", "pme", "pmh", "pmo")
        sequence = ("pause1", "walk", "run", "pause2", "sw_quickdraw", "pause1",
                    "sw_forcepush", "pause1", "sw_forceleap", "pause1", "sw_furystn", "pause1",
                    "conjure1", "castout", "pause1")
        for prefix in prefixes:
            path = bound.ROOT / "sw_pt_root" / f"{prefix}200.mdl"
            data = path.read_bytes()
            model = poses.Model(data, False)
            clips = {}
            directory = "sw_anim_f" if prefix[1] == "f" else "sw_anim_m"
            for part in manifest["animation_bank_sets"][model.parent]["parts"]:
                clips.update(poses.Model((bound.ROOT / directory / f"{part}.mdl").read_bytes()).clips)
            self.assertTrue(set(sequence).issubset(clips), prefix)
            bindings = {name: model.nodes[parent][0] for name, parent, _, _, _ in model.nodes
                        if name.removeprefix("rm_").removeprefix("rg_") in PANELS}
            with self.subTest(prefix=prefix):
                bound.validate_panel_poses(data, data, bindings, [clips[name] for name in sequence])


if __name__ == "__main__":
    unittest.main()
