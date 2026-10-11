"""Neck packaging must audit the original bytes behind checked-in LODs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import CheckNeckModels as necks
import ModelLodAssets as models


class CheckNeckModelsTests(unittest.TestCase):
    def fixture(self, root):
        for directory in ("sw_pt_neck", "sw_pt_root", "tools"):
            (root / directory).mkdir()
        (root / "hakbuilder.json").write_text(json.dumps({"HakList": [{"Path": "sw_pt_neck"}]}))
        first = b"newmodel pfg0_neck123\nnode dummy pfg0_neck123\n parent NULL\nendnode\ndonemodel pfg0_neck123\n"
        second = models.rename_identity(first, "pfg0_neck124")
        (root / "sw_pt_neck/pfg0_neck123.mdl").write_bytes(first)
        (root / "sw_pt_neck/pfg0_neck124.mdl").write_bytes(second)
        # Production keeps necks as MDLs; the guard must still audit any redirected input.
        with patch.object(models, "lod_supported", lambda path, root: True):
            models.apply(root)
        body = b"newmodel pfg5\nnode dummy pfg5\n parent NULL\nendnode\ndonemodel pfg5\n"
        (root / "sw_pt_root/pfg5.mdl").write_bytes(body)
        digest = hashlib.sha256(second).hexdigest()
        implementation = {}
        for relative in necks.IMPLEMENTATION:
            (root / relative).write_bytes(f"# {relative}\r\n".encode())
            implementation[relative] = necks.source_digest(root / relative)
        manifest = {"inputs": {"pfg5": hashlib.sha256(body).hexdigest(), "pfg0_neck124": digest},
                    "owned_inputs": {"pfg5": "sw_pt_root/pfg5.mdl"},
                    "aliases": {"pfg0_neck124": {"sha256": digest}}, "tables": {},
                    "implementation": implementation}
        (root / "tools/NeckModels.json").write_text(json.dumps(manifest))
        return manifest

    def test_lod_necks_are_audited_without_expanding_them(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            self.fixture(root)
            necks.check(root)
            self.assertFalse((root / "sw_pt_neck/pfg0_neck124.mdl").exists())

    def test_stale_lod_neck_fingerprints_fail_packaging(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            manifest = self.fixture(root)
            manifest["inputs"]["pfg0_neck124"] = "0" * 64
            manifest["aliases"]["pfg0_neck124"]["sha256"] = "0" * 64
            (root / "tools/NeckModels.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Stale neck assets.*pfg0_neck124"):
                necks.check(root)

    def test_deleted_repository_input_fails_packaging(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            self.fixture(root)
            (root / "sw_pt_root/pfg5.mdl").unlink()
            with self.assertRaisesRegex(ValueError, "Stale neck assets.*pfg5"):
                necks.check(root)

    def test_generator_changes_fail_packaging(self):
        for relative in necks.IMPLEMENTATION:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory, \
                    patch.object(models, "ROOT", Path(directory)):
                root = Path(directory)
                self.fixture(root)
                necks.check(root)
                (root / relative).write_text("# changed mapping\n")
                with self.assertRaisesRegex(ValueError, "Stale neck assets"):
                    necks.check(root)

    def test_manifests_without_implementation_fingerprints_fail_packaging(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, "ROOT", Path(directory)):
            root = Path(directory)
            manifest = self.fixture(root)
            del manifest["implementation"]
            (root / "tools/NeckModels.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Stale neck assets.*implementation"):
                necks.check(root)


if __name__ == "__main__":
    unittest.main()
