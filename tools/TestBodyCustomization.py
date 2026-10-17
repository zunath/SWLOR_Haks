#!/usr/bin/env python3
"""Protect restored bicep geometry and its matching native palette masks."""

import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import CompileModels as models
import GenerateTintMapAssets as tint


class BodyCustomizationTests(unittest.TestCase):
    def normalized_geometry(self, path):
        data = bytearray(path.read_bytes())
        root = int.from_bytes(data[84:88], "little") + 12
        data[20:84] = bytes(64)
        data[root + 32:root + 64] = bytes(32)
        for bitmap, _, material, _ in tint.read_binary_model_material_fields(path, data):
            data[bitmap:bitmap + 64] = bytes(64)
            data[material:material + 64] = bytes(64)
        return data

    def test_male_biceps_preserve_pre_conversion_geometry(self):
        # swlor2_parts/pmh0_bicep[lr]001.mdl at 666c399dfe^, excluding only
        # model/root names and texture/material fields that the new resource needs.
        expected = {
            "l": "89f868aabfb1d5f69a7d58367ccfae0f3b1357dd7ef9bf3184cb4c5673d25fb9",
            "r": "02cb10d6c0049cc1fe63c168273dfd473fd3499a54d1593663e1c62ac994e2bf",
        }
        for side, digest in expected.items():
            name = f"pmh0_bicep{side}251"
            path = tint.REPOSITORY_ROOT / f"sw_pt_{side}bicep/{name}.mdl"
            self.assertEqual(hashlib.sha256(self.normalized_geometry(path)).hexdigest(), digest)
            data = path.read_bytes()
            self.assertEqual(data[20:84].split(b"\0")[0].decode(), name)
            self.assertGreater(sum(len(mesh["verts"]) for _, _, mesh in models.binary_nodes(data) if mesh), 300)

    def test_female_biceps_keep_their_authored_shape(self):
        for side in "lr":
            folder = tint.REPOSITORY_ROOT / f"sw_pt_{side}bicep"
            self.assertEqual(self.normalized_geometry(folder / f"pfh0_bicep{side}251.mdl"),
                             self.normalized_geometry(folder / f"pfh0_bicep{side}001.mdl"))

    def test_restored_models_use_matching_masks_and_skin_tints(self):
        entries = {entry["model"]: entry for entry in json.loads(tint.SOURCE_MANIFEST.read_text())}
        for side in "lr":
            male = entries[f"pmh0_bicep{side}251"]
            self.assertEqual((male["width"], male["height"]), (256, 256))
            self.assertEqual(male["layers"], [0])
            female = entries[f"pfh0_bicep{side}251"]
            self.assertEqual(female["sourceSha256"], entries[f"pfh0_bicep{side}001"]["sourceSha256"])
            for gender in "mf":
                name = f"p{gender}h0_bicep{side}251"
                path = tint.REPOSITORY_ROOT / f"sw_pt_{side}bicep/{name}.mdl"
                for _, material in tint.read_model_material_bindings(path):
                    self.assertEqual(material, name)


class DefaultMaleBodyTests(unittest.TestCase):
    PARTS = ("chest", "neck", "pelvis", "bicepl", "bicepr", "forel", "forer",
             "legl", "legr", "shinl", "shinr", "footl", "footr")
    SOURCE = "de1e6b21f003eee340db78053fd163951cb163ed^"

    def original(self, path):
        return subprocess.check_output(["git", "show", f"{self.SOURCE}:{path}"], cwd=tint.REPOSITORY_ROOT)

    def test_default_male_parts_keep_original_geometry_and_matching_rgb_palettes(self):
        entries = tint.load_source_manifest()
        catalog = {tuple(line.split()[1:3]): line.split()[3]
                   for line in tint.OUTPUT_2DA.read_text().splitlines()[3:] if len(line.split()) == 4}
        for part in self.PARTS:
            name = f"pmh0_{part}001"
            path = tint.stock_model_output_directory(name) / (name + ".mdl")
            relative = path.relative_to(tint.REPOSITORY_ROOT).as_posix()
            with self.subTest(model=name):
                original = bytearray(self.original(relative))
                current = bytearray(path.read_bytes())
                for data in (original, current):
                    for _, _, offset, _ in tint.read_binary_model_material_fields(path, data):
                        data[offset:offset + 64] = bytes(64)
                self.assertEqual(current, original)
                palette = entries[name]["modelPalette"]
                entry = entries[palette]
                raw_palette = self.original(relative[:-4] + ".plt")
                self.assertEqual(entry["sourceSha256"], hashlib.sha256(raw_palette).hexdigest())
                self.assertEqual(entry["layers"], sorted(set(raw_palette[25::2])))
                packed = tint.packed_dds_path(palette, entry)
                layers = tint.decode_dds_layers(packed, entry["width"], entry["height"])
                self.assertEqual(layers.tobytes(), raw_palette[25::2])
                self.assertEqual(hashlib.sha256(layers.tobytes()).hexdigest(), entry["layerSha256"])
                self.assertIn(f"texture7 {entry['texture']}\n", tint.mtr_path(palette).read_text())
                with patch.object(tint, "_NATIVE_MODULAR_PALETTES", set()):
                    choices = tint.native_modular_material_choices(path, entries)
                self.assertTrue(choices)
                self.assertTrue(all(source == palette for source, _, _ in choices.values()))
                for _, material in tint.read_model_material_bindings(path):
                    self.assertEqual(material, palette)
                    self.assertEqual(catalog[(name, material)], ",".join(map(str, entry["layers"])))


if __name__ == "__main__":
    unittest.main()
