"""Static robe attachments follow their bones throughout the complete catalog."""
import json
from pathlib import Path
import unittest
import tempfile

import BindRobePanels as bound
import CompileModels as mdl
import MakeRobePanelsRigid as rigid
import RobePoseAudit as poses
import GenerateTintMapAssets as tint


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


class CatalogPanelPolicyTests(unittest.TestCase):
    def scene(self, panel="trimesh", constraints="", render="1"):
        return f"""newmodel rig
setsupermodel rig NULL
beginmodelgeom rig
node dummy rig
parent NULL
endnode
node dummy torso_g
parent rig
endnode
node trimesh chest
parent torso_g
render {render}
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
endnode
node {panel} ornament
parent chest
render {render}
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
{constraints}endnode
endmodelgeom rig
donemodel rig
"""

    def test_nested_rigid_geometry_binds_to_the_nearest_body_bone(self):
        text = self.scene()
        panels, moving, animated = bound.panel_candidates(text.encode())
        self.assertEqual({"chest": "torso_g", "ornament": "torso_g"}, panels)
        self.assertEqual(([], []), (moving, animated))
        output, bindings = bound.bind_source(text, panels, panels)
        self.assertEqual(panels, bindings)
        ornament = mdl.parse_nodes(output)[-1]
        self.assertEqual("skin", ornament[0])
        self.assertEqual(["chest"], ornament[2]["parent"])
        self.assertEqual([["torso_g", "1"]] * 3, ornament[2]["weights"])

    def test_hidden_meshes_and_moving_cloth_are_preserved(self):
        self.assertEqual(({}, [], []), bound.panel_candidates(self.scene(render="0").encode()))
        source = self.scene("danglymesh", "constraints 3\n0\n128\n255\n")
        panels, moving, _ = bound.panel_candidates(source.encode())
        self.assertEqual({"chest": "torso_g"}, panels)
        self.assertEqual(["ornament"], moving)
        output, _ = bound.bind_source(source, panels, panels)
        self.assertEqual(mdl.parse_nodes(source)[-1], mdl.parse_nodes(output)[-1])

    def test_fully_constrained_cloth_uses_the_bone_renderer(self):
        source = self.scene("danglymesh", "constraints 3\n0\n0\n0\n")
        panels, moving, _ = bound.panel_candidates(source.encode())
        self.assertEqual([], moving)
        output, _ = bound.bind_source(source, panels, panels)
        self.assertEqual("skin", mdl.parse_nodes(output)[-1][0])
        self.assertNotIn("constraints", mdl.parse_nodes(output)[-1][2])

    def test_independent_animation_and_its_children_keep_their_motion(self):
        parts = bound.AnimationParts(lambda _: None)
        data = self.scene() + "newanim motion rig\nnode dummy chest\nparent torso_g\norientationkey 1\n0 0 0 1 0.2\nendnode\ndoneanim motion rig\n"
        inherited = parts.inherited(data.encode())
        panels, _, animated = bound.panel_candidates(data.encode(), inherited)
        self.assertEqual({}, panels)
        self.assertEqual(["chest", "ornament"], animated)

    def test_constant_rest_pose_tracks_are_static_but_different_resets_are_animated(self):
        parts = bound.AnimationParts(lambda _: None)
        clip = "newanim reset rig\nnode dummy chest\nparent torso_g\norientationkey 1\n0 0 0 1 0\nendnode\ndoneanim reset rig\n"
        data = (self.scene() + clip).encode()
        self.assertEqual(set(), parts.inherited(data))
        self.assertEqual({"chest": "torso_g", "ornament": "torso_g"},
                         bound.panel_candidates(data, parts.inherited(data))[0])
        different_bind = data.replace(b"parent torso_g\nrender", b"parent torso_g\norientation 0 0 1 0.2\nrender")
        self.assertEqual({"chest"}, parts.inherited(different_bind))

    def test_scaled_nested_attachment_keeps_its_compiled_world_shape(self):
        source = self.scene().replace("parent rig\nendnode", "parent rig\nposition 0 0 2\nscale 2\nendnode")
        source = source.replace("parent torso_g\nrender", "parent torso_g\nscale 0.5\nrender")
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            compiler, _ = mdl.prepare_compiler(stage)
            (stage / "before").mkdir()
            (stage / "after").mkdir()
            source_path = stage / "rig.mdl"
            source_path.write_text(source)
            mdl.run_compiler(compiler, stage, ["-cne", str(source_path), str(stage / "before") + "/"], "before.log")
            before = (stage / "before/rig.mdl").read_bytes()
            panels, _, _ = bound.panel_candidates(before)
            world = poses.Model(before, False).pose()
            scales = {mesh: world[mesh][2]/world[bone][2] for mesh, bone in panels.items()}
            self.assertEqual({"chest": .5, "ornament": .5}, scales)
            output, bindings = bound.bind_source(source, panels, panels, scales)
            source_path.write_text(output)
            mdl.run_compiler(compiler, stage, ["-cne", str(source_path), str(stage / "after") + "/"], "after.log")
            after = bound.preserve_bindings(before, (stage / "after/rig.mdl").read_bytes(), bindings)
            bound.validate_panel_poses(before, after, bindings)
            geometry = {name: props for name, _, props in mdl.binary_nodes(after) if props}
            self.assertEqual(.5, max(vertex[0] for vertex in geometry["ornament"]["verts"]))


