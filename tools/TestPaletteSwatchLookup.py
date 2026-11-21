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
import TintRgbShading as shading

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


def row_shades(pixels, row):
    """One palette row as the shaders sample it: 256 RGBA shades in 0-1."""
    return [tuple(float(value) / 255 for value in pixels[row, shade, [2, 1, 0, 3]]) for shade in range(256)]


def shade_rgb(pixels, group, rgb):
    """Mirror the shaders: the color's swatch, then the shared exact-RGB shading."""
    palette_row, _ = tint.PALETTE_LOOKUP_GROUPS[group]
    color = pick(pixels, group, rgb)
    return color, shading.custom_rgb_shades(rgb, row_shades(pixels, palette_row + color),
                                            row_shades(pixels, palette_row))


def shade_rgb_2026_11_17(pixels, group, rgb):
    """The 2026-11-17 shading: swatch row + (rgb - midtone) * row luminance ratio, swatch coverage."""
    palette_row, _ = tint.PALETTE_LOOKUP_GROUPS[group]
    color = pick(pixels, group, rgb)
    row, first = row_shades(pixels, palette_row + color), row_shades(pixels, palette_row)
    luminance = np.array(shading.RGB_LUMINANCE)
    midpoint = np.array(row[128][:3])
    ramp = first if midpoint @ luminance < .5 / 255 else row
    ramp_midpoint = max(np.array(ramp[128][:3]) @ luminance, 1 / 255)
    target = np.array(rgb) / 255
    return color, [tuple(np.clip(np.array(row[shade][:3]) + (target - midpoint)
                          * max(np.array(ramp[shade][:3]) @ luminance / ramp_midpoint, 0), 0, 1)) + (row[shade][3],)
            for shade in range(256)]


CLOTH = 3  # cloth, leather and tattoo layers share the cloth palette's lookup group


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

    def test_every_swatch_as_rgb_shades_like_its_row(self):
        for group, (palette_row, _) in enumerate(tint.PALETTE_LOOKUP_GROUPS):
            first = row_shades(self.pixels, palette_row)
            for color in range(176):
                row = row_shades(self.pixels, palette_row + color)
                midtone = tuple(int(v) for v in self.pixels[palette_row + color, 128, 2::-1])
                if np.dot(midtone, shading.RGB_LUMINANCE) / 255 < shading.RGB_DARK_SWATCH:
                    continue  # black rows and skin row 31: see test_black_dyes_take_the_first_rows_matte_coverage
                with self.subTest(group=group, color=color):
                    shades = shading.custom_rgb_shades(midtone, row, first)
                    self.assertLess(np.abs(np.array(shades) - np.array(row)).max(), 1e-6)

    def test_black_dyes_take_the_first_rows_matte_coverage(self):
        # Bug report 2026-11-20: black and near-black cloth chose the half-reflective
        # black row 57 (lowest ID among the black rows) and its reflection turned a
        # hood flat grey. The black rows differ only in reflection. Skin row 31
        # (midtone 1,1,1 rising to white) turned near-black skin white.
        for group, (palette_row, _) in enumerate(tint.PALETTE_LOOKUP_GROUPS):
            first = np.array(row_shades(self.pixels, palette_row))
            for rgb in ((0, 0, 0), (2, 2, 2), (5, 4, 6)):
                with self.subTest(group=group, rgb=rgb):
                    _, shades = shade_rgb(self.pixels, group, rgb)
                    shades = np.array(shades)
                    np.testing.assert_array_equal(first[:, 3], shades[:, 3])
                    self.assertLessEqual(shades[:, :3].max() * 255, max(rgb) * 1.6 + .5)
        _, before = shade_rgb_2026_11_17(self.pixels, CLOTH, (2, 2, 2))
        self.assertLess(before[128][3], .5, "control: the 11/17 shading took row 57's reflection")

    def test_reported_dark_neutral_dyes_stay_dark_and_neutral(self):
        # Bug report 2026-11-20: hood dyed 44,44,48 and 2,2,2, tunic "graphite" 20,20,24.
        # 20,20,24's nearest cloth swatch is olive row 167, whose shades turn green
        # in shadow and blue-white in highlights; a neutral dye must not follow that hue.
        for rgb in ((2, 2, 2), (20, 20, 24), (44, 44, 48)):
            spread = max(rgb) - min(rgb)
            with self.subTest(rgb=rgb):
                    _, shades = shade_rgb(self.pixels, CLOTH, rgb)
                    for shade in range(255):
                        color = np.array(shades[shade][:3]) * 255
                        self.assertLessEqual(color.max() - color.min(), spread * 1.5 + 3, f"shade {shade}")
                        if shade <= 128:
                            self.assertLessEqual(color.max(), max(rgb) + 1, f"shade {shade}")
        _, before = shade_rgb_2026_11_17(self.pixels, CLOTH, (20, 20, 24))
        green_shadow = np.array(before[40][:3]) * 255
        self.assertGreater(green_shadow[1] - green_shadow[0], 3, "control: the 11/17 shading tinted graphite green")
        purple_highlight = np.array(before[224][:3]) * 255
        self.assertGreater(purple_highlight[2] - purple_highlight[1], 30, "control: and blue-purple in highlights")

    def test_neutral_greys_away_from_a_swatch_stay_neutral(self):
        for group, (palette_row, _) in enumerate(tint.PALETTE_LOOKUP_GROUPS):
            for level in range(256):
                rgb = (level, level, level)
                color, shades = shade_rgb(self.pixels, group, rgb)
                midtone = self.pixels[palette_row + color, 128, 2::-1].astype(float)
                if np.sqrt(((midtone - level) ** 2).sum()) < shading.RGB_SWATCH_REACH:
                    continue  # close to its swatch: renders like that palette color
                with self.subTest(group=group, level=level):
                    values = np.array(shades)[:255, :3] * 255
                    self.assertLess((values.max(1) - values.min(1)).max(), 1.0)

    def test_hue_matched_dyes_keep_the_palette_shading(self):
        # Ordinary colors keep the 11/17 palette-like shading: a dye of its swatch's
        # hue still darkens and desaturates like that palette row.
        for rgb in ((200, 30, 30), (90, 60, 30), (35, 109, 150)):
            with self.subTest(rgb=rgb):
                _, shades = shade_rgb(self.pixels, CLOTH, rgb)
                _, before = shade_rgb_2026_11_17(self.pixels, CLOTH, rgb)
                difference = np.abs(np.array(shades)[32:225, :3] - np.array(before)[32:225, :3]).mean() * 255
                self.assertLess(difference, 6.0)

    def test_shaders_use_the_reference_constants(self):
        root = tint.REPOSITORY_ROOT / "sw_shader"
        constants = (
            "vec3 luminance = vec3({}, {}, {});".format(*shading.RGB_LUMINANCE),
            "vec3 chromaU = vec3({}, {}, {});".format(*("%.9f" % value for value in shading.RGB_CHROMA_U)),
            "vec3 chromaV = vec3({}, {}, {});".format(*("%.9f" % value for value in shading.RGB_CHROMA_V)),
            "1.0 / (midpointChromaSquared + {});".format(shading.RGB_CHROMA_CONFIDENCE),
            "* 255.0 / {:.1f}, 0.0, 1.0);".format(shading.RGB_SWATCH_REACH),
        )
        for name in SHADERS:
            source = (root / f"{name}.shd").read_text()
            for constant in constants:
                with self.subTest(name, constant=constant):
                    self.assertIn(constant, source)
            self.assertNotIn("swatchShade.rgb + (decodedRgb - swatchMidpoint)", source)

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
