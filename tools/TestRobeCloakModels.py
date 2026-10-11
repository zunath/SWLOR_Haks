"""Cloaks must retain native geometry while using a generated body phenotype."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import GenerateRobeCloakModels as cloaks
import ModelLodAssets as lod


class RobeCloakModelTests(unittest.TestCase):
    def test_quoted_labels_select_geometry_instead_of_texture_or_icon(self):
        mappings = "2DA V2.0\n\nMODEL PHENOTYPE BASEPHENOTYPE\n0 pmh0_robe007 34 0\n1 pfh0_robe007 34 0\n"
        table = '2DA V2.0\n\nLABEL MODEL TEXTURE ICON\n0 **** 0 0 0\n1 "Great Cloak" 2 62 8\n2 Plain 1 1 1\n'
        self.assertEqual({"pmh34_cloak_002": "pmh0_cloak_002",
                          "pfh34_cloak_002": "pfh0_cloak_002",
                          "pmh34_cloak_001": "pmh0_cloak_001",
                          "pfh34_cloak_001": "pfh0_cloak_001"}, cloaks.required_models(mappings, table))

    def test_native_byte_phenotypes_fit_the_resource_limit(self):
        result = cloaks.required_models("2DA V2.0\n\nCOLUMNS\n0 pmh0_robe007 255 0\n",
                                       "2DA V2.0\n\nCOLUMNS\n1 Plain 150 1 1\n")
        self.assertEqual({"pmh255_cloak_150": "pmh0_cloak_150"}, result)

    def test_renaming_retains_every_geometry_and_bind_byte(self):
        data = bytearray((index % 256 for index in range(600)))
        data[:4] = bytes(4)
        original = "pmh0_cloak_004"
        struct.pack_into("<I", data, 84, 244)
        data[20:84] = original.encode().ljust(64, b"\0")
        data[288:320] = original.encode().ljust(32, b"\0")
        output = cloaks.rename_model(bytes(data), original, "pmh34_cloak_004")
        restored = cloaks.rename_model(output, "pmh34_cloak_004", original)
        self.assertEqual(bytes(data), restored)
        self.assertEqual(data[84:288], output[84:288])
        self.assertEqual(data[320:], output[320:])

    def test_wrong_root_names_are_rejected(self):
        data = bytearray(600)
        struct.pack_into("<I", data, 84, 244)
        data[20:84] = b"pmh0_cloak_004".ljust(64, b"\0")
        data[288:320] = b"unrelated".ljust(32, b"\0")
        with self.assertRaisesRegex(ValueError, "Unexpected cloak model/root"):
            cloaks.rename_model(bytes(data), "pmh0_cloak_004", "pmh34_cloak_004")

    def test_packaged_models_cover_the_current_tables_and_match_validated_bytes(self):
        self.assertEqual([], cloaks.check())

    def test_regenerated_models_replace_their_redirects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("sw_pt_cloak", "tools"):
                (root / folder).mkdir()
            (root / "sw_pt_cloak/pfh0_cloak_004.mdl").write_bytes(b"canonical")
            (root / "sw_pt_cloak/pfh171_cloak_004.lod").write_bytes(b"pfh0_cloak_004\n")
            aliases = {"sw_pt_cloak/pfh171_cloak_004.mdl": {"canonical": "sw_pt_cloak/pfh0_cloak_004.mdl"},
                       "sw_pt_cloak/pfh172_cloak_004.mdl": {"canonical": "sw_pt_cloak/pfh0_cloak_004.mdl"}}
            (root / lod.MANIFEST).write_text(json.dumps({"aliases": aliases, "native_overrides": ["helm_001"]}))
            replaced = cloaks.install({"pfh171_cloak_004": b"generated", "pfh173_cloak_004": b"new"}, root)
            self.assertEqual(["sw_pt_cloak/pfh171_cloak_004.mdl"], replaced)
            self.assertFalse((root / "sw_pt_cloak/pfh171_cloak_004.lod").exists())
            self.assertEqual(b"generated", (root / "sw_pt_cloak/pfh171_cloak_004.mdl").read_bytes())
            manifest = json.loads((root / lod.MANIFEST).read_text())
            self.assertEqual({"sw_pt_cloak/pfh172_cloak_004.mdl"}, set(manifest["aliases"]))
            self.assertEqual(["helm_001"], manifest["native_overrides"])
            # No MDL is left shadowed by a same-name LOD.
            self.assertEqual(3, len(lod.model_paths(root / "sw_pt_cloak")))

    def test_sources_recorded_as_unavailable_are_rechecked(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "RobeCloakModels.json"
            manifest.write_text(json.dumps({"models": {}, "unavailable_sources": ["pfa0_cloak_100"],
                                            "files": {}, "sources": {}}))
            required = {"pfa171_cloak_100": "pfa0_cloak_100"}
            with patch.object(cloaks, "MANIFEST", manifest), patch.object(cloaks, "requirements", lambda: required):
                with patch.object(cloaks.tint, "find_active_models", lambda: {}):
                    self.assertEqual([], cloaks.check())
                added = {"pfa0_cloak_100": Path(directory) / "pfa0_cloak_100.mdl"}
                with patch.object(cloaks.tint, "find_active_models", lambda: added):
                    self.assertTrue(any("now exist" in error and "pfa0_cloak_100" in error for error in cloaks.check()))


if __name__ == "__main__":
    unittest.main()
