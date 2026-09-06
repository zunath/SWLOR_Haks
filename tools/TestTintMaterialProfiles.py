#!/usr/bin/env python3
"""Guard native PLT selection independently from retained authored MTR inputs."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import GenerateTintMapAssets as g


class MaterialProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parts = self.root / "sw_pt_lshin"
        self.parts.mkdir()
        self.output = self.root / "sw_tint_mtr"
        self.output.mkdir()
        self.profiles = {}
        for name, value in (
            ("_MATERIAL_SOURCES", self.profiles), ("_MATERIAL_BITMAP_ALIASES", {}),
            ("_MTR_PATHS_BY_RESREF", {}), ("_SOURCE_MTR_PATHS_BY_RESREF", {}),
            ("_PROFILE_SIGNATURES", {}), ("_PROFILE_ALIASES", {}), ("_PRESERVED_MATERIALS", {}),
            ("OUTPUT_MTR_DIRECTORY", self.output), ("_NATIVE_MODULAR_PALETTES", set()),
        ):
            mock = patch.object(g, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def model(self, bitmap="shin", material=""):
        path = self.parts / "pme0_shinl249.mdl"
        path.write_text(f"newmodel {path.stem}\nnode trimesh {path.stem}\n bitmap {bitmap}\n materialname {material or 'NULL'}\nendnode\n")
        return path

    def choices(self, path):
        return g.native_modular_material_choices(path, {"pmh0_shinl249": {"layers": [7]}})

    def test_unmapped_bitmap_does_not_inherit_canonical_normal_maps(self):
        (self.output / "pmh0_shinl249.mtr").write_text("texture1 canonical_n\ntexture2 canonical_s\n")
        choice = self.choices(self.model())["@node:pme0_shinl249"]
        self.assertEqual(choice, ("pmh0_shinl249", "", []))
        self.assertNotEqual(g.material_profile_signature(choice[0]), g.material_profile_signature(choice[0], (choice[1], choice[2])))
        text = g.tint_material_text(self.output / "alias.mtr", "alias", "mask", 128, 128, choice[0], (choice[1], choice[2]))
        self.assertIn("customshaderFS fs_plt_tinter\n", text)
        self.assertNotIn("canonical_n", text)

    def test_original_mapped_inputs_win_over_canonical_palette_inputs(self):
        self.profiles["authored"] = {"lines": ["texture1 authored_n", "texture2 authored_s", "parameter float Roughness 0.7"]}
        source, name, lines = self.choices(self.model("authored"))["@node:pme0_shinl249"]
        text = g.tint_material_text(self.output / "alias.mtr", "alias", "mask", 128, 128, source, (name, lines))
        self.assertIn("texture1 authored_n\n", text)
        self.assertIn("texture2 authored_s\n", text)
        self.assertIn("parameter float Roughness 0.7\n", text)
        self.assertIn("customshaderFS fs_plt_tinter_nm\n", text)

    def test_fixed_diffuse_keeps_authored_custom_shader_outside_tint_catalog(self):
        self.profiles["fixed"] = {"lines": ["texture0 fixed", "customshaderFS fsFlowmap", "texture6 flow_south"]}
        path = self.model("fixed", "pmh0_shinl249")
        with (
            patch.object(g, "find_active_models", return_value={path.stem: path}),
            patch.object(g, "find_modular_human_material_fallbacks", return_value={}),
            patch.object(g, "find_authored_texture_overrides", return_value={}),
            patch.object(g, "find_active_render_surfaces", return_value={"fixed"}),
        ):
            rows, pending, aliases = g.build_model_material_plan({"pmh0_shinl249": {"layers": [7]}})
        self.assertEqual(rows, [])
        self.assertEqual(aliases, {})
        target = pending[path]["@node:pme0_shinl249"]
        self.assertEqual(g._PRESERVED_MATERIALS[target][1], self.profiles["fixed"]["lines"])

    def test_missing_explicit_diffuse_still_suppresses_native_plt(self):
        self.profiles["missing"] = {"lines": ["texture0 absent_raster"], "resolvedTexture0": None}
        self.assertIsNone(self.choices(self.model("missing"))["@node:pme0_shinl249"][0])

    def test_null_diffuse_retains_native_palette_and_authored_other_maps(self):
        self.profiles["mapped"] = {"lines": ["texture0 NULL", "texture1 skin_n"]}
        self.assertEqual(self.choices(self.model("mapped"))["@node:pme0_shinl249"][0], "pmh0_shinl249")

    def test_raw_null_bitmap_does_not_autoload_invented_same_name_material(self):
        self.profiles["pme0_shinl249"] = {"lines": ["texture0 fixed"]}
        self.assertEqual(self.choices(self.model("NULL"))["@node:pme0_shinl249"], ("pmh0_shinl249", "", []))

    def test_retired_bitmap_alias_recovers_original_mapped_profile(self):
        self.profiles["authored"] = {"lines": ["texture1 authored_n"]}
        g._MATERIAL_BITMAP_ALIASES["old_alias"] = "authored"
        self.assertEqual(self.choices(self.model("old_alias"))["@node:pme0_shinl249"][1:], ("authored", ["texture1 authored_n"]))

    def test_normal_alpha_cutout_survives_profile_conversion(self):
        text = g.tint_material_text(self.output / "alias.mtr", "alias", "mask", 16, 16, "palette", ("authored_neck", ["customshaderFS pfh0_neck199", "texture1 own_neck_n", "transparencyhint 1"]))
        self.assertIn("texture1 own_neck_n\n", text)
        self.assertIn("parameter float useTexture1Alpha 1.0\n", text)

    def test_audit_rejects_missing_or_modified_preserved_original_material(self):
        g._PRESERVED_MATERIALS["original_alias"] = ("original", ["texture0 original", "customshaderFS fsFlowmap"])
        with patch.object(g, "REPOSITORY_ROOT", self.root):
            self.assertIn("missing preserved authored", g.preserved_material_errors()[0])
            item = self.root / "sw_item"
            item.mkdir()
            path = item / "original_alias.mtr"
            path.write_text("texture0 original\ncustomshaderFS fsFlowmap\n")
            self.assertEqual(g.preserved_material_errors(), [])
            path.write_text("texture0 plt_white\ncustomshaderFS fs_plt_tinter\n")
            self.assertIn("differs from its original", g.preserved_material_errors()[0])


class BaseBodyPaletteTests(unittest.TestCase):
    def test_restored_master_hand_models_use_their_original_full_size_palettes(self):
        # The hand meshes/UVs were restored from master; the earlier stock mask
        # is 64px and lays the hand shading out differently despite sharing Skin.
        entries = g.load_source_manifest()
        for name in ("pmh0_handl001", "pmh0_handr001"):
            with self.subTest(name=name):
                entry = entries[name]
                self.assertEqual(entry["sourceSha256"], "2d71bdc7106ac85a2d28d08824e7e7f2193210a3fc5aa5bfde0f8b4e075983c8")
                self.assertEqual((entry["width"], entry["height"]), (256, 256))
                self.assertEqual(entry["layers"], [0])
                path = g.packed_dds_path(name, entry)
                self.assertIsNone(g.check_dds(path, 256, 256))
                self.assertEqual(path.with_suffix(".txi").read_text().strip(), "mipmap 0")
                for material in [name] + entry.get("aliases", []):
                    lines = g.mtr_path(material).read_text().splitlines()
                    self.assertIn("parameter float tintMapWidth 256.0", lines)
                    self.assertIn("parameter float tintMapHeight 256.0", lines)
                    self.assertIn(f"texture7 {entry['texture']}", lines)

    def test_stock_foot_models_retain_their_matching_stock_palettes(self):
        entries = g.load_source_manifest()
        for name in ("pmh0_footl001", "pmh0_footr001"):
            with self.subTest(name=name):
                self.assertEqual(entries[name]["sourceSha256"], "c24bb70dcc7e06c20af52193b908f36da048381712e23c1979cfe480de8af1d5")
                self.assertEqual((entries[name]["width"], entries[name]["height"]), (64, 64))


if __name__ == "__main__":
    unittest.main()
