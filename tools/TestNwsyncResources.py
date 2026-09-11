import json
from pathlib import Path
import tempfile
import unittest

from CheckNwsyncResources import LIMIT_BYTES, oversized_resources


class NwsyncResourceTests(unittest.TestCase):
    def test_supported_builder_config_syntax_still_checks_resource_sizes(self):
        for bom, trailing_commas in ((True, False), (False, True), (True, True)):
            with self.subTest(bom=bom, trailing_commas=trailing_commas), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                directory = root / "sw_anim_m"
                directory.mkdir()
                config = ('{"HakList":[{"Path":"sw_anim_m",},],}' if trailing_commas else
                          '{"HakList":[{"Path":"sw_anim_m"}]}')
                (root / "hakbuilder.json").write_text(config, encoding="utf-8-sig" if bom else "utf-8")
                with (directory / "boundary.mdl").open("wb") as output:
                    output.truncate(LIMIT_BYTES)
                self.assertEqual([("sw_anim_m/boundary.mdl", LIMIT_BYTES)], oversized_resources(root))

    def test_checks_individual_files_in_every_configured_hak(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directories = [root / "sw_anim_m", root / "other"]
            for directory in directories:
                directory.mkdir()
            (root / "hakbuilder.json").write_text(json.dumps({"HakList": [
                {"Path": directory.name} for directory in directories]}))
            for path, size in [(directories[0] / "safe.mdl", LIMIT_BYTES - 1),
                               (directories[0] / "large.mdl", LIMIT_BYTES + 1),
                               (directories[1] / "boundary.wav", LIMIT_BYTES)]:
                with path.open("wb") as output:
                    output.truncate(size)
            self.assertEqual([("other/boundary.wav", LIMIT_BYTES),
                              ("sw_anim_m/large.mdl", LIMIT_BYTES + 1)], oversized_resources(root))

    def test_missing_hak_directory_cannot_pass_a_sparse_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "hakbuilder.json").write_text('{"HakList":[{"Path":"missing"}]}')
            with self.assertRaisesRegex(ValueError, "Missing or redirected"):
                oversized_resources(root)


if __name__ == "__main__":
    unittest.main()
