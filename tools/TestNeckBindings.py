"""Guard neck attachment binding, authored mesh data, and archive budgets."""
import copy
from pathlib import Path
import struct
import shutil
import tempfile
import unittest

import GenerateNeckBindings as necks


class NeckBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.native = cls.body(0)

    @classmethod
    def body(cls, phenotype):
        return necks.Model((cls.root / f"sw_pt_root/pfh{phenotype}.mdl").read_bytes(), False)

    def source(self, number):
        return (self.root / f"sw_pt_neck/pfh0_neck{number:03}.mdl").read_bytes()

    def test_reproduced_necks_follow_torso_and_head_on_robe_105(self):
        wearer = self.body(98)
        for number, original_index, expected_index, bone in ((104, 10, 15, "torso_g"), (108, 23, 28, "head_g")):
            with self.subTest(neck=number):
                source = self.source(number)
                old = necks.skin_nodes(source)[0]
                self.assertEqual({0: original_index}, old[3])
                self.assertNotEqual(bone, wearer.nodes[original_index][0])
                generated = necks.remap_neck(source, self.native, wearer, f"pfh98_neck{number:03}")
                actual = necks.skin_nodes(generated)[0]
                self.assertEqual({0: expected_index}, actual[3])
                self.assertEqual(bone, wearer.nodes[expected_index][0])
                necks.validate_remap(source, generated, self.native, wearer, f"pfh98_neck{number:03}")

    def test_all_shipped_female_neck_skins_preserve_geometry_and_every_bone_binding(self):
        skinned = 0
        for path in sorted((self.root / "sw_pt_neck").glob("pfh0_neck*.mdl")):
            source = path.read_bytes()
            if not necks.skin_nodes(source):
                continue
            skinned += 1
            for phenotype in (98, 110):
                with self.subTest(neck=path.name, phenotype=phenotype):
                    wearer = self.body(phenotype)
                    name = path.stem.replace("pfh0", f"pfh{phenotype}")
                    output = necks.remap_neck(source, self.native, wearer, name)
                    self.assertIsNotNone(output)
                    necks.validate_remap(source, output, self.native, wearer, name)
        self.assertGreaterEqual(skinned, 61)

    def test_rigid_necks_and_unchanged_skin_indices_need_no_variant(self):
        self.assertIsNone(necks.remap_neck(self.source(1), self.native, self.body(98), "pfh98_neck001"))
        self.assertIsNone(necks.remap_neck(self.source(104), self.native, self.native, "pfh0_neck104"))
        # This robe retains the torso index but inserts nodes ahead of head_g.
        self.assertIsNone(necks.remap_neck(self.source(104), self.native, self.body(158), "pfh158_neck104"))
        self.assertIsNotNone(necks.remap_neck(self.source(108), self.native, self.body(158), "pfh158_neck108"))

    def test_multibone_and_duplicate_named_meshes_keep_all_independent_inverse_binds(self):
        source, wearer = self.source(300), self.body(98)
        self.assertEqual(2, len(necks.skin_nodes(source)))
        output = necks.remap_neck(source, self.native, wearer, "pfh98_neck300")
        necks.validate_remap(source, output, self.native, wearer, "pfh98_neck300")
        self.assertGreater(len(necks.skin_nodes(output)[0][3]), 4)

    def test_validation_rejects_geometry_and_inverse_bind_corruption(self):
        source, wearer = self.source(108), self.body(98)
        output = necks.remap_neck(source, self.native, wearer, "pfh98_neck108")
        corrupt = bytearray(output)
        corrupt[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "Raw vertex"):
            necks.validate_remap(source, corrupt, self.native, wearer, "pfh98_neck108")
        corrupt = bytearray(output)
        extra = necks.skin_nodes(output)[0][0]
        pointer = struct.unpack_from("<I", output, extra + 28)[0]
        corrupt[12 + pointer + 28 * 16] ^= 1
        with self.assertRaisesRegex(ValueError, "inverse bind"):
            necks.validate_remap(source, corrupt, self.native, wearer, "pfh98_neck108")

    def test_changed_or_missing_native_bone_fails_closed(self):
        wearer = copy.deepcopy(self.body(98))
        head = next(i for i, node in enumerate(wearer.nodes) if node[0] == "head_g")
        node = wearer.nodes[head]
        wearer.nodes[head] = ("missing_head", *node[1:])
        with self.assertRaisesRegex(ValueError, "missing native bone"):
            necks.remap_neck(self.source(108), self.native, wearer, "pfh98_neck108")
        wearer.nodes[head] = node
        wearer.nodes[head][3][8] = ((0.0,), [(20, 0, 0)])
        with self.assertRaisesRegex(ValueError, "changed native bind"):
            necks.remap_neck(self.source(108), self.native, wearer, "pfh98_neck108")

    def test_generation_is_deterministic_and_bank_budget_includes_erf_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            first = necks.generate(self.root, Path(folder), [104, 108], [98, 99])
            second = necks.generate(self.root, Path(folder), [104, 108], [98, 99])
            self.assertEqual(first, second)
            self.assertEqual(first, necks.check(self.root, Path(folder)))
            for bank, size in first["archive_bytes"].items():
                expected = 160 + sum(row["bytes"] + 32 for name, row in first["files"].items() if name.startswith(bank + "/"))
                self.assertEqual(expected, size)
                self.assertLess(size, 2**31)

    def test_manifest_audit_detects_missing_resource_changed_root_and_new_skinned_neck(self):
        with tempfile.TemporaryDirectory() as folder:
            source_root, output_root = Path(folder) / "source", Path(folder) / "output"
            for relative in ("sw_pt_root/pfh0.mdl", "sw_pt_root/pfh98.mdl", "sw_pt_neck/pfh0_neck104.mdl"):
                target = source_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / relative, target)
            report = necks.generate(source_root, output_root)
            self.assertEqual(report, necks.check(source_root, output_root))
            output = output_root / next(iter(report["files"]))
            saved_output = output.read_bytes()
            output.unlink()
            with self.assertRaisesRegex(ValueError, "missing or unrecorded"):
                necks.check(source_root, output_root)
            output.write_bytes(saved_output)
            root_path = source_root / "sw_pt_root/pfh98.mdl"
            saved_root = root_path.read_bytes()
            changed = bytearray(saved_root)
            changed[20] ^= 1  # Valid structure, different resource header name.
            root_path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, "skeleton contents changed"):
                necks.check(source_root, output_root)
            root_path.write_bytes(saved_root)
            shutil.copyfile(self.root / "sw_pt_neck/pfh0_neck108.mdl", source_root / "sw_pt_neck/pfh0_neck108.mdl")
            with self.assertRaisesRegex(ValueError, "neck inventory"):
                necks.check(source_root, output_root)


if __name__ == "__main__":
    unittest.main()
