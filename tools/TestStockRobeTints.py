"""Stock-only resources must be discovered without replacing HAK overrides."""
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
import GenerateTintMapAssets as tint
from ImportStockRobeTints import plan


class StockRobeTintTests(unittest.TestCase):
    def test_rebinding_after_conversion_uses_generated_material_not_deleted_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_directory = root / "source"
            output = root / "generated"
            source_directory.mkdir()
            output.mkdir()
            source = source_directory / "pmh0_robe003.mtr"
            generated = output / source.name
            source.write_text("texture1 authored_normal\nparameter float SpecularPower 17.0\n")
            with patch.object(tint, "REPOSITORY_ROOT", root), \
                 patch.object(tint, "OUTPUT_MTR_DIRECTORY", output), \
                 patch.object(tint, "_SOURCE_MTR_PATHS_BY_RESREF", None), \
                 patch.object(tint, "hak_directories", return_value=[source_directory, output]):
                material = tint.tint_material_text(generated, source.stem, "testmask", 64, 64)
                generated.write_text(material)
                self.assertEqual([source], tint.source_mtr_paths(source.stem))
                self.assertEqual(1, tint.remove_overridden_materials({source.stem: {}}))
                self.assertEqual([], tint.source_mtr_paths(source.stem))
                rebound = tint.tint_material_text(generated, source.stem, "testmask", 64, 64)
                self.assertIn("texture1 authored_normal", rebound)
                self.assertIn("parameter float SpecularPower 17.0", rebound)
                self.assertEqual(material, rebound)

    def test_imports_missing_racial_mesh_with_native_human_palette(self):
        rows = plan({"pme0_robe003": None}, {"pmh0_robe003": None}, {}, {}, set(), {3})
        self.assertEqual(rows, [{"model": "pme0_robe003", "palette": "pmh0_robe003",
                                 "importModel": True, "importPalette": True}])

    def test_preserves_authored_model_and_converted_mask(self):
        rows = plan({"pfh0_robe003": None}, {"pfh0_robe003": None}, {"pfh0_robe003": None},
                    {"pfh0_robe003": {}}, set(), {3})
        self.assertFalse(rows[0]["importModel"])
        self.assertFalse(rows[0]["importPalette"])

    def test_existing_hak_palette_wins_over_stock(self):
        rows = plan({"pfh0_robe003": None}, {"pfh0_robe003": None}, {}, {}, {"pfh0_robe003"}, {3})
        self.assertFalse(rows[0]["importPalette"])

    def test_racial_stock_palette_precedes_converted_human_palette(self):
        rows = plan({"pfe0_robe003": None}, {"pfe0_robe003": None}, {}, {"pfh0_robe003": {}}, set(), {3})
        self.assertEqual(rows[0]["palette"], "pfe0_robe003")
        self.assertTrue(rows[0]["importPalette"])

    def test_excludes_invalid_table_styles_and_other_phenotypes(self):
        self.assertEqual(plan({"pmh0_robe001": None, "pmh22_robe003": None}, {}, {}, {}, set(), {3}), [])

    def test_missing_palette_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no resolved palette"):
            plan({"pmh0_robe003": None}, {}, {}, {}, set(), {3})


if __name__ == "__main__":
    unittest.main()
