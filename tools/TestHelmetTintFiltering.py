"""Compare dye edges with bilinear filtering of resolved PLT colors.

Covers helm 53 on fs_plt_tinter and neck 267 on fs_plt_tinter_nm.

Run with --game-data pointing to the installed NWN data directory. Uses the
shipped BC5 mask and palette on a hidden OpenGL framebuffer. No game is started.
"""
import argparse
import ctypes as ct
import json
from pathlib import Path
import re
import tempfile
import unittest

import numpy as np

import GenerateTintMapAssets as g
from TestTintShaderMaterials import OpenGL, MaterialTest, ADAPTER, STANDARD, stock_material


ROOT = Path(__file__).resolve().parents[1]
ROWS = ("rowSkin", "rowHair", "rowMetal1", "rowMetal2", "rowCloth1", "rowCloth2",
        "rowLeath1", "rowLeath2", "rowTat1", "rowTat2")
BASES = (0, 176, 352, 528, 704, 704, 880, 880, 1056, 1056)
GAME_DATA = None


class TintEdgeFilteringCases:
    SHADER = MODEL = None

    @classmethod
    def setUpClass(cls):
        cls.gl = OpenGL()
        cls.addClassCleanup(cls.gl.close)
        cls.test = MaterialTest(cls.gl)
        shader = (ROOT / f"sw_shader/{cls.SHADER}.shd").read_text()
        shader, count = re.subn(r"gl_FragColor\s*=\s*vec4\(FragmentColor.rgb, outputAlpha\);",
                                "gl_FragColor = paletteColor;", shader)
        assert count == 1
        fragment = "#version 120\n" + shader.replace('#include "inc_standard"',
            ADAPTER + stock_material(GAME_DATA) + STANDARD)
        vertex = """#version 120
uniform vec2 testUV;
varying vec2 vVertexTexCoords;
void main() { gl_Position = gl_Vertex; vVertexTexCoords = testUV; }
"""
        cls.program = cls.test.program(fragment, vertex)
        cls.addClassCleanup(cls.test.delete_program, cls.program)
        cls.uv = cls.gl.function("glUniform2f", None, ct.c_int, ct.c_float, ct.c_float)
        cls.test.sample(cls.program, 1, 1)
        cls.test.use(cls.program)
        cls.entry = next(entry for entry in json.loads((ROOT / "tools/TintMapSources.json").read_text())
                         if entry["model"] == cls.MODEL)
        cls.width, cls.height = cls.entry["width"], cls.entry["height"]
        cls.mask = ROOT / cls.entry["output"]
        cls.layers = g.decode_dds_layers(cls.mask, cls.width, cls.height)
        palette = (ROOT / "sw_item/plt_palette.tga").read_bytes()
        assert palette[2] == 2 and palette[16] == 32 and palette[17] & 0x20 == 0
        cls.test.active_texture(0x84C0 + 10)
        cls.test.bind_texture(0x0DE1, 11)
        cls.linear_texture()
        cls.test.texture_image(0x0DE1, 0, 0x8058, 256, 2048, 0, 0x80E1, 0x1401,
                               ct.create_string_buffer(palette[18:]))

    @classmethod
    def linear_texture(cls):
        for parameter in (0x2800, 0x2801):
            cls.test.texture_parameter(0x0DE1, parameter, 0x2601)
        for parameter in (0x2802, 0x2803):
            cls.test.texture_parameter(0x0DE1, parameter, 0x2901)
        cls.test.texture_parameter(0x0DE1, 0x813D, 0)

    def scalar(self, name, value):
        self.test.scalar(self.test.location(self.program, name.encode()), value)

    def draw(self, u, v, smooth):
        self.scalar("smoothTintEdges", smooth)
        self.uv(self.test.location(self.program, b"testUV"), u, v)
        self.test.begin(0x0007)
        for point in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            self.test.vertex(*point)
        self.test.end()
        result = (ct.c_float * 4)()
        self.test.read(4, 4, 1, 1, 0x1908, 0x1406, result)
        self.assertEqual(self.test.error(), 0)
        return np.array(result)

    def compare(self, u, v, width, height):
        # Resolve the four pixel centers independently, then interpolate RGBA.
        # This models native PLT filtering without interpolating layer IDs.
        x, y = (u % 1) * width - .5, (v % 1) * height - .5
        left, bottom = np.floor((x, y)).astype(int)
        fx, fy = x - left, y - bottom
        centers = [self.draw((left + dx + .5) / width, (bottom + dy + .5) / height, 0)
                   for dy, dx in ((0, 0), (0, 1), (1, 0), (1, 1))]
        expected = ((1 - fy) * ((1 - fx) * centers[0] + fx * centers[1])
                    + fy * ((1 - fx) * centers[2] + fx * centers[3]))
        np.testing.assert_allclose(self.draw(u, v, 1), expected, atol=.004, rtol=0)
        return np.max(np.abs(self.draw(u, v, 0) - expected))

    def colors(self, mode):
        self.scalar("useNativePalette", mode == "native")
        color_ids = (2, 31, 8, 4, 3, 20, 23, 174, 1, 2)
        native_bases = (0, 256, 512, 512, 1024, 1024, 1280, 1280, 1536, 1536)
        for layer, (row, base, color) in enumerate(zip(ROWS, BASES, color_ids)):
            value = (base + color + .5) / 2048
            if mode == "native":
                value = -1
                self.scalar(f"PLTscheme[{layer}]", (native_bases[layer] + color) / 1792)
            elif mode == "rgb":
                # Pure black background, bone-colored metal, contrasting eye insets.
                rgb = ((230, 215, 170) if layer in (2, 3) else
                       (160, 40, 20) if layer == 5 else (0, 0, 0))
                packed = rgb[0] * 65536 + rgb[1] * 256 + rgb[2]
                value = (1 + packed / 8388608 if packed < 8388608
                         else 2 + (packed - 8388608) / 4194304)
            self.scalar(row, value)

    def test_shipped_mask_edges_match_resolved_color_filtering(self):
        raw = self.mask.read_bytes()[128:]
        self.test.active_texture(0x84C0 + 7)
        self.test.bind_texture(0x0DE1, 8)
        self.linear_texture()
        upload = self.gl.function("glCompressedTexImage2D", None, ct.c_uint, ct.c_int,
            ct.c_uint, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_void_p)
        upload(0x0DE1, 0, 0x8DBD, self.width, self.height, 0, len(raw), ct.create_string_buffer(raw))
        self.scalar("tintMapWidth", self.width)
        self.scalar("tintMapHeight", self.height)
        edges = np.argwhere(self.layers[:, :-1] != self.layers[:, 1:])
        selected = edges[np.linspace(0, len(edges) - 1, 48, dtype=int)]
        for mode in ("rgb", "preset", "native"):
            self.colors(mode)
            differences = []
            for y, x in selected:
                for fraction in (.25, .5, .75):
                    with self.subTest(mode=mode, x=x, y=y, fraction=fraction):
                        differences.append(self.compare((x + .5 + fraction) / self.width,
                            (y + .5) / self.height, self.width, self.height))
            self.assertGreater(max(differences), .05, "Old nearest-layer sampling must reproduce jagged edges")
            # Repeat wrapping must also work across both texture seams and negative UVs.
            for uv in ((0, 0), (1, 1), (-.0002, .0003), (.9998, 1.0003)):
                self.compare(*uv, self.width, self.height)

    def test_four_layer_junction_never_selects_an_interpolated_dye_id(self):
        self.test.active_texture(0x84C0 + 7)
        self.test.bind_texture(0x0DE1, 8)
        self.linear_texture()
        pixels = (ct.c_float * 16)(*[value for layer, shade in ((2, .3), (4, .5), (5, .7), (7, .9))
                                    for value in (shade, (layer + .5) / 10, 0, 1)])
        self.test.texture_image(0x0DE1, 0, 0x8814, 2, 2, 0, 0x1908, 0x1406, pixels)
        self.scalar("tintMapWidth", 2)
        self.scalar("tintMapHeight", 2)
        self.colors("rgb")
        self.assertGreater(self.compare(.5, .5, 2, 2), .05)
        for uv in ((.3, .4), (.6, .7), (0, 0), (-.1, 1.1)):
            self.compare(*uv, 2, 2)


