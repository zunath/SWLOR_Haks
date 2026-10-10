"""Cloaks must retain native geometry while using a generated body phenotype."""
import struct
import unittest
from unittest.mock import patch
import json
from pathlib import Path
import tempfile

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

    def test_compact_matrix_matches_every_required_resource(self):
        manifest = json.loads(cloaks.MANIFEST.read_text())
        models = cloaks.model_map(manifest)
        self.assertEqual(15195, len(models))
        self.assertEqual(114, len(manifest["sources"]))
        self.assertEqual(set(models.values()), set(manifest["sources"]))
        self.assertLess(cloaks.MANIFEST.stat().st_size, 40000)

    def test_archive_retains_existing_resources_and_exact_variant_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = bytes(600)
            source = bytearray(source)
            struct.pack_into("<I", source, 84, 244)
            source[20:84] = b"pmh0_cloak_004".ljust(64, b"\0")
            source[288:320] = b"pmh0_cloak_004".ljust(32, b"\0")
            manifest = {"phenotypes": {"pmh0": [34, 255]}, "geometry": [4],
                        "sources": {"pmh0_cloak_004": cloaks.digest(source)}, "unavailable_sources": []}
            (root / "pmh0_cloak_004.mdl").write_bytes(source)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            header = bytearray(160)
            header[:8] = b"HAK V1.0"
            struct.pack_into("<4I", header, 16, 1, 160, 160, 184)
            existing_key = struct.pack("<16sIHH", b"original", 0, 10, 0)
            archive = root / "proof.hak"
            original = bytes(header) + existing_key + struct.pack("<II", 192, 8) + b"original"
            archive.write_bytes(original)
            with patch.multiple(cloaks, SOURCES=root, MANIFEST=manifest_path), patch.object(cloaks, "check", return_value=[]):
                cloaks.pack(archive)
                data = archive.read_bytes()
                count, _, keys, resources = struct.unpack_from("<4I", data, 16)
                self.assertEqual(3, count)
                expected_resources = {b"original": b"original", **{
                    name.encode(): cloaks.rename_model(source, "pmh0_cloak_004", name)
                    for name in ("pmh34_cloak_004", "pmh255_cloak_004")}}
                for index in range(count):
                    name, resource, kind, _ = struct.unpack_from("<16sIHH", data, keys + index*24)
                    offset, size = struct.unpack_from("<II", data, resources + resource*8)
                    self.assertEqual(expected_resources.pop(name.rstrip(b"\0")), data[offset:offset+size])
                self.assertEqual({}, expected_resources)
                # Repacking must fail closed instead of appending duplicate resources.
                with self.assertRaisesRegex(ValueError, "collides"):
                    cloaks.pack(archive)
                self.assertEqual(data, archive.read_bytes())
                self.assertEqual([], list(root.glob("*.tmp")))

    def test_invalid_sources_do_not_touch_the_previous_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "existing.hak"
            archive.write_bytes(b"previous archive")
            with patch.object(cloaks, "check", return_value=["Missing canonical source"]):
                with self.assertRaisesRegex(ValueError, "Missing canonical"):
                    cloaks.pack(archive)
            self.assertEqual(b"previous archive", archive.read_bytes())


if __name__ == "__main__":
    unittest.main()