class CatalogBoundRobePanelTests(unittest.TestCase):
    def test_legacy_ascii_hat_is_compiled_and_bound_without_changing_its_scale(self):
        data = (bound.ROOT / "sw_pt_robe/pmh22_robe254.mdl").read_bytes()
        self.assertTrue(mdl.binary(data))
        model = poses.Model(data, False)
        self.assertEqual({"head_g"}, set(model.skin_bindings()["hat"]))
        # Its exported local scale survives; the vertex scale is baked into
        # the skin so the rendered world shape is unchanged.
        hat = next(node for node in model.nodes if node[0] == "hat")
        self.assertAlmostEqual(.925926, hat[3][36][1][0][0], delta=1e-6)

    def test_every_catalog_model_has_no_unbound_static_attachments(self):
        manifest = json.loads(bound.MANIFEST.read_text())
        active = tint.find_active_models()
        parts = bound.AnimationParts(lambda name: active[name].read_bytes() if name in active else None)
        paths = bound.catalog_targets(manifest)
        self.assertGreater(len(paths), 7000, "Audit both native robes and all RGB roots")
        failures = []
        for path in paths:
            data = path.read_bytes()
            panels, _, _ = bound.panel_candidates(data, parts.inherited(data))
            if panels:
                failures.append((path.name, sorted(panels)))
        self.assertEqual([], failures, "Run BindRobePanels.py --all to validate and bind new static attachments")

    def test_all_single_bone_child_skins_follow_compiled_bone_transforms(self):
        manifest = json.loads(bound.MANIFEST.read_text())
        checked_models, checked_panels = 0, 0
        for path in bound.catalog_targets(manifest):
            data = path.read_bytes()
            if not mdl.binary(data):
                continue
            model = poses.Model(data, False)
            skins = model.skin_bindings()
            bindings = {}
            for name, parent, _, _, offset in model.nodes:
                if name not in skins or not model.uint(offset + 108) & 0x40:
                    continue
                current = parent
                while current is not None and not bound.BODY_BONE.fullmatch(model.nodes[current][0]):
                    current = model.nodes[current][1]
                if current is not None and set(skins[name]) == {model.nodes[current][0]}:
                    bindings[name] = model.nodes[current][0]
            if not bindings:
                continue
            # Exercise arbitrary translation, rotation and scaling at five phases.
            # This verifies the actual native inverse binds and every vertex,
            # including nested coverings, against the bone-following invariant.
            clip = (1, [(name, None, part,
                         {8: ((0., 1.), [(0, 0, .1), (.03, -.02, .15)]),
                          20: ((0., 1.), [(0, 0, 0, 1), (.1, 0, 0, .994987437)]),
                          36: ((0., 1.), [(1,), (1.15,)])}, 0)
                        for name, _, part, _, _ in model.nodes if name in set(bindings.values()) and part >= 0])
            with self.subTest(path=path.name):
                bound.validate_panel_poses(data, data, bindings, [clip])
            checked_models += 1
            checked_panels += len(bindings)
        self.assertGreaterEqual(checked_models, 664)
        self.assertGreaterEqual(checked_panels, 1200)


if __name__ == "__main__":
    unittest.main()
