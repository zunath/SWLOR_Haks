#!/usr/bin/env python3
"""Check that the tint model compilation audit respects active resource scope.

Run: python -B tools/TestTintModelCompilation.py
"""

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import GenerateTintMapAssets as generator
from GenerateTintMapAssets import find_invalid_binary_tint_models, find_uncompiled_tint_models


class TintModelCompilationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def model(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def binary_dummy(self):
        # A valid model section with one dummy root and no raw mesh data.
        data = bytearray(356)
        struct.pack_into("<III", data, 0, 0, len(data) - 12, 0)
        struct.pack_into("<I", data, 84, 232)
        struct.pack_into("<I", data, 244 + 108, 1)
        return data

    def test_ascii_tint_model_is_rejected_once_for_all_its_materials(self):
        model = self.model("head.mdl", b"newmodel head\n")
        rows = [("head", "face", [1]), ("head", "hair", [2])]
        self.assertEqual(find_uncompiled_tint_models({"head": model}, rows), [model])

    def test_unrelated_ascii_is_outside_the_compilation_requirement(self):
        unrelated = self.model("unrelated.mdl", b"newmodel unrelated\n")
        self.assertEqual(find_uncompiled_tint_models({"unrelated": unrelated}, []), [])

    def test_only_the_active_resource_for_a_tint_model_is_checked(self):
        # Full binary structure validation belongs to the material-plan parser;
        # this guard checks the format marker of the winning resource only.
        lower = self.root / "lower"
        higher = self.root / "higher"
        lower.mkdir()
        higher.mkdir()
        self.model("lower/head.mdl", b"newmodel head\n")
        active = self.model("higher/head.mdl", b"\0\0\0\0")
        config = self.model("hakbuilder.json", b"{}")
        with (
            patch.object(generator, "HAK_CONFIG", config),
            patch.object(generator, "_ACTIVE_MODELS", None),
            patch.object(generator, "hak_directories", return_value=(lower, higher)),
        ):
            models = generator.find_active_models()
            self.assertEqual(models["head"], active)
            self.assertEqual(find_uncompiled_tint_models(models, [("head", "face", [1])]), [])

    def test_missing_models_remain_the_catalog_audits_responsibility(self):
        self.assertEqual(find_uncompiled_tint_models({}, [("missing", "face", [1])]), [])

    def test_valid_binary_section_and_root_are_accepted(self):
        model = self.model("head.mdl", self.binary_dummy())
        self.assertEqual(find_invalid_binary_tint_models({"head": model}, [("head", "face", [1])]), {})

    def test_variable_length_binary_name_replacement_is_rejected(self):
        data = self.binary_dummy()
        # Historical one-byte name expansions shifted pointers while retaining
        # both the binary marker and the old declared section sizes.
        data[20:20] = b"x"
        model = self.model("head.mdl", data)
        errors = find_invalid_binary_tint_models({"head": model}, [("head", "face", [1])])
        self.assertIn("section lengths", errors[model])

    def test_invalid_node_pointer_is_rejected_with_matching_section_lengths(self):
        data = self.binary_dummy()
        struct.pack_into("<I", data, 84, 0xE800)
        model = self.model("head.mdl", data)
        errors = find_invalid_binary_tint_models({"head": model}, [("head", "face", [1])])
        self.assertIn("invalid node pointer", errors[model])


if __name__ == "__main__":
    unittest.main()
