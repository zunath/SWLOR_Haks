#!/usr/bin/env python3
"""Regression checks for NWN's segmented-body PLT selection and replacement.

Run: python -B tools/TestTintModularFallbacks.py
"""

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import GenerateTintMapAssets as generator


class ModularTintFallbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parts = self.root / "sw_pt_lshin"
        self.parts.mkdir()

    def model(self, name, nodes="  bitmap shin\n"):
        path = self.parts / (name + ".mdl")
        path.write_text(
            f"newmodel {name}\nnode trimesh {name}\n{nodes}endnode\n",
            encoding="ascii",
        )
        return path

    def references(self, path, entries, native=()):
        with patch.object(generator, "native_modular_palettes", return_value=set(native)):
            return generator.find_native_modular_tint_references(path, entries)

    def test_native_order_includes_race_zero_human_zero_and_female_male_fallback(self):
        self.assertEqual(generator.modular_palette_candidates("pfe22_shinl249"), (
            "pfe22_shinl249", "pfe0_shinl249", "pfh0_shinl249", "pmh0_shinl249",
        ))

    def test_duplicate_native_candidates_are_removed_without_reordering(self):
        self.assertEqual(generator.modular_palette_candidates("pmh0_shinl249"), ("pmh0_shinl249",))

    def test_nonmodular_names_have_no_guessed_palette(self):
        self.assertEqual(generator.modular_palette_candidates("custom_creature"), ())

    def test_first_converted_native_candidate_wins(self):
        entries = {name: {} for name in generator.modular_palette_candidates("pfe22_shinl249")}
        self.assertEqual(generator.modular_palette_source("pfe22_shinl249", entries, set()), "pfe22_shinl249")

    def test_racial_zero_palette_precedes_human(self):
        entries = {"pfe0_shinl249": {}, "pfh0_shinl249": {}}
        self.assertEqual(generator.modular_palette_source("pfe22_shinl249", entries, set()), "pfe0_shinl249")

    def test_human_palette_uses_phenotype_zero(self):
        self.assertEqual(generator.modular_palette_source("pme2_shinl249", {"pmh0_shinl249": {}}, set()), "pmh0_shinl249")

    def test_female_can_use_final_male_palette(self):
        self.assertEqual(generator.modular_palette_source("pfe0_shinl249", {"pmh0_shinl249": {}}, set()), "pmh0_shinl249")

    def test_male_never_falls_back_to_female_palette(self):
        self.assertIsNone(generator.modular_palette_source("pme0_shinl249", {"pfh0_shinl249": {}}, set()))

    def test_existing_stock_palette_stops_lower_priority_converted_fallback(self):
        self.assertIsNone(generator.modular_palette_source("pfe22_shinl249", {"pfh0_shinl249": {}}, {"pfe0_shinl249"}))

    def test_converted_resource_supersedes_stock_of_same_name(self):
        self.assertEqual(generator.modular_palette_source("pme0_shinl249", {"pmh0_shinl249": {}}, {"pmh0_shinl249"}), "pmh0_shinl249")

    def test_missing_palette_preserves_authored_material_path(self):
        path = self.model("pme0_shinl249", "  bitmap authored\n  materialname authored_mtr\n")
        self.assertIsNone(self.references(path, {}))

    def test_selected_palette_replaces_real_bitmap_and_custom_material(self):
        path = self.model("pme0_shinl249", "  bitmap c_nymph\n  materialname authored_mtr\n")
        self.assertEqual(self.references(path, {"pmh0_shinl249": {}}), {"@node:pme0_shinl249": "pmh0_shinl249"})

    def test_selected_palette_replaces_exporter_placeholder(self):
        path = self.model("pme0_shinl249")
        self.assertEqual(self.references(path, {"pmh0_shinl249": {}}), {"@node:pme0_shinl249": "pmh0_shinl249"})

    def test_native_subtree_also_includes_hidden_meshes(self):
        path = self.model("pme0_shinl249", "  render 0\n  bitmap NULL\n")
        self.assertEqual(self.references(path, {"pmh0_shinl249": {}}), {"@node:pme0_shinl249": "pmh0_shinl249"})

    def test_native_subtree_includes_implicit_bitmap_meshes(self):
        path = self.model("pme0_shinl249", "  render 1\n")
        self.assertEqual(self.references(path, {"pmh0_shinl249": {}}), {"@node:pme0_shinl249": "pmh0_shinl249"})

    def test_exact_human_palette_also_replaces_authored_bitmap(self):
        path = self.model("pmh0_shinl249", "  bitmap authored\n")
        self.assertEqual(self.references(path, {"pmh0_shinl249": {}}), {"@node:pmh0_shinl249": "pmh0_shinl249"})

    def test_full_creature_models_do_not_use_body_part_replacement(self):
        path = self.root / "pme0_shinl249.mdl"
        path.write_text("newmodel pme0_shinl249\n", encoding="ascii")
        self.assertIsNone(self.references(path, {"pmh0_shinl249": {}}))

    def test_missing_named_subtree_keeps_existing_materials(self):
        path = self.model("pme0_shinl249")
        path.write_text(path.read_text().replace("node trimesh pme0_shinl249", "node trimesh wrong_root"))
        self.assertIsNone(self.references(path, {"pmh0_shinl249": {}}))

    def test_ascii_named_subtree_does_not_rewrite_sibling_with_same_bitmap(self):
        path = self.model("pme0_shinl249")
        path.write_text(
            "newmodel pme0_shinl249\nnode dummy outer\n parent NULL\nendnode\n"
            "node trimesh pme0_shinl249\n parent outer\n bitmap shin\nendnode\n"
            "node trimesh child\n parent pme0_shinl249\n render 0\n bitmap shin\nendnode\n"
            "node trimesh sibling\n parent outer\n bitmap shin\n materialname authored\nendnode\n"
        )
        references = self.references(path, {"pmh0_shinl249": {}})
        self.assertEqual(set(references), {"@node:pme0_shinl249", "@node:child"})
        sibling_before = path.read_text().split("node trimesh sibling")[1]
        self.assertTrue(generator.synchronize_model_material_bindings(path, references))
        self.assertEqual(path.read_text().split("node trimesh sibling")[1], sibling_before)
        self.assertEqual(path.read_text().count("materialname pmh0_shinl249"), 2)

    def test_native_subtree_does_not_treat_particle_texture_as_mesh(self):
        path = self.model("pme0_shinl249")
        path.write_text(path.read_text() +
            "node emitter sparks\n parent pme0_shinl249\n bitmap sparks\nendnode\n")
        references = self.references(path, {"pmh0_shinl249": {}})
        self.assertEqual(set(references), {"@node:pme0_shinl249"})

    def test_binary_named_subtree_changes_only_its_own_material_field(self):
        path = self.parts / "pme0_shinl249.mdl"
        data = bytearray(1612)
        struct.pack_into("<III", data, 0, 0, 1600, 0)
        struct.pack_into("<I", data, 84, 232)
        struct.pack_into("<II", data, 12 + 344, 352, 976)
        for pointer, name, flags in ((232, "outer", 1), (352, "pme0_shinl249", 33), (976, "sibling", 33)):
            node = 12 + pointer
            data[node + 32:node + 64] = name.encode().ljust(32, b"\0")
            struct.pack_into("<I", data, node + 108, flags)
            if flags & 32:
                data[node + 112 + 120:node + 112 + 184] = b"shin".ljust(64, b"\0")
        struct.pack_into("<II", data, 12 + 232 + 72, 344, 2)
        path.write_bytes(data)
        references = self.references(path, {"pmh0_shinl249": {}})
        material_offset = 12 + 352 + 112 + 312
        self.assertEqual(references, {f"@field:{material_offset}": "pmh0_shinl249"})
        self.assertTrue(generator.synchronize_model_material_bindings(path, references))
        expected = bytearray(data)
        expected[material_offset:material_offset + 64] = b"pmh0_shinl249".ljust(64, b"\0")
        self.assertEqual(path.read_bytes(), expected)

    def test_shadow_audit_exempts_only_the_native_named_subtree(self):
        path = self.model("pme0_shinl249")
        path.write_text(
            "newmodel pme0_shinl249\nnode dummy outer\n parent NULL\nendnode\n"
            "node trimesh pme0_shinl249\n parent outer\n bitmap authored\n materialname pmh0_shinl249\nendnode\n"
            "node trimesh sibling\n parent outer\n bitmap authored\n materialname pmh0_shinl249\nendnode\n"
        )
        with (
            patch.object(generator, "find_active_models", return_value={path.stem: path}),
            patch.object(generator, "native_modular_palettes", return_value=set()),
            patch.object(generator, "find_active_render_surfaces", return_value={"authored"}),
            patch.object(generator, "load_authored_texture_overrides", return_value={}),
        ):
            self.assertEqual(generator.find_generated_materials_shadowing_authored_surfaces(
                {"pmh0_shinl249": {}}), [("pme0_shinl249", "authored", "pmh0_shinl249")])

    def test_shadow_audit_does_not_exempt_missing_native_root(self):
        path = self.model("pme0_shinl249", " bitmap authored\n materialname pmh0_shinl249\n")
        path.write_text(path.read_text().replace("node trimesh pme0_shinl249", "node trimesh wrong_root"))
        with (
            patch.object(generator, "find_active_models", return_value={path.stem: path}),
            patch.object(generator, "native_modular_palettes", return_value=set()),
            patch.object(generator, "find_active_render_surfaces", return_value={"authored"}),
            patch.object(generator, "load_authored_texture_overrides", return_value={}),
        ):
            self.assertEqual(len(generator.find_generated_materials_shadowing_authored_surfaces(
                {"pmh0_shinl249": {}})), 1)

    def test_material_plan_finds_missing_rows_and_binding_before_and_after_repair(self):
        human = self.model("pmh0_shinl249", "  bitmap shin\n  materialname pmh0_shinl249\n")
        racial = self.model("pme0_shinl249")
        entries = {"pmh0_shinl249": {"layers": [7]}}
        with (
            patch.object(generator, "find_active_models", return_value={human.stem: human, racial.stem: racial}),
            patch.object(generator, "native_modular_palettes", return_value=set()),
            patch.object(generator, "find_active_render_surfaces", return_value=set()),
            patch.object(generator, "find_modular_human_material_fallbacks", return_value={}),
            patch.object(generator, "find_authored_texture_overrides", return_value={}),
        ):
            rows, pending, aliases = generator.build_model_material_plan(entries)
            self.assertIn(("pme0_shinl249", "pmh0_shinl249", [7]), rows)
            self.assertEqual(pending, {racial: {"@node:pme0_shinl249": "pmh0_shinl249"}})
            self.assertEqual(aliases, {})
            self.assertTrue(generator.synchronize_model_material_bindings(racial, pending[racial]))
            self.assertEqual(generator.build_model_material_plan(entries)[1], {})

    def test_stock_inventory_is_deterministic_and_records_only_palette_names(self):
        data = bytearray(64 + 3 * 22)
        data[:8] = b"KEY V1  "
        struct.pack_into("<IIII", data, 8, 0, 3, 64, 64)
        for index, (name, kind) in enumerate((("z_palette", 6), ("a_texture", 3), ("a_palette", 6))):
            struct.pack_into("<16sHI", data, 64 + index * 22, name.encode(), kind, index)
        (self.root / "test.key").write_bytes(data)
        actual = generator.stock_palette_inventory(self.root)
        self.assertEqual(actual["palettes"], ["a_palette", "z_palette"])
        self.assertEqual(actual["keys"][0]["name"], "test.key")
        self.assertEqual(len(actual["keys"][0]["sha256"]), 64)
        self.assertEqual(actual, generator.stock_palette_inventory(self.root))

    def test_duplicate_ascii_node_names_require_compilation_before_binding(self):
        path = self.model("pme0_shinl249")
        with path.open("a") as stream:
            stream.write("node trimesh PME0_SHINL249\n bitmap different\nendnode\n")
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "repeated ASCII node names.*CompileModels.py"):
            self.references(path, {"pmh0_shinl249": {}})
        self.assertEqual(path.read_bytes(), original)

    def test_conflicting_duplicate_palettes_cannot_follow_builder_order(self):
        other = self.root / "other"
        other.mkdir()
        (self.parts / "duplicate.plt").write_bytes(b"first")
        (other / "duplicate.plt").write_bytes(b"second")
        with patch.object(generator, "hak_directories", return_value=(self.parts, other)):
            with self.assertRaisesRegex(RuntimeError, "module's HAK priority"):
                generator.find_plts(lambda path: True)
            (other / "duplicate.plt").write_bytes(b"first")
            self.assertEqual(len(generator.find_plts(lambda path: True)[0]), 1)


if __name__ == "__main__":
    unittest.main()
