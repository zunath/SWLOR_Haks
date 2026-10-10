"""Sharing must preserve geometry, materials and skin bindings exactly."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import ModelLodAssets as models


def binary(name):
    data = bytearray(640)
    struct.pack_into("<I", data, 84, 244)
    data[20:84] = name.encode().ljust(64, b"\0")
    data[288:320] = name.encode().ljust(32, b"\0")
    data[500:516] = b"bone-bind-data!!"
    return bytes(data)


def animated(name):
    data = bytearray(binary(name) + bytes(360))
    struct.pack_into("<II", data, 132, 600, 1)
    struct.pack_into("<I", data, 612, 650)
    struct.pack_into("<I", data, 734, 800)
    data[746:810] = name.encode().ljust(64, b"\0")
    data[844:876] = b"animation_joint".ljust(32, b"\0")
    return bytes(data)


class ModelLodTests(unittest.TestCase):
    def test_root_only_clones_keep_their_original_animation_root(self):
        first = animated("first")
        second = models.rename_identity(first, "second", "root")
        self.assertEqual(models.identity(first, "root"), models.identity(second, "root"))
        self.assertNotEqual(models.identity(first), models.identity(second))
        self.assertEqual(first, models.rename_identity(second, "first", "root"))

    def test_merged_canonical_groups_flatten_redirects_and_retain_exact_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            self.fixture(root)
            first = animated("first")
            second = models.rename_identity(first, "second", "root")
            third = models.rename_identity(first, "a")
            for name, data in (("first", first), ("second", second), ("A", third)):
                (root / "parts" / (name + ".mdl")).write_bytes(data)
            models.apply(root)
            self.assertEqual(b"a\n", (root / "parts/second.lod").read_bytes())
            self.assertEqual(second, models.ModelPath(root / "parts/second.mdl").read_bytes())
            self.assertEqual([], models.check(root))
            self.assertEqual(2, len(models.catalog(root)["parts/second.mdl"]["identity_steps"]))

    def test_binary_identity_does_not_erase_geometry_materials_or_skin_binds(self):
        original = binary("first")
        renamed = models.rename_identity(original, "second")
        self.assertEqual(models.identity(original), models.identity(renamed))
        self.assertEqual(original, models.rename_identity(renamed, "first"))
        for offset in (180, 400, 500, 639):
            changed = bytearray(renamed)
            changed[offset] ^= 1
            self.assertNotEqual(models.identity(original), models.identity(bytes(changed)))

    def test_ascii_texture_names_are_not_rewritten_with_model_identity(self):
        original = b"newmodel first\nsetsupermodel first body\nnode dummy first\n parent NULL\nendnode\ndonemodel first\nbitmap first\n"
        renamed = models.rename_identity(original, "second")
        self.assertIn(b"bitmap first", renamed)
        self.assertIn(b"setsupermodel second body", renamed)
        self.assertEqual(original, models.rename_identity(renamed, "first"))
        self.assertNotEqual(models.identity(original), models.identity(renamed.replace(b"bitmap first", b"bitmap second")))

    def test_nonzero_name_padding_is_not_ignored(self):
        original = bytearray(binary("first"))
        original[83] = 9
        renamed = models.rename_identity(bytes(original), "second")
        self.assertEqual(bytes(original[20:84]), renamed[20:84])

    def test_invalid_compiled_pointer_is_rejected(self):
        original = bytearray(binary("first"))
        struct.pack_into("<I", original, 84, 999999)
        with self.assertRaises(ValueError):
            models.identity(bytes(original))

    def fixture(self, root):
        (root / "parts").mkdir()
        (root / "tools").mkdir()
        (root / "hakbuilder.json").write_text(json.dumps({"HakList": [{"Path": "parts"}]}))
        first = binary("first")
        second = models.rename_identity(first, "second")
        (root / "parts/first.mdl").write_bytes(first)
        (root / "parts/second.mdl").write_bytes(second)
        return first, second

    def test_checked_in_redirect_reconstructs_exact_original_without_creating_an_mdl(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            first, second = self.fixture(root)
            models.apply(root)
            self.assertFalse((root / "parts/second.mdl").exists())
            self.assertEqual(b"first\n", (root / "parts/second.lod").read_bytes())
            logical = models.ModelPath(root / "parts/second.mdl")
            self.assertTrue(logical.is_file())
            self.assertEqual(second, logical.read_bytes())
            self.assertEqual(second, logical.open("rb").read())
            self.assertFalse((root / "parts/second.mdl").exists())
            self.assertEqual([], models.check(root))
            models.apply(root)
            self.assertEqual([], models.check(root))
            models.expand(root)
            self.assertEqual(first, (root / "parts/first.mdl").read_bytes())
            self.assertEqual(second, (root / "parts/second.mdl").read_bytes())
            self.assertFalse((root / "parts/second.lod").exists())

    def test_missing_or_modified_target_and_shadowed_original_fail_closed(self):
        for problem in ("target", "geometry", "missing", "shadow"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
                root = Path(directory)
                first, second = self.fixture(root)
                models.apply(root)
                if problem == "target":
                    (root / "parts/second.lod").write_bytes(b"other\n")
                elif problem == "geometry":
                    (root / "parts/first.mdl").write_bytes(first + b"changed")
                elif problem == "missing":
                    (root / "parts/first.mdl").unlink()
                else:
                    (root / "parts/second.mdl").write_bytes(second)
                self.assertTrue(models.check(root))

    def test_different_skin_bindings_keep_separate_geometry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            _, second = self.fixture(root)
            changed = bytearray(second)
            changed[500] ^= 1
            (root / "parts/second.mdl").write_bytes(changed)
            models.apply(root)
            self.assertEqual({}, models.catalog(root))
            self.assertTrue((root / "parts/first.mdl").is_file())
            self.assertTrue((root / "parts/second.mdl").is_file())

    def test_path_traversal_cannot_load_an_external_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                models.safe_path(Path(directory), "../outside.mdl")


if __name__ == "__main__":
    unittest.main()
