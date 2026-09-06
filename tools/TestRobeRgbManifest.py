"""Allocation and manifest guards must fail before touching model resources."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import GenerateRobeRgbModels as g
import GenerateTintMapAssets as tint


class RobeRgbManifestTests(unittest.TestCase):
    def test_text_manifest_hashes_are_checkout_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in (".py", ".2da"):
                path = Path(directory) / ("source" + extension)
                path.write_bytes(b"first\nsecond\n")
                expected = g.file_digest(path)
                path.write_bytes(b"first\r\nsecond\r\n")
                self.assertEqual(expected, g.file_digest(path))
                path.write_bytes(b"first\r\nchanged\r\n")
                self.assertNotEqual(expected, g.file_digest(path))

    def test_unused_table_rows_with_existing_root_or_attachment_are_reserved(self):
        active = {"pfa34": None, "pmh35_robe007": None, "pfg036_robe187": None}
        result = g.allocate_phenotypes([3], {}, ["2DA V2.0", "", "Label"], active)
        self.assertEqual({3: 37}, result)

    def test_retired_assignments_are_never_recycled(self):
        table = ["2DA V2.0", "", "Label", "34 RobeRgb_003 **** 0"]
        self.assertEqual({3: 34, 7: 35}, g.allocate_phenotypes([7], {}, table, {}))

    def test_conflicting_persisted_mapping_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            g.allocate_phenotypes([3], {"pmh0_robe003": 35},
                ["2DA V2.0", "", "Label", "34 RobeRgb_003 **** 0"], {})

    def test_only_byte_verified_prior_outputs_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "ROOT", Path(directory)):
            root = Path(directory) / "sw_pt_root"
            root.mkdir()
            path = root / "pmh34.mdl"
            path.write_bytes(b"owned model")
            selected = {"pmh0_robe003": None}
            prior = {"pmh0_robe003": 34}
            active = {"pmh34": path}
            manifest = {"files": {"sw_pt_root/pmh34.mdl": g.file_digest(path)}}
            g.validate_output_ownership(selected, {3: 34}, prior, active, manifest)
            for wrong_prior, wrong_manifest in (({}, manifest), (prior, {"files": {}})):
                with self.assertRaisesRegex(ValueError, "not a verified prior"):
                    g.validate_output_ownership(selected, {3: 34}, wrong_prior, active, wrong_manifest)
            path.write_bytes(b"unrelated model")
            with self.assertRaisesRegex(ValueError, "not a verified prior"):
                g.validate_output_ownership(selected, {3: 34}, prior, active, manifest)

    def test_compile_helpers_are_required_manifest_inputs(self):
        self.assertIn("CompileModels.py", g.GENERATOR_INPUTS)
        manifest = json.loads(g.MANIFEST.read_text())
        for name in g.GENERATOR_INPUTS:
            self.assertEqual(g.file_digest(g.ROOT / "tools" / name), manifest["files"]["tools/" + name])

    def test_atlas_audit_rejects_nonmetal_texels_and_header_corruption(self):
        original = tint.PALETTE_TEXTURE.read_bytes()
        self.assertEqual([], tint.palette_atlas_errors(tint.PALETTE_TEXTURE))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "palette.tga"
            for offset in (17, 18, 18 + 704 * 256 * 4, 18 + 1056 * 256 * 4, len(original) - 1):
                corrupted = bytearray(original)
                corrupted[offset] ^= 1
                path.write_bytes(corrupted)
                self.assertTrue(tint.palette_atlas_errors(path), f"undetected corruption at {offset}")


if __name__ == "__main__":
    unittest.main()
