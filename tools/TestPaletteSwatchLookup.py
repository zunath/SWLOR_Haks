"""Exact RGB dyes are shaded with their nearest palette swatch's row.

The atlas rows 1536-2047 list candidate swatches per 32-level RGB cell
(GenerateTintMapAssets.palette_swatch_lookup); every tint shader reads them.
GPU parity of the shaded result is covered by TestTintShaderMaterials.py.

Run from tools/: python -B -m unittest TestPaletteSwatchLookup -v
"""
from pathlib import Path
import re
import tempfile
import unittest

import numpy as np

import GenerateTintMapAssets as tint

SHADERS = ("fs_plt_tinter", "fs_plt_tinter_nm", "fs_plt_hair_nm", "fs_cloaktint",
           "fs_hav_dev", "fs_hav_emit", "fs_hav_static")


def pick(pixels, group, rgb):
    """Mirror the shader: read the cell's candidates and keep the nearest, earliest on a tie."""
    palette_row, _ = tint.PALETTE_LOOKUP_GROUPS[group]
    cell = sum(int(np.floor(channel / 255 * 31 + .5)) * weight for channel, weight in zip(rgb, (1024, 32, 1)))
    row = tint.PALETTE_LOOKUP_BASE_ROW + group * tint.PALETTE_LOOKUP_ROWS_PER_GROUP + cell // 256
    blue, green, red, _ = pixels[row, cell % 256]
    best, best_distance = None, None
    for color in (int(red), int(green), int(blue)):
        midtone = pixels[palette_row + color, 128, 2::-1].astype(float)
        distance = float(((midtone - np.array(rgb, float)) ** 2).sum())
        if best_distance is None or distance < best_distance:
            best, best_distance = int(color), distance
    return best


class PaletteSwatchLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = tint.PALETTE_TEXTURE.read_bytes()
        cls.pixels = tint.palette_atlas_pixels(cls.raw)

    def test_checked_in_lookup_is_current(self):
        self.assertEqual([], tint.palette_lookup_errors(tint.PALETTE_TEXTURE))
        self.assertEqual([], tint.palette_atlas_errors(tint.PALETTE_TEXTURE))

    def test_every_swatch_selects_its_own_row(self):
        for group, (palette_row, _) in enumerate(tint.PALETTE_LOOKUP_GROUPS):
            midtones = [tuple(int(v) for v in self.pixels[palette_row + color, 128, 2::-1]) for color in range(176)]
            for color, midtone in enumerate(midtones):
                with self.subTest(group=group, color=color):
                    # Equal midtones resolve to the lowest ID, like the editor's closest-swatch lookup.
                    self.assertEqual(midtones.index(midtone), pick(self.pixels, group, midtone))

    def test_duplicate_reflective_black_is_never_selected(self):
        for group, (palette_row, _) in enumerate(tint.PALETTE_LOOKUP_GROUPS):
            self.assertEqual(0, self.pixels[palette_row + 61, 128, 3], "row 61 is the fully reflective black")
            self.assertNotEqual(61, pick(self.pixels, group, (0, 0, 0)))

    def test_other_colors_select_a_swatch_close_to_the_nearest(self):
        rng = np.random.default_rng(20261104)
        colors = rng.integers(0, 256, (3000, 3))
        for group, (palette_row, _) in enumerate(tint.PALETTE_LOOKUP_GROUPS):
            midtones = self.pixels[palette_row:palette_row + 176, 128, 2::-1].astype(float)
            exact = 0
            for rgb in colors:
                distances = np.sqrt(((midtones - rgb) ** 2).sum(1))
                chosen = distances[pick(self.pixels, group, tuple(rgb))]
                self.assertLessEqual(chosen - distances.min(), 8.0, f"group {group} RGB {tuple(rgb)}")
                exact += chosen == distances.min()
            self.assertGreater(exact / len(colors), 0.99, f"group {group}")

    def test_audit_rejects_stale_or_inconsistent_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "palette.tga"
            start = 18 + self.raw[0]

            def texel(row, shade):
                return start + (row * 256 + shade) * 4

            for label, offset in (("swatch midtone", texel(176 + 3, 128)),
                                  ("reserved row", texel(1300, 7)),
                                  ("lookup row", texel(1700, 9)),
                                  ("leather block", texel(880 + 5, 40))):
                corrupted = bytearray(self.raw)
                corrupted[offset:offset + 3] = bytes(255 - value for value in corrupted[offset:offset + 3])
                path.write_bytes(corrupted)
                with self.subTest(label):
                    self.assertTrue(tint.palette_lookup_errors(path))

    def test_shaders_read_the_generated_rows(self):
        root = tint.REPOSITORY_ROOT / "sw_shader"
        bases = [tint.PALETTE_LOOKUP_BASE_ROW + group * tint.PALETTE_LOOKUP_ROWS_PER_GROUP
                 for group in range(len(tint.PALETTE_LOOKUP_GROUPS))]
        for name in SHADERS:
            with self.subTest(name):
                source = (root / f"{name}.shd").read_text()
                self.assertNotIn("referenceRow", source)
                self.assertIn(f"float lookupBase = {bases[0]}.0;", source)
                for base in bases[1:]:
                    self.assertRegex(source, rf"lookupBase = {base}\.0;")
                self.assertIn(f"floor(decodedRgb * {tint.PALETTE_LOOKUP_LEVELS - 1}.0 + 0.5)", source)
                self.assertEqual(1, len(re.findall(r"paletteColor = vec4\(clamp\(shadedRgb, 0\.0, 1\.0\), environmentAlpha\);",
                                                   source)))


if __name__ == "__main__":
    unittest.main()
