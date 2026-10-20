"""Immutable native geometry remains authoritative during descendant rebinding."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import CompileModels as mdl
import RobeNativeWearers as native


class NativeWearerPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = "pfb158"
        cls.data = (Path(__file__).resolve().parents[1] / "sw_pt_root" / (cls.name + ".mdl")).read_bytes()
        cls.digest = hashlib.sha256(cls.data).hexdigest()
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        stage = Path(temporary.name)
        (stage / "original").mkdir()
        (stage / "decompiled").mkdir()
        compiler, _ = mdl.prepare_compiler(stage)
        source = stage / (cls.name + ".mdl")
        source.write_bytes(cls.data)
        mdl.run_compiler(compiler, stage, ["-de", str(source), str(stage / "original") + "/"], "original.log")
        cls.canonical = native.canonical_source((stage / "original" / source.name).read_text("latin1"),
                                                cls.data, "pfb_ra999")
        cls.candidate = native.with_parent(cls.data, "pfb_ra999")
        source.write_bytes(cls.candidate)
        mdl.run_compiler(compiler, stage, ["-de", str(source), str(stage / "decompiled") + "/"], "candidate.log")
        cls.decompiled = (stage / "decompiled" / source.name).read_text("latin1")

    def capture(self, root_hash=None, garment_hash="a" * 64, expected_garment="a" * 64):
        return native.capture(self.name, self.data, root_hash or self.digest,
                              "pfb0_robe174", garment_hash, expected_garment)

    def test_capture_rejects_unowned_native_bytes(self):
        with self.assertRaisesRegex(ValueError, "verified prior resource"):
            self.capture(root_hash="b" * 64)

    def test_capture_rejects_authored_garment_changes(self):
        with self.assertRaisesRegex(ValueError, "garment input changed"):
            self.capture(garment_hash="b" * 64)

    def test_capture_rejects_different_model_identity(self):
        with self.assertRaisesRegex(ValueError, "identity changed"):
            native.capture("pfb159", self.data, self.digest, "garment", "a" * 64, "a" * 64)

    def test_native_faces_normals_and_all_other_geometry_pass_strict_roundtrip(self):
        self.assertEqual(self.digest, self.capture()["source_sha256"])
        native.validate_preserved_bytes(self.data, self.candidate)
        native.validate_desired_source(self.canonical, self.canonical, self.data, "pfb_ra999")
        mdl.validate_round_trip(self.canonical, self.candidate, self.decompiled)

    def test_any_geometry_payload_edit_is_rejected(self):
        changed = bytearray(self.candidate)
        changed[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "outside parent/header/animation IDs"):
            native.validate_preserved_bytes(self.data, bytes(changed))

    def test_authored_material_changes_are_rejected(self):
        text = self.canonical.decode("latin1")
        changed = text.replace("bitmap NULL", "bitmap changed", 1)
        if changed == text:
            changed = text.replace("bitmap ", "bitmap changed_", 1)
        self.assertNotEqual(text, changed)
        with self.assertRaises(ValueError):
            native.validate_desired_source(changed.encode("latin1"), self.canonical, self.data, "pfb_ra999")

    def test_node_topology_changes_are_rejected(self):
        changed = self.canonical.replace(b"node dummy rootdummy", b"node dummy moved_rootdummy", 1)
        self.assertNotEqual(self.canonical, changed)
        with self.assertRaisesRegex(ValueError, "topology"):
            native.validate_desired_source(changed, self.canonical, self.data, "pfb_ra999")


if __name__ == "__main__":
    unittest.main()
