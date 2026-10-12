"""Packed HAKs must contain every checked-in LOD redirect."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import CheckPackedLods as packed


def erf(entries, version=b"V1.0"):
    name_size = 16 if version == b"V1.0" else 32
    keys = b"".join(name.encode().ljust(name_size, b"\0") + struct.pack("<IHH", index, restype, 0)
                    for index, (name, restype) in enumerate(entries))
    header = b"HAK " + version + struct.pack("<IIIIII", 0, 0, len(entries), 160, 160, 160 + len(keys))
    return header.ljust(160, b"\0") + keys


class PackedLodTests(unittest.TestCase):
    def fixture(self, root, packed_entries):
        (root / "parts").mkdir()
        (root / "parts/proxy.lod").write_text("geometry\n")
        (root / "parts/geometry.mdl").write_bytes(b"model")
        (root / "hakbuilder.json").write_text(json.dumps(
            {"OutputPath": "./output/", "HakList": [{"Name": "parts", "Path": "./parts/"}]}))
        if packed_entries is not None:
            (root / "output/hak").mkdir(parents=True)
            (root / "output/hak/parts.hak").write_bytes(erf(packed_entries))

    def test_archive_with_every_redirect_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, [("geometry", 2002), ("PROXY", packed.LOD_TYPE)])
            self.assertEqual([], packed.missing_lods(root))

    def test_dropped_or_unbuilt_redirects_fail(self):
        for entries in ([("geometry", 2002)], [("proxy", 2002)], None):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root, entries)
                self.assertTrue(packed.missing_lods(root))

    def test_long_resref_archives_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "parts.hak"
            archive.write_bytes(erf([("proxy", packed.LOD_TYPE)], b"V1.1"))
            self.assertEqual({"proxy"}, packed.packed_resrefs(archive, packed.LOD_TYPE))


if __name__ == "__main__":
    unittest.main()
