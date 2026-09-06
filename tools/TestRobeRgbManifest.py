"""Allocation and manifest guards must fail before touching model resources."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import GenerateRobeRgbModels as g
import GenerateTintMapAssets as tint
import RobeSkeleton as rig
import TestRobeSkeleton as skeleton_tests


class RobeRgbManifestTests(unittest.TestCase):
    def test_text_manifest_hashes_are_checkout_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in (".py", ".2da", ".json"):
                path = Path(directory) / ("source" + extension)
                path.write_bytes(b"first\nsecond\n")
                expected = g.file_digest(path)
                path.write_bytes(b"first\r\nsecond\r\n")
                self.assertEqual(expected, g.file_digest(path))
                path.write_bytes(b"first\r\nchanged\r\n")
                self.assertNotEqual(expected, g.file_digest(path))

    def test_unused_table_rows_with_existing_root_or_attachment_are_reserved(self):
        active = {"pfa34": None, "pmh35_robe007": None, "pfg036_robe187": None}
        result = g.allocate_phenotypes([3], {}, ["2DA V2.0", "", "Label"], active)
        self.assertEqual({3: 37}, result)

    def test_retired_assignments_are_never_recycled(self):
        table = ["2DA V2.0", "", "Label", "34 RobeRgb_003 **** 0"]
        self.assertEqual({3: 34, 7: 35}, g.allocate_phenotypes([7], {}, table, {}))

    def test_conflicting_persisted_mapping_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            g.allocate_phenotypes([3], {"pmh0_robe003": 35},
                ["2DA V2.0", "", "Label", "34 RobeRgb_003 **** 0"], {})

    def test_only_byte_verified_prior_outputs_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "ROOT", Path(directory)):
            root = Path(directory) / "sw_pt_root"
            root.mkdir()
            path = root / "pmh34.mdl"
            path.write_bytes(b"owned model")
            selected = {"pmh0_robe003": None}
            prior = {"pmh0_robe003": 34}
            active = {"pmh34": path}
            manifest = {"files": {"sw_pt_root/pmh34.mdl": g.file_digest(path)}}
            g.validate_output_ownership(selected, {3: 34}, prior, active, manifest)
            for wrong_prior, wrong_manifest in (({}, manifest), (prior, {"files": {}})):
                with self.assertRaisesRegex(ValueError, "not a verified prior"):
                    g.validate_output_ownership(selected, {3: 34}, wrong_prior, active, wrong_manifest)
            path.write_bytes(b"unrelated model")
            with self.assertRaisesRegex(ValueError, "not a verified prior"):
                g.validate_output_ownership(selected, {3: 34}, prior, active, manifest)

    def test_compile_helpers_are_required_manifest_inputs(self):
        self.assertIn("CompileModels.py", g.GENERATOR_INPUTS)
        manifest = json.loads(g.MANIFEST.read_text())
        for name in g.GENERATOR_INPUTS:
            self.assertEqual(g.file_digest(g.ROOT / "tools" / name), manifest["files"]["tools/" + name])
        for name in g.CATALOG_INPUTS:
            self.assertEqual(g.file_digest(g.ROOT / name), manifest["files"][name])

    def test_new_animation_families_skip_occupied_resources(self):
        groups = {"new-family": {"base": "pfa0"}}
        self.assertEqual({"new-family": "pfa_ra003"}, g.allocate_animation_bridges(
            groups, {"pfa_ra001": None, "pfa_ra002": None}, {}))

    def test_bridge_collision_added_during_generation_rejects_the_old_plan(self):
        groups = {"new-family": {"base": "pfa0"}}
        planned = g.allocate_animation_bridges(groups, {}, {})
        self.assertEqual(planned, g.allocate_animation_bridges(groups, {}, {}, planned))
        with self.assertRaisesRegex(ValueError, "allocation changed"):
            g.allocate_animation_bridges(groups, {"pfa_ra001": None}, {}, planned)

    def test_animation_bridge_reuse_and_retirement_require_verified_ownership(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "ROOT", Path(directory)):
            root = Path(directory) / "sw_pt_root"
            root.mkdir()
            path = root / "pfa_ra001.mdl"
            path.write_bytes(b"generated animation")
            groups = {"family": {"base": "pfa0"}}
            prior = {"animation_bridges": {"family": "pfa_ra001"},
                     "files": {"sw_pt_root/pfa_ra001.mdl": g.file_digest(path)}}
            self.assertEqual(prior["animation_bridges"], g.allocate_animation_bridges(
                groups, {"pfa_ra001": path}, prior))
            other = Path(directory) / "authored.mdl"
            other.write_bytes(path.read_bytes())
            with self.assertRaisesRegex(ValueError, "not a verified prior"):
                g.allocate_animation_bridges(groups, {"pfa_ra001": other}, prior)
            path.write_bytes(b"authored replacement")
            for current_groups in (groups, {}):
                with self.assertRaisesRegex(ValueError, "not a verified prior"):
                    g.allocate_animation_bridges(current_groups, {"pfa_ra001": path}, prior)

    def test_stock_inventory_must_cover_authoritative_selectable_models(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.json"
            complete = {"models": [{"model": "pfa0_robe003"}],
                        "stockModelSha256": {"pfa0_robe003": "hash"}}
            path.write_text(json.dumps(complete))
            with patch.object(g.stock_robes, "MANIFEST", path), \
                 patch.object(g.stock_robes, "selectable_styles", return_value={3, 7}):
                g.validate_stock_inventory({"pfa0_robe003": None, "pfa0_head003": None})
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    g.validate_stock_inventory({"pfa0_robe003": None, "pmh0_robe007": None})
                complete["models"] = []
                path.write_text(json.dumps(complete))
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    g.validate_stock_inventory({"pfa0_robe003": None})

    def test_retired_roots_keep_their_bridge_and_attachment_across_generations(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "ROOT", Path(directory)):
            root = Path(directory)
            files = {
                "sw_pt_root/pmh34.mdl": b"setsupermodel pmh34 pmh_ra001\n",
                "sw_pt_root/pmh_ra001.mdl": b"setsupermodel pmh_ra001 NULL\n",
                "sw_pt_robe/pmh34_robe003.mdl": g.empty_attachment("pmh34_robe003"),
            }
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(data)
            active = {(root / name).stem: root / name for name in files}
            manifest = {"animation_bridges": {"retired-family": "pmh_ra001"},
                        "files": {name: g.file_digest(root / name) for name in files}}
            for _ in range(2):
                g.allocate_animation_bridges({}, active, manifest)
                retained = g.retained_model_files(manifest, {}, {34}, active)
                self.assertEqual(set(files), set(retained))
                parent = g.mdl.supermodel(active["pmh34"].read_bytes())
                self.assertTrue(active[parent].is_file())
                manifest = {**manifest, "files": retained}
            active["pmh_ra001"].unlink()
            with self.assertRaisesRegex(ValueError, "not a verified prior"):
                g.retained_model_files(manifest, {}, {34}, active)

    def test_input_snapshot_rejects_dependency_and_generator_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mdl"
            helper = Path(directory) / "helper.py"
            source.write_bytes(b"original model")
            helper.write_bytes(b"original helper\n")
            dependencies = {"source": source.read_bytes()}
            active = {"source": source}
            snapshot = {helper: g.file_digest(helper)}
            g.validate_input_snapshot(dependencies, active, snapshot)
            source.write_bytes(b"changed model")
            with self.assertRaisesRegex(ValueError, "Source changed"):
                g.validate_input_snapshot(dependencies, active, snapshot)
            source.write_bytes(dependencies["source"])
            helper.write_bytes(b"changed helper\n")
            with self.assertRaisesRegex(ValueError, "Input changed"):
                g.validate_input_snapshot(dependencies, active, snapshot)

    def test_manifest_uses_captured_inputs_when_live_files_change_during_copy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "ROOT", Path(directory)), \
                patch.object(g, "GENERATOR_INPUTS", ("helper.py",)), patch.object(g, "CATALOG_INPUTS", ()):
            source = Path(directory) / "source.mdl"
            helper = Path(directory) / "tools" / "helper.py"
            helper.parent.mkdir()
            source.write_bytes(b"validated model")
            helper.write_bytes(b"validated helper\n")
            dependencies = {"source": source.read_bytes()}
            active = {"source": source}
            snapshot = {helper: g.file_digest(helper)}
            g.validate_input_snapshot(dependencies, active, snapshot)
            source_hash = g.file_digest(source)
            source.write_bytes(b"edit made while outputs were copied")
            helper.write_bytes(b"later helper edit\n")

            files = g.snapshot_manifest_inputs(dependencies, active, snapshot)

            self.assertEqual(source_hash, files["source.mdl"])
            self.assertEqual(snapshot[helper], files["tools/helper.py"])
            self.assertNotEqual(g.file_digest(source), files["source.mdl"])
            self.assertNotEqual(g.file_digest(helper), files["tools/helper.py"])

    def test_same_resref_override_invalidates_tracked_model_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "ROOT", Path(directory)):
            root = Path(directory)
            files = {"low/pmh0_robe003.mdl": "hash", "low/pmh0.mdl": "hash", "low/animation.mdl": "hash"}
            active = {Path(name).stem: root / name for name in files}
            self.assertEqual([], g.model_path_errors(files, {"stock_parent": "hash"}, active))
            for relative in files:
                name = Path(relative).stem
                shadowed = {**active, name: root / "high" / (name + ".mdl")}
                self.assertEqual(1, len(g.model_path_errors(files, {}, shadowed)))
            self.assertEqual(1, len(g.model_path_errors(files, {"stock_parent": "hash"},
                {**active, "stock_parent": root / "high/stock_parent.mdl"})))

    def test_retiring_member_versions_the_bridge_without_removing_its_tracks(self):
        fixtures = {key.replace("body", "pmh0"): value.replace("body", "pmh0")
                    for key, value in skeleton_tests.IndependentRobeSkeletonTests().fixtures().items()}
        fixtures["other"] = fixtures["garment"].replace("garment", "other").replace(
            "parent rootdummy\nposition 2", "parent other\nposition 2")
        both = rig.Families(fixtures.get)
        family = both.add("pmh0", fixtures["garment"])
        self.assertEqual(family, both.add("pmh0", fixtures["other"]))
        remaining = rig.Families(fixtures.get)
        self.assertEqual(family, remaining.add("pmh0", fixtures["garment"]))
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "ROOT", Path(directory)):
            old_path = Path(directory) / "sw_pt_root/pmh_ra001.mdl"
            old_path.parent.mkdir()
            old_source = both.bridge(family, "pmh_ra001")
            old_path.write_bytes(old_source)
            prior = {"animation_bridges": {family: "pmh_ra001"},
                     "files": {"sw_pt_root/pmh_ra001.mdl": g.file_digest(old_path)}}
            versions, migrated = g.version_animation_families(both, prior, lambda *_: True)
            self.assertNotIn(family, migrated["animation_bridges"])
            self.assertEqual("pmh_ra001", migrated["animation_bridges"][versions[family]])
            same_versions, _ = g.version_animation_families(both, migrated,
                lambda *_: self.fail("An unchanged version must reuse its recorded identity"))
            self.assertEqual(versions, same_versions)
            next_versions, retained = g.version_animation_families(remaining, migrated,
                lambda *_: self.fail("Versioned families never use the legacy migration"))
            self.assertNotEqual(versions[family], next_versions[family])
            names = g.allocate_animation_bridges({next_versions[family]: remaining.groups[family]},
                {"pmh_ra001": old_path}, retained)
            self.assertEqual("pmh_ra002", names[next_versions[family]])
            self.assertEqual(old_source, old_path.read_bytes())
            self.assertIn("pmh_ra001", retained["animation_bridges"].values())
            _, incompatible = g.version_animation_families(remaining, prior, lambda *_: False)
            self.assertIn(family, incompatible["animation_bridges"])
            old_parent, _ = g.version_animation_families(both, {}, lambda *_: False, {"pmh0": b"native part IDs v1"})
            new_parent, _ = g.version_animation_families(both, {}, lambda *_: False, {"pmh0": b"native part IDs v2"})
            self.assertNotEqual(old_parent[family], new_parent[family])

    def test_atlas_audit_rejects_nonmetal_texels_and_header_corruption(self):
        original = tint.PALETTE_TEXTURE.read_bytes()
        self.assertEqual([], tint.palette_atlas_errors(tint.PALETTE_TEXTURE))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "palette.tga"
            for offset in (17, 18, 18 + 704 * 256 * 4, 18 + 1056 * 256 * 4, len(original) - 1):
                corrupted = bytearray(original)
                corrupted[offset] ^= 1
                path.write_bytes(corrupted)
                self.assertTrue(tint.palette_atlas_errors(path), f"undetected corruption at {offset}")


if __name__ == "__main__":
    unittest.main()
