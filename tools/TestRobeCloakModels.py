"""Cloaks must retain native geometry while using a generated body phenotype."""
import struct
import unittest

import GenerateRobeCloakModels as cloaks


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


if __name__ == "__main__":
    unittest.main()