class HelmetTintFilteringTests(TintEdgeFilteringCases, unittest.TestCase):
    SHADER, MODEL = "fs_plt_tinter", "helm_053"

    def test_generation_and_activation_are_limited_to_reported_materials(self):
        enabled = [path.stem for path in (ROOT / "sw_tint_mtr").glob("*.mtr")
                   if re.search(r"parameter\s+float\s+smoothTintEdges\s+1", path.read_text())]
        # Neck 267 scoped aliases render the same mask on other racial body models.
        self.assertEqual(sorted(enabled), ["helm_053", "pfh0_n_ne_7905de", "pfh0_neck267",
                                           "pmh0_n_ne_0b3c67", "pmh0_neck267"])
        for material in ("helm_053", "helm_054"):
            with tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / f"{material}.mtr"
                path.write_text((ROOT / f"sw_tint_mtr/{material}.mtr").read_text())
                for _ in range(2):
                    text = g.tint_material_text(path, material, self.entry["texture"], 512, 512,
                                               native_palette=True)
                    self.assertEqual(text.count("parameter float smoothTintEdges 1.0"),
                                     int(material == "helm_053"))
                    self.assertIn("parameter float useNativePalette 1.0", text)
                    path.write_text(text)
                # Authored profiles may carry a different legacy bitmap name.
                # It must not change the generated material's edge-filter choice.
                text = g.tint_material_text(path, material, self.entry["texture"], 512, 512,
                    profile=("legacy_mask", path.read_text().splitlines()), native_palette=True)
                self.assertEqual(text.count("parameter float smoothTintEdges 1.0"),
                                 int(material == "helm_053"))
        for source in ("pfh0_neck267", "pmh0_neck267"):
            with tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / f"{source}.mtr"
                path.write_text((ROOT / f"sw_tint_mtr/{source}.mtr").read_text())
                for _ in range(2):
                    text = g.tint_material_text(path, source, "tm_07111bb2418e7", 512, 512)
                    self.assertEqual(text.count("parameter float smoothTintEdges 1.0"), 1)
                    self.assertIn("customshaderFS fs_plt_tinter_nm", text)
                    path.write_text(text)
            # Scoped alias resrefs hash this signature; the opt-in must not rename them.
            self.assertNotIn(("parameter", "float", "smoothtintedges"),
                             [directive for directive, _ in g.material_profile_signature(source)])


class NeckTintFilteringTests(TintEdgeFilteringCases, unittest.TestCase):
    SHADER, MODEL = "fs_plt_tinter_nm", "pfh0_neck267"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", required=True, type=Path)
    args, rest = parser.parse_known_args()
    GAME_DATA = args.game_data
    unittest.main(argv=[__file__, *rest])
