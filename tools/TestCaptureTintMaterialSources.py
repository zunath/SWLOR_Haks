"""Authored material capture works for sibling, rather than nested, checkouts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import CaptureTintMaterialSources as capture


class CaptureTintMaterialSourcesTests(unittest.TestCase):
    def test_explicit_module_repository_supplies_both_commit_and_hak_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            haks, module, game = root / "haks", root / "module", root / "game"
            for path in (haks, module, game):
                path.mkdir()
            (haks / "tools").mkdir()
            (haks / "tools/TintMapSources.json").write_text("[]")
            (haks / "module_only_hak").mkdir()
            (haks / "module_only_hak/authored.mtr").write_text("texture0 fixed_diffuse\n")
            (module / "Module/ifo").mkdir(parents=True)
            (module / "Module/ifo/module.ifo.json").write_text(json.dumps({
                "Mod_HakList": {"value": [{"Mod_Hak": {"value": "module_only_hak"}}]}
            }))
            def git(path, *args):
                return subprocess.check_output(["git", "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "-C", str(path), *args], stderr=subprocess.DEVNULL)
            for path in (haks, module):
                git(path, "init")
                git(path, "add", ".")
                git(path, "commit", "-m", "Fixture")
            expected_module_commit = git(module, "rev-parse", "HEAD").decode().strip()
            with patch.object(capture.tint, "REPOSITORY_ROOT", haks), \
                 patch.object(capture.tint, "find_active_models", return_value={}), \
                 patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}):
                with self.assertRaises(subprocess.CalledProcessError):
                    git(haks, "rev-parse", "HEAD")
                result = capture.capture("HEAD", "HEAD", "HEAD", game, module)
            self.assertEqual(expected_module_commit, result["moduleCommit"])
            self.assertEqual(["module_only_hak"], result["hakPriorityFirstWins"])
            self.assertEqual(["texture0 fixed_diffuse"], result["materials"]["authored"]["lines"])


if __name__ == "__main__":
    unittest.main()
