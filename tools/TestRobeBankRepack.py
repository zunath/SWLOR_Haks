"""Packaging may reuse audited bytes only with intact cache and source provenance."""
import copy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import RepackRobeAnimationBanks as repack


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RobeRepackCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from TestRobeAnimationBanks import NativeRobeAnimationBankTests
        NativeRobeAnimationBankTests.setUpClass()
        cls.addClassCleanup(NativeRobeAnimationBankTests.doClassCleanups)
        cls.original = NativeRobeAnimationBankTests.original
        cls.parts = NativeRobeAnimationBankTests.parts
        cls.head = "pmh_ra001"
        cls.source_sha = digest(cls.original)
        cls.validation_sha = digest(b"validation tools and native compiler")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name)
        (self.stage / "binary").mkdir()
        for name, data in self.parts.items():
            (self.stage / "binary" / f"{name}.mdl").write_bytes(data)
        self.receipt = {
            "head": self.head,
            "original_bytes": len(self.original),
            "clips": 5,
            "source_sha256": self.source_sha,
            "validation_sha256": self.validation_sha,
            "banks": {name: len(data) for name, data in self.parts.items()},
            "bank_sha256": {name: digest(data) for name, data in self.parts.items()},
        }

    def matches(self, receipt=None):
        return repack.cached_audit_matches(
            self.receipt if receipt is None else receipt,
            self.head, self.source_sha, self.validation_sha, self.stage)

    def test_valid_native_bank_chain_can_reuse_its_receipt(self):
        self.assertTrue(self.matches())

    def test_head_source_and_validator_must_match_the_requested_family(self):
        for key, value in (("head", "pmh_ra002"), ("source_sha256", digest(b"other source")),
                           ("validation_sha256", digest(b"other validation"))):
            with self.subTest(key=key):
                receipt = {**self.receipt, key: value}
                self.assertFalse(self.matches(receipt))

    def test_receipt_names_and_hashes_must_cover_the_same_ordered_complete_chain(self):
        variants = []
        for key in ("banks", "bank_sha256"):
            receipt = copy.deepcopy(self.receipt)
            receipt[key] = dict(reversed(list(receipt[key].items())))
            variants.append(receipt)
            receipt = copy.deepcopy(self.receipt)
            receipt[key].pop(next(reversed(receipt[key])))
            variants.append(receipt)
        receipt = copy.deepcopy(self.receipt)
        receipt["banks"] = dict(reversed(list(receipt["banks"].items())))
        receipt["bank_sha256"] = dict(reversed(list(receipt["bank_sha256"].items())))
        variants.append(receipt)
        for receipt in variants:
            with self.subTest(banks=list(receipt["banks"]), hashes=list(receipt["bank_sha256"])):
                self.assertFalse(self.matches(receipt))

    def test_missing_or_modified_staged_file_cannot_reuse_the_receipt(self):
        child = "pmh_ra001_b01"
        path = self.stage / "binary" / f"{child}.mdl"
        path.unlink()
        self.assertFalse(self.matches())
        path.write_bytes(self.parts[child] + b"modified")
        self.assertFalse(self.matches())
        path.write_bytes(self.parts[child])
        self.receipt["banks"][child] -= 1
        self.assertFalse(self.matches())

    def test_updated_file_hash_alone_does_not_prove_the_original_animation_is_unchanged(self):
        child = "pmh_ra001_b01"
        changed = bytearray(self.parts[child])
        model = repack.banks.CompiledBridge(changed)
        duration = 12 + model.clips[0].start + 112
        struct.pack_into("<f", changed, duration, struct.unpack_from("<f", changed, duration)[0] + 1)
        path = self.stage / "binary" / f"{child}.mdl"
        path.write_bytes(changed)
        self.receipt["bank_sha256"][child] = digest(changed)
        # The native file remains readable and its recorded size/hash now match;
        # only reconstructing and comparing to source_sha reveals the change.
        repack.banks.CompiledBridge(changed)
        self.assertFalse(self.matches())

    def test_unsafe_resource_names_are_rejected_before_reading_or_joining(self):
        for name in ("../outside", "pmh_ra001_b01/../outside", "pmh_ra001_b01_extra"):
            with self.subTest(name=name), patch.object(repack.banks, "join") as join:
                receipt = copy.deepcopy(self.receipt)
                receipt["banks"][name] = 1
                receipt["bank_sha256"][name] = digest(b"x")
                self.assertFalse(self.matches(receipt))
                join.assert_not_called()

    def test_banks_at_the_transfer_limit_are_not_reused(self):
        with patch.object(repack.banks, "LIMIT_BYTES", max(map(len, self.parts.values()))):
            self.assertFalse(self.matches())

    def test_malformed_receipts_fail_closed(self):
        for receipt in ([], {}, {**self.receipt, "banks": None},
                        {**self.receipt, "bank_sha256": []},
                        {**self.receipt, "banks": {}},
                        {**self.receipt, "bank_sha256": {self.head: "invalid"}}):
            with self.subTest(receipt=receipt):
                self.assertFalse(self.matches(receipt))


class RobeRepackPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(repack.robes, "ROOT", self.root).start()
        paths = ["tools/GenerateRobeRgbModels.py",
                 *(f"tools/{name}" for name in repack.robes.PACKAGING_INPUTS),
                 "tools/RobePoseAudit.py", "sw_cr_creature/a_ba.mdl"]
        self.files = {}
        for relative in paths:
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"unchanged validated content")
            self.files[relative] = repack.robes.file_digest(path)
        self.manifest = {"files": self.files, "stock_model_sha256": {"stock_body": "source hash"}}
        self.active = {"a_ba": self.root / "sw_cr_creature/a_ba.mdl"}

    def test_unchanged_hashes_do_not_hide_new_active_resource_overrides(self):
        with patch.object(repack.robes, "fresh_active_models", return_value=self.active) as discover, \
                patch.object(repack.robes, "model_path_errors", return_value=["New active model override"]) as paths:
            with self.assertRaisesRegex(ValueError, "New active model override"):
                repack.preflight(self.manifest)
            discover.assert_called_once_with()
            paths.assert_called_once_with(self.files, self.manifest["stock_model_sha256"], self.active)

    def test_current_active_sources_allow_only_packaging_hashes_to_be_refreshed(self):
        with patch.object(repack.robes, "fresh_active_models", return_value=self.active), \
                patch.object(repack.robes, "model_path_errors", return_value=[]) as paths:
            changed = self.root / "tools/RobeAnimationBanks.py"
            changed.write_bytes(b"new lossless packaging implementation")
            result = repack.preflight(self.manifest)
            expected = {"tools/RobeAnimationBanks.py", "tools/RepackRobeAnimationBanks.py"}
            self.assertEqual(expected, set(result))
            self.assertEqual(repack.robes.file_digest(changed), result["tools/RobeAnimationBanks.py"])
            paths.assert_called_once_with(self.files, self.manifest["stock_model_sha256"], self.active)
            (self.root / "tools/RobePoseAudit.py").write_bytes(b"changed pose validation")
            with self.assertRaisesRegex(ValueError, "Prior validated input/output changed"):
                repack.preflight(self.manifest)

    def test_generator_changes_require_regeneration_even_with_current_outputs(self):
        (self.root / "tools/GenerateRobeRgbModels.py").write_bytes(b"changed authoring or skin generation")
        with self.assertRaisesRegex(ValueError, "Prior validated input/output changed: tools/GenerateRobeRgbModels.py"):
            repack.preflight(self.manifest)

    def test_missing_generator_fingerprint_cannot_certify_existing_outputs(self):
        self.manifest["files"].pop("tools/GenerateRobeRgbModels.py")
        with self.assertRaisesRegex(ValueError, "Missing validated generator fingerprint"):
            repack.preflight(self.manifest)


if __name__ == "__main__":
    unittest.main()
