"""Sharing must preserve geometry, materials and skin bindings exactly."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import ModelLodAssets as models

ORIGINAL_LOD_SUPPORTED = models.lod_supported


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
    def setUp(self):
        # Fixtures publish redirects freely; production allows only generated robe cloaks.
        supported = patch.object(models, "lod_supported", lambda path, root: True)
        supported.start()
        self.addCleanup(supported.stop)

    def test_only_generated_robe_cloaks_may_redirect(self):
        with patch.object(models, "lod_supported", ORIGINAL_LOD_SUPPORTED), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "tools/RobeCloakModels.json").write_text(json.dumps({"models": {"pfh171_cloak_004": "pfh0_cloak_004"}}))
            self.assertTrue(models.lod_supported(root / "sw_pt_cloak/pfh171_cloak_004.mdl", root))
            self.assertFalse(models.lod_supported(root / "sw_pt_cloak/pfb3_cloak_002.mdl", root))
            self.assertFalse(models.lod_supported(root / "sw_pt_head/pfe0_head1001.mdl", root))
            self.assertFalse(models.lod_supported(root / "sw_t_cepmine/zdm02_a08_11.mdl", root))

    def test_unsupported_model_types_keep_native_mdls_and_existing_redirects_are_restored(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            first, second = self.fixture(root)
            models.apply(root)
            self.assertEqual({"parts/second.mdl"}, set(models.catalog(root)))
            with patch.object(models, "lod_supported", lambda path, root: False):
                self.assertTrue(any("keep its MDL" in error for error in models.check(root)))
                models.apply(root)
                self.assertEqual({}, models.catalog(root))
                self.assertEqual(second, (root / "parts/second.mdl").read_bytes())
                self.assertFalse((root / "parts/second.lod").exists())
                self.assertEqual([], models.check(root))

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

    def test_empty_robe_attachments_keep_native_mdls_while_other_empty_models_share(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            self.fixture(root)
            empty = bytearray(binary("first"))
            struct.pack_into("<I", empty, 364, 1)  # A single dummy root, no meshes or animations.
            for name in ("first", "second", "pmh212_robe003", "pfh213_robe004"):
                (root / "parts" / (name + ".mdl")).write_bytes(models.rename_identity(bytes(empty), name))
            models.apply(root)
            self.assertEqual({"parts/second.mdl"}, set(models.catalog(root)))
            for name in ("pmh212_robe003", "pfh213_robe004"):
                self.assertTrue((root / "parts" / (name + ".mdl")).is_file())
                self.assertFalse((root / "parts" / (name + ".lod")).exists())
            self.assertEqual([], models.check(root))

    def test_existing_empty_robe_redirect_is_rejected_and_restored_byte_exactly(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            self.fixture(root)
            empty = bytearray(binary("first"))
            struct.pack_into("<I", empty, 364, 1)
            original = models.rename_identity(bytes(empty), "pmh212_robe003")
            (root / "parts/first.mdl").write_bytes(empty)
            (root / "parts/pmh212_robe003.mdl").write_bytes(original)
            with patch.object(models, "requires_native_attachment", return_value=False):
                models.apply(root)
            self.assertTrue(any("requires a native MDL" in error for error in models.check(root)))
            models.apply(root)
            self.assertEqual(original, (root / "parts/pmh212_robe003.mdl").read_bytes())
            self.assertFalse((root / "parts/pmh212_robe003.lod").exists())
            self.assertEqual([], models.check(root))

    def test_base_game_model_overrides_stay_native_and_existing_redirects_are_restored(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            first, second = self.fixture(root)
            models.apply(root)
            self.assertEqual({"parts/second.mdl"}, set(models.catalog(root)))
            models.apply(root, stock_models={"SECOND"})
            self.assertEqual({}, models.catalog(root))
            self.assertEqual(second, (root / "parts/second.mdl").read_bytes())
            self.assertFalse((root / "parts/second.lod").exists())
            self.assertEqual([], models.check(root))
            manifest = json.loads((root / models.MANIFEST).read_text())
            self.assertEqual(["second"], manifest["native_overrides"])
            # A redirect reintroduced for a recorded base-game name fails the build check.
            models.apply(root)
            manifest = json.loads((root / models.MANIFEST).read_text())
            manifest["native_overrides"] = ["second"]
            (root / models.MANIFEST).write_text(json.dumps(manifest))
            self.assertTrue(any("base-game model" in error for error in models.check(root)))

    def test_visible_or_animated_robe_geometry_can_still_be_shared(self):
        for flags, children, animations in ((0x21, 0, 0), (1, 1, 0), (1, 0, 1)):
            data = bytearray(binary("pmh0_robe003"))
            struct.pack_into("<I", data, 364, flags)
            struct.pack_into("<I", data, 332, children)
            struct.pack_into("<I", data, 136, animations)
            self.assertFalse(models.requires_native_attachment(Path("pmh0_robe003.mdl"), data))

    def test_path_traversal_cannot_load_an_external_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                models.safe_path(Path(directory), "../outside.mdl")


if __name__ == "__main__":
    unittest.main()
