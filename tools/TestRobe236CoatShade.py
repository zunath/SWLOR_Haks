"""Robe 236's coat must keep the dark shade players had before the March 2026 texture pass.

SWLOR_Haks #291 raised the Leather 1 shade of the robe 236 PLT (90% of the texture, the coat)
from a mean of about 38 to about 61, so every dye rendered its coat much lighter. The tint map
is rebuilt from the original PLT; this guards against regenerating it from the brightened one.
"""
import json
import unittest

import numpy as np

import GenerateTintMapAssets as tint

ORIGINAL_SOURCE_SHA256 = "6ab6cc6871496bee3ed14327d8c987666e51627fb90020c4d340900bfd261347"
BRIGHTENED_SOURCE_SHA256 = "915e90331089958720e01fe44d3af409e0b9ad1a139535268f7a7e4cd02218df"
LEATHER1 = 6
MATERIALS = ("pmh0_robe236", "pfh0_robe236")


def manifest_entries() -> dict[str, dict[str, object]]:
    entries = json.loads(tint.SOURCE_MANIFEST.read_text(encoding="utf-8"))
    return {str(entry["material"]).lower(): entry for entry in entries if entry["material"] in MATERIALS}


class Robe236CoatShadeTests(unittest.TestCase):
    def test_materials_are_built_from_the_original_plt(self):
        entries = manifest_entries()
        self.assertEqual(set(MATERIALS), set(entries))
        for material, entry in entries.items():
            with self.subTest(material=material):
                self.assertEqual(ORIGINAL_SOURCE_SHA256, entry["sourceSha256"])
                self.assertNotEqual(BRIGHTENED_SOURCE_SHA256, entry["sourceSha256"])

    def test_materials_bind_the_recorded_tint_map(self):
        for material, entry in manifest_entries().items():
            with self.subTest(material=material):
                lines = (tint.OUTPUT_MTR_DIRECTORY / f"{material}.mtr").read_text().split()
                self.assertEqual(entry["texture"], lines[lines.index("texture7") + 1])

    def test_coat_keeps_its_original_dark_shade(self):
        for material, entry in manifest_entries().items():
            with self.subTest(material=material):
                path = tint.REPOSITORY_ROOT / str(entry["output"])
                width, height = int(entry["width"]), int(entry["height"])
                shades = tint.decode_dds_shades(path, width, height).astype(np.float64)
                layers = tint.decode_dds_layers(path, width, height)
                coat = layers == LEATHER1
                self.assertGreater(coat.mean(), 0.85)
                # Original PLT: mean 38.2. Brightened PLT: mean 60.9.
                self.assertLess(shades[coat].mean(), 42.0)


if __name__ == "__main__":
    unittest.main()
