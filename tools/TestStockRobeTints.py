"""Stock-only resources must be discovered without replacing HAK overrides."""
import unittest
from ImportStockRobeTints import plan


class StockRobeTintTests(unittest.TestCase):
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
