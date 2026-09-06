"""Regression coverage for texture resolution and non-textured engine nodes."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import AuditModelTextures as audit


class ModelTextureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "haks"
        self.root.mkdir()
        self.high = self.root / "high"
        self.low = self.root / "low"
        self.high.mkdir()
        self.low.mkdir()
        (self.root / "hakbuilder.json").write_text(json.dumps({"HakList": [
            {"Name": "high", "Path": "high"}, {"Name": "low", "Path": "low"}]}))
        self.game = Path(temporary.name) / "game/data"
        self.game.mkdir(parents=True)
        (self.game / "base.key").write_bytes(b"KEY V1  " + bytes(56))

    def model(self, body):
        (self.high / "head.mdl").write_text("newmodel head\nnode trimesh face\n" + body + "\nendnode\n")

    def test_existing_material_does_not_hide_missing_diffuse_or_normal_map(self):
        self.model("bitmap old_palette\nmaterialname face_material")
        (self.high / "face_material.mtr").write_text("texture0 old_palette\ntexture1 face_n\n")
        _, problems = audit.audit(self.root, self.game)
        self.assertEqual({(p[2], p[3]) for p in problems}, {("texture0", "old_palette"), ("texture1", "face_n")})
        (self.high / "face_material.mtr").write_text("texture0 white\ntexture7 packed\ntexture1 face_n\n")
        for name in ("white.tga", "packed.dds", "face_n.dds"):
            (self.high / name).write_bytes(b"resource")
        self.assertEqual(audit.audit(self.root, self.game)[1], [])

    def test_earlier_hak_wins_and_missing_optional_material_can_use_bitmap(self):
        self.model("bitmap existing\nmaterialname setter_name")
        (self.low / "head.mdl").write_text("newmodel broken\nnode trimesh face\nbitmap absent\nendnode\n")
        (self.high / "existing.tga").write_bytes(b"resource")
        self.assertEqual(audit.audit(self.root, self.game)[1], [])

    def test_stock_texture_pack_satisfies_reference(self):
        self.model("bitmap stock")
        packs = self.game / "txpk"
        packs.mkdir()
        header = bytearray(160)
        header[:8] = b"ERF V1.0"
        struct.pack_into("<I", header, 16, 1)
        struct.pack_into("<I", header, 24, 160)
        (packs / "textures.erf").write_bytes(header + struct.pack("<16sIHH", b"stock", 0, 3, 0))
        self.assertEqual(audit.audit(self.root, self.game)[1], [])

    def test_module_hak_order_uses_latin1_json_in_utf8_environments(self):
        module = self.root.parent / "Module/ifo/module.ifo.json"
        module.parent.mkdir(parents=True)
        module.write_bytes(json.dumps({
            "Mod_Name": {"value": "\u00ff"},
            "Mod_HakList": {"value": [{"Mod_Hak": {"value": "low"}},
                                        {"Mod_Hak": {"value": "high"}}]}
        }, ensure_ascii=False).encode("latin-1"))
        self.model("bitmap absent")
        (self.low / "head.mdl").write_text("newmodel head\nnode trimesh face\nbitmap present\nendnode\n")
        (self.low / "present.tga").write_bytes(b"resource")
        self.assertEqual(audit.audit(self.root, self.game)[1], [])

    def test_chunk_emitter_does_not_require_unused_particle_texture(self):
        data = b"newmodel test\nnode emitter sparks\ntexture nonexistent\nchunkname woodchunk\nendnode\n"
        self.assertEqual(list(audit.model_surfaces(data))[0][-1], "")

    def test_legacy_long_bitmap_and_six_face_environment_map_resolve(self):
        self.model("bitmap wsf01_fieldstne44")
        (self.high / "wsf01_fieldstne4.tga").write_bytes(b"resource")
        (self.high / "wsf01_fieldstne4.txi").write_text("bumpyshinytexture ttr01__env\n")
        for face in range(6):
            (self.high / f"ttr01__env{face}.tga").write_bytes(b"resource")
        summary, problems = audit.audit(self.root, self.game)
        self.assertEqual(problems, [])
        self.assertEqual(summary["counts"]["cubeMapReferences"], 1)
        (self.high / "ttr01__env5.tga").unlink()
        self.assertEqual(audit.audit(self.root, self.game)[1][0][3], "ttr01__env")

    def test_binary_render_flag_and_optional_trailing_data(self):
        data = bytearray(868)
        struct.pack_into("<III", data, 0, 0, 856, 0)
        struct.pack_into("<I", data, 84, 232)
        struct.pack_into("<I", data, 244+108, 33)
        mesh = 244+112
        struct.pack_into("<I", data, mesh+12, 1)
        struct.pack_into("<I", data, mesh+108, 1)
        data[mesh+120:mesh+124] = b"face"
        self.assertEqual(list(audit.model_surfaces(data + b"exporter trailer"))[0][1], "face")
        struct.pack_into("<I", data, mesh+108, 0)
        self.assertEqual(list(audit.model_surfaces(data)), [])


if __name__ == "__main__":
    unittest.main()
