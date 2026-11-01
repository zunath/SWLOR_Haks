#!/usr/bin/env python3
"""Guard dyed Republic Trooper metal against its authored specular/roughness maps.

Run from tools/: python -B -m unittest TestPaletteSpecularMaps -v
"""

from pathlib import Path
import json
import re
import tempfile
import unittest
from unittest.mock import patch

import GenerateTintMapAssets as g

ROOT = Path(__file__).resolve().parents[1]
REPUBLIC_TROOPER_PARTS = re.compile(
    r"^p[mf][a-z][0-9]_(bicep[lr]101|fore[lr]101|hand[lr]101|leg[lr]121|chest099|pelvis156|"
    r"shin[lr]102|sho[lr]197|foot[lr]186)$")
AUTHORED = ["customshaderVS vslit_sm_nm", "renderhint NormalAndSpecMapped",
            "texture1 rep_leg_n", "texture2 rep_leg_s", "texture3 rep_leg_r"]


def bindings(text):
    return {match.group(1).lower(): match.group(2).lower()
            for match in re.finditer(r"(?im)^\s*(texture\d+)\s+(\S+)\s*$", text)}


class PaletteSpecularMapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        for name, value in (("_SOURCE_MTR_PATHS_BY_RESREF", {}), ("_MTR_PATHS_BY_RESREF", {}), ("_PROFILE_SIGNATURES", {}),
                            ("_MATERIAL_SOURCES", {}), ("OUTPUT_MTR_DIRECTORY", self.output)):
            mock = patch.object(g, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def generate(self, lines, texture="mask", **options):
        path = self.output / "pmh0_legl121.mtr"
        path.write_text("\n".join(lines) + "\n")
        return g.tint_material_text(path, "pmh0_legl121", texture, 512, 512, **options)

    def test_generation_unbinds_specular_maps_and_keeps_normal_map(self):
        text = self.generate(AUTHORED)
        self.assertEqual(bindings(text)["texture1"], "rep_leg_n")
        self.assertEqual(bindings(text)["texture2"], "null")
        self.assertEqual(bindings(text)["texture3"], "null")
        self.assertIn("customshaderFS fs_plt_tinter_nm", text)
        # Regenerating the output keeps the result stable.
        self.assertEqual(self.generate(text.splitlines()), text)

    def test_authored_profiles_are_unbound_without_renaming_scoped_aliases(self):
        profile = ("pmh0_legl121", AUTHORED)
        text = self.generate([], profile=profile)
        self.assertEqual(bindings(text)["texture2"], "null")
        self.assertEqual(bindings(text)["texture3"], "null")
        # Scoped alias resrefs hash this signature; it still sees the authored maps.
        signature = dict(g.material_profile_signature("pmh0_legl121", profile))
        self.assertEqual(signature[("texture2",)], "texture2 rep_leg_s")
        self.assertEqual(signature[("texture3",)], "texture3 rep_leg_r")

    def test_other_authored_specular_maps_stay_bound(self):
        text = self.generate(["customshaderVS vslit_sm_nm", "renderhint NormalAndSpecMapped",
                              "texture1 pme0_chest211_N", "texture2 pme0_chest211_S"])
        self.assertEqual(bindings(text)["texture2"], "pme0_chest211_s")

    def test_forearm_30_maps_are_unbound_only_on_its_metal_mask(self):
        for name in g.PALETTE_SPECULAR_MASK_MAPS["tm_2c6676909ab21"]:
            with self.subTest(name=name):
                normal = name[:-1] + "n"
                authored = ["customshaderVS vslit_sm_nm", "texture1 " + normal, "texture2 " + name]
                metal = self.generate(authored, texture="tm_2c6676909ab21")
                self.assertEqual(bindings(metal)["texture2"], "null")
                self.assertEqual(bindings(metal)["texture1"], normal)
                cloth = self.generate(authored, texture="tm_29999d953bf13")
                self.assertEqual(bindings(cloth)["texture2"], name)
                signature = self.generate(authored, texture="tm_2c6676909ab21",
                                          keep_palette_specular_maps=True)
                self.assertEqual(bindings(signature)["texture2"], name)

    def test_approved_metal_material_keeps_other_masks_and_materials_unchanged(self):
        authored = ["customshaderVS vslit_sm_nm", "texture1 shared_n", "texture2 shared_s",
                    "texture3 shared_r", "texture5 shared_emit", "texture9 shared_alpha"]
        record = {"mask": "metal_mask", "maps": {"texture2": "shared_s", "texture3": "shared_r"}}
        with patch.object(g, "_PALETTE_METAL_MATERIALS", {"pmh0_legl121": record}):
            metal = bindings(self.generate(authored, texture="metal_mask"))
            self.assertEqual((metal["texture2"], metal["texture3"]), ("null", "null"))
            self.assertEqual((metal["texture1"], metal["texture5"]), ("shared_n", "shared_emit"))
            cloth = bindings(self.generate(authored, texture="cloth_mask"))
            self.assertEqual((cloth["texture2"], cloth["texture3"]), ("shared_s", "shared_r"))
            signature = bindings(self.generate(authored, texture="metal_mask",
                                               keep_palette_specular_maps=True))
            self.assertEqual((signature["texture2"], signature["texture3"]), ("shared_s", "shared_r"))
            path = self.output / "other.mtr"
            path.write_text("\n".join(authored) + "\n")
            other = bindings(g.tint_material_text(path, "other", "metal_mask", 512, 512))
            self.assertEqual((other["texture2"], other["texture3"]), ("shared_s", "shared_r"))

    def test_canonical_signatures_survive_regenerating_already_unbound_materials(self):
        authored = ["customshaderVS vslit_sm_nm", "texture1 shared_n", "texture2 shared_s",
                    "texture3 shared_r"]
        record = {"mask": "metal_mask", "maps": {"texture2": "shared_s", "texture3": "shared_r"}}
        with patch.object(g, "_PALETTE_METAL_MATERIALS", {"pmh0_legl121": record}):
            path = self.output / "pmh0_legl121.mtr"
            path.write_text("\n".join(authored) + "\n")
            before = g.material_profile_signature("pmh0_legl121")
            self.assertEqual(dict(before)[("texture2",)], "texture2 shared_s")
            path.write_text(g.tint_material_text(path, "pmh0_legl121", "metal_mask", 512, 512))
            g._PROFILE_SIGNATURES.clear()
            self.assertEqual(g.material_profile_signature("pmh0_legl121"), before)
            # An explicit source profile still owns its intentional null maps.
            empty = ["customshaderVS vslit_sm_nm", "texture1 shared_n", "texture2 null"]
            signature = dict(g.material_profile_signature("pmh0_legl121", ("explicit", empty)))
            self.assertNotIn(("texture2",), signature)
            path.write_text(path.read_text().replace("texture2 null", "texture2 revised_s"))
            g._PROFILE_SIGNATURES.clear()
            self.assertEqual(dict(g.material_profile_signature("pmh0_legl121"))[("texture2",)],
                             "texture2 revised_s")
            path.write_text(path.read_text().replace("texture2 revised_s", "texture2 null")
                            .replace("texture7 metal_mask", "texture7 other_mask"))
            g._PROFILE_SIGNATURES.clear()
            self.assertNotIn(("texture2",), dict(g.material_profile_signature("pmh0_legl121")))

    def test_registered_materials_use_metal_masks_without_authored_reflectance_maps(self):
        metal_masks = {entry["texture"] for entry in json.loads(g.SOURCE_MANIFEST.read_text())
                       if {2, 3} & set(entry["layers"])}
        paths = {path.stem.lower(): path for path in (ROOT / "sw_tint_mtr").glob("*.mtr")}
        for material, record in g.palette_metal_materials().items():
            with self.subTest(material=material):
                mask = record["mask"]
                self.assertIn(mask, metal_masks)
                bound = bindings(paths[material].read_text())
                self.assertEqual(bound["texture7"], mask)
                self.assertEqual(bound.get("texture2", "null"), "null")
                self.assertEqual(bound.get("texture3", "null"), "null")

    def test_checked_in_materials_do_not_bind_palette_specular_maps(self):
        republic = []
        for path in sorted((ROOT / "sw_tint_mtr").glob("*.mtr")):
            bound = bindings(path.read_text())
            for unit in ("texture2", "texture3"):
                self.assertNotIn(bound.get(unit), g.PALETTE_SPECULAR_MAPS, f"{path.stem} {unit}")
                self.assertNotIn(bound.get(unit),
                                 g.PALETTE_SPECULAR_MASK_MAPS.get(bound.get("texture7"), ()),
                                 f"{path.stem} {unit}")
            if REPUBLIC_TROOPER_PARTS.match(path.stem.lower()):
                republic.append(path.stem)
                self.assertRegex(bound.get("texture1", ""), r"^rep_\w+_n$", path.stem)
        # Eight body models for every two-sided part plus chest, pelvis and human feet.
        self.assertEqual(len(republic), 116)


if __name__ == "__main__":
    unittest.main()
