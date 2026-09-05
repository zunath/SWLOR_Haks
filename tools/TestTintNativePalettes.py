#!/usr/bin/env python3
"""Protect native robe metadata without turning control pixels into artwork."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import GenerateTintMapAssets as g


class NativeRobePaletteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.materials = self.root / "sw_tint_mtr"
        self.materials.mkdir()
        self.parts = self.root / "sw_pt_robe"
        self.parts.mkdir()
        for name, value in (
            ("OUTPUT_MTR_DIRECTORY", self.materials), ("_NATIVE_MODULAR_PALETTES", set()),
            ("_NATIVE_ROBE_SOURCES", set()), ("_NATIVE_ROBE_MATERIALS", set()),
            ("_MATERIAL_SOURCES", {}), ("_MATERIAL_BITMAP_ALIASES", {}),
            ("_MTR_PATHS_BY_RESREF", {}), ("_SOURCE_MTR_PATHS_BY_RESREF", {}),
            ("_PROFILE_SIGNATURES", {}), ("_PROFILE_ALIASES", {}), ("_PRESERVED_MATERIALS", {}),
            ("_ACTIVE_RENDER_SURFACES", None),
        ):
            override = patch.object(g, name, value)
            override.start()
            self.addCleanup(override.stop)

    def model(self, name="pfe22_robe187", node=None):
        path = self.parts / (name + ".mdl")
        path.write_text(f"newmodel {name}\nnode trimesh {node or name}\n bitmap cloth\n materialname pfh0_robe187\nendnode\n")
        return path

    def plan(self, path, entries=None, other_models=()):
        entries = entries or {"pfh0_robe187": {"layers": [0, 4]}}
        with (
            patch.object(g, "find_active_models", return_value={p.stem: p for p in (path, *other_models)}),
            patch.object(g, "find_modular_human_material_fallbacks", return_value={}),
            patch.object(g, "find_authored_texture_overrides", return_value={}),
            patch.object(g, "find_active_render_surfaces", return_value=set()),
        ):
            return g.build_model_material_plan(entries)

    def test_control_is_valid_one_pixel_plt_with_no_artwork_dependencies(self):
        path = self.materials / "pfh0_robe187.plt"
        path.write_bytes(g.native_robe_control_bytes())
        width, height, shade, layer, _ = g.read_plt(path)
        self.assertEqual((width, height, len(path.read_bytes())), (1, 1, 26))
        self.assertEqual((int(shade[0, 0]), int(layer[0, 0])), (128, 0))

    def test_controls_are_neither_conversion_inputs_nor_authored_surfaces(self):
        path = self.materials / "pfh0_robe187.plt"
        path.write_bytes(g.native_robe_control_bytes())
        with patch.object(g, "hak_directories", return_value=(self.materials, self.parts)):
            self.assertFalse(g.is_tint_material_plt(path))
            self.assertEqual(g.find_tint_material_plts(), ({}, []))
            self.assertNotIn(path.stem, g.find_active_render_surfaces())

    def test_corrupt_control_cannot_be_reconverted_into_authoritative_pixels(self):
        path = self.materials / "pfh0_robe187.plt"
        path.write_bytes(b"damaged control")
        g._NATIVE_ROBE_SOURCES = {path.stem}
        self.assertFalse(g.is_tint_material_plt(path))
        self.assertTrue(g.native_robe_control_errors())
        with self.assertRaisesRegex(RuntimeError, "unrecognized"):
            g.synchronize_native_robe_controls()
        self.assertEqual(path.read_bytes(), b"damaged control")

    def test_controls_follow_exact_native_race_phenotype_fallback(self):
        rows, _, _ = self.plan(self.model())
        self.assertEqual(g._NATIVE_ROBE_SOURCES, {"pfh0_robe187"})
        self.assertEqual(g._NATIVE_ROBE_MATERIALS, {rows[0][1]})
        g.synchronize_native_robe_controls()
        actual = {path.stem for path in self.materials.glob("*.plt")}
        chosen = next(name for name in g.modular_palette_candidates("pfe22_robe187") if name in actual)
        self.assertEqual(chosen, "pfh0_robe187")
        self.assertEqual(g.native_robe_control_errors(), [])

    def test_stock_backed_source_needs_no_control_override(self):
        self.plan(self.model())
        g._NATIVE_MODULAR_PALETTES = {"pfh0_robe187"}
        g.synchronize_native_robe_controls()
        self.assertEqual(list(self.materials.glob("*.plt")), [])
        self.assertEqual(g.native_robe_control_errors(), [])

    def test_higher_priority_stock_palette_stops_control_inference(self):
        g._NATIVE_MODULAR_PALETTES = {"pfe0_robe187"}
        self.plan(self.model())
        self.assertEqual(g._NATIVE_ROBE_SOURCES, set())
        self.assertEqual(g._NATIVE_ROBE_MATERIALS, set())

    def test_missing_native_named_subtree_never_opts_in(self):
        self.plan(self.model(node="different_root"))
        self.assertEqual(g._NATIVE_ROBE_SOURCES, set())
        self.assertEqual(g._NATIVE_ROBE_MATERIALS, set())

    def test_nonrobe_models_never_create_controls(self):
        self.plan(self.model("pfe0_head187"), {"pfh0_head187": {"layers": [0]}})
        self.assertEqual(g._NATIVE_ROBE_SOURCES, set())
        self.assertEqual(g._NATIVE_ROBE_MATERIALS, set())

    def test_unproven_shared_consumers_get_scripted_alias_without_changing_proven_material(self):
        native = self.model()
        legacy = self.model("pfev_robe187")
        legacy.write_text(legacy.read_text().replace("bitmap cloth", "bitmap pfh0_robe187"))
        rows, pending, aliases = self.plan(native, other_models=(legacy,))
        by_model = {name: material for name, material, _ in rows}
        self.assertEqual(by_model[native.stem], "pfh0_robe187")
        self.assertNotEqual(by_model[legacy.stem], "pfh0_robe187")
        self.assertEqual(aliases[by_model[legacy.stem]], "pfh0_robe187")
        self.assertEqual(g._NATIVE_ROBE_MATERIALS, {"pfh0_robe187"})
        self.assertNotIn(native, pending)
        self.assertIn(legacy, pending)

    def test_surface_audit_rejects_flagged_material_outside_native_subtree(self):
        path = self.model("pfev_robe187")
        g._NATIVE_ROBE_MATERIALS = {"pfh0_robe187"}
        errors = g.native_robe_surface_errors({path.stem: path}, {"pfh0_robe187": {"layers": [0]}},
                                            [(path.stem, "pfh0_robe187", [0])])
        self.assertTrue(errors)

    def test_shader_audit_rejects_native_override_of_explicit_scripted_row(self):
        shader = """uniform float PLTscheme[15]; uniform float useNativePalette;
        if (v < 0.0 && useNativePalette > 0.5) {
            float colorId = mod(floor(PLTscheme[int(layer)] * 1792.0 + 0.5), 256.0);
        }"""
        self.assertEqual(g.native_robe_shader_errors(shader), [])
        self.assertTrue(g.native_robe_shader_errors(shader.replace("v < 0.0 && ", "")))
        self.assertTrue(g.native_robe_shader_errors(shader.replace("256.0", "176.0")))

    def test_regeneration_is_idempotent_and_retains_native_icons_and_cloaks(self):
        icon = self.parts / "ipf_robe187.plt"
        cloak_dir = self.root / "sw_pt_cloak"
        cloak_dir.mkdir()
        cloak = cloak_dir / "cloak_017.plt"
        icon.write_bytes(b"authored icon")
        cloak.write_bytes(b"authored cloak")
        self.plan(self.model())
        g.synchronize_native_robe_controls()
        before = {path.name: path.read_bytes() for path in self.materials.iterdir()}
        g.synchronize_native_robe_controls()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.materials.iterdir()})
        self.assertEqual(icon.read_bytes(), b"authored icon")
        self.assertEqual(cloak.read_bytes(), b"authored cloak")
        self.assertTrue(g.is_inventory_icon_plt(icon))
        self.assertTrue(g.is_dynamic_cloak_plt(cloak))

    def test_only_recognized_stale_controls_can_be_removed(self):
        stale = self.materials / "pfh0_robe188.plt"
        stale.write_bytes(g.native_robe_control_bytes())
        self.assertTrue(g.native_robe_control_errors())
        g.synchronize_native_robe_controls()
        self.assertFalse(stale.exists())

    def test_robe_rows_use_negative_sentinel_without_changing_visual_inputs(self):
        path = self.materials / "robe_alias.mtr"
        original = "texture1 cloth_n\ntexture2 cloth_s\nparameter float Roughness 0.7\n"
        path.write_text(original)
        text = g.tint_material_text(path, "robe_alias", "mask", 512, 512, native_palette=True)
        for name, _ in g.TINT_ROW_PARAMETERS:
            self.assertIn(f"parameter float {name} -1.0\n", text)
        self.assertIn("parameter float useNativePalette 1.0\n", text)
        self.assertIn(original, text)
        self.assertIn("texture0 plt_white\n", text)

    def test_leaving_robe_scope_restores_normal_defaults_and_removes_flag(self):
        path = self.materials / "robe_alias.mtr"
        path.write_text(g.tint_material_text(path, "robe_alias", "mask", 512, 512, native_palette=True))
        text = g.tint_material_text(path, "robe_alias", "mask", 512, 512)
        self.assertNotIn("useNativePalette", text)
        for line in g.TINT_ROW_PARAMETER_LINES:
            self.assertIn(line + "\n", text)

    def test_metal_palette_audit_rejects_rgb_and_environment_alpha_drift(self):
        path = self.root / "palette.tga"
        header = bytearray(18)
        header[2] = 2
        struct.pack_into("<HH", header, 12, 256, 2048)
        header[16] = 32
        data = header + bytearray(256 * 2048 * 4)
        path.write_bytes(data)
        self.assertEqual(g.native_metal_palette_errors(path), [])
        for channel in (0, 3):
            changed = bytearray(data)
            changed[18 + 528 * 256 * 4 + channel] = 1
            path.write_bytes(changed)
            self.assertTrue(g.native_metal_palette_errors(path))
        path.write_bytes(data[:-1])
        self.assertTrue(g.native_metal_palette_errors(path))

    def test_native_flag_cannot_select_a_vector_uniform_upload(self):
        path = self.materials / "robe.mtr"
        text = g.tint_material_text(path, "robe", "mask", 512, 512, native_palette=True)
        path.write_text(text)
        self.assertEqual(g.check_tint_mtr_structure(path), [])
        path.write_text(text.replace("useNativePalette 1.0", "useNativePalette 1.0 1.0 1.0 1.0"))
        self.assertTrue(any("single scalar" in error for error in g.check_tint_mtr_structure(path)))


if __name__ == "__main__":
    unittest.main()
