import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from PruneRobeAnimationBridges import referenced_bridges, plan, robes


class RobeBridgePruningTests(unittest.TestCase):
    def test_plan_requires_owned_outputs_and_a_complete_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            models = root / "sw_anim_m"
            models.mkdir()
            roots = root / "sw_pt_root"
            roots.mkdir()
            (root / "hakbuilder.json").write_text(json.dumps({"HakList": [{"Path": "sw_anim_m"}, {"Path": "sw_pt_root"}]}))
            orphan = models / "pmh_ra001.mdl"
            orphan.write_text("newmodel pmh_ra001\nsetsupermodel pmh_ra001 NULL\n")
            relative = "sw_anim_m/pmh_ra001.mdl"
            manifest = {"animation_bridges": {"old": "pmh_ra001"}, "files": {relative: robes.file_digest(orphan)}}
            with patch("PruneRobeAnimationBridges.subprocess.check_output", return_value=relative.encode() + b"\0"):
                self.assertEqual([orphan], plan(root, manifest))
                (roots / "pmh34.mdl").write_text("setsupermodel pmh34 pmh_ra001\n")
                self.assertEqual([], plan(root, manifest))
                orphan.write_text("changed")
                with self.assertRaisesRegex(ValueError, "changed since validation"):
                    plan(root, manifest)
            with patch("PruneRobeAnimationBridges.subprocess.check_output", return_value=b"sw_anim_m/missing.mdl\0"):
                with self.assertRaisesRegex(ValueError, "Materialize all"):
                    plan(root, manifest)

    def test_retired_roots_and_transitive_legacy_parents_are_preserved(self):
        parents = {"pmh34": {"current"}, "pmh90": {"legacy"},
                   "current": {"base"}, "legacy": {"older"}, "older": {"base"},
                   "orphan": {"base"}, "base": {""}}
        self.assertEqual({"current", "legacy", "older"}, referenced_bridges(
            parents, {"current", "legacy", "older", "orphan"}))

    def test_unused_families_do_not_keep_each_other_alive(self):
        parents = {"body": {"current"}, "current": {"base"},
                   "orphan": {"older"}, "older": {"orphan"}}
        self.assertEqual({"current"}, referenced_bridges(parents, {"current", "orphan", "older"}))

    def test_all_hak_layers_keep_their_references(self):
        parents = {"body": {"current", "legacy"}, "current": {"base"}, "legacy": {"base"}}
        self.assertEqual({"current", "legacy"}, referenced_bridges(parents, {"current", "legacy"}))


if __name__ == "__main__":
    unittest.main()
