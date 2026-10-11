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
        models.apply(root)
        digest = hashlib.sha256(second).hexdigest()
        manifest = {"inputs": {"pfg0_neck124": digest},
                    "aliases": {"pfg0_neck124": {"sha256": digest}}, "tables": {}}
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


if __name__ == "__main__":
    unittest.main()
