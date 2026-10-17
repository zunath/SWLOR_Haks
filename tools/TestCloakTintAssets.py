"""Cloak atlas coverage, palette layer preservation, and geometry sharing regressions."""
import ctypes as ct
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import CloakTintAssets as cloaks
import GenerateTintMapAssets as tint


class CloakTintTests(unittest.TestCase):
    def test_checked_in_assets_cover_every_native_cloak_texture(self):
        self.assertEqual([], cloaks.check())
        manifest = json.loads(cloaks.MANIFEST.read_text())
        self.assertEqual(list(range(len(manifest['textures']))),
                         sorted(value['index'] for value in manifest['textures'].values()))

    def test_atlas_tiles_preserve_the_original_plt_layers(self):
        manifest = json.loads(cloaks.MANIFEST.read_text())
        atlases = [tint.decode_dds_layers(cloaks.ROOT / f'sw_pt_cloak/cloak_tints{number}.dds', 2048, 4096)
                   for number in range(4)]
        for entry in manifest['textures'].values():
            if entry['source'].startswith('stock:'):
                continue
            width, _, _, layers, _ = tint.read_plt(cloaks.ROOT / entry['source'])
            scale = 512 // width
            original = np.repeat(np.repeat(layers, scale, axis=0), scale, axis=1)
            index = entry['index']
            x, y = index % 4 * 512, index % 32 // 4 * 512
            actual = atlases[index // 32][y:y + 512, x:x + 512]
            self.assertTrue(np.array_equal(original, actual), entry['source'])

    def test_shared_textures_cannot_disagree_about_opacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sw_2da").mkdir()
            (root / "sw_2da/cloakmodel.2da").write_text(
                '2DA V2.0\n\nLABEL MODEL TEXTURE ENVMAP\n'
                '0 Mantle 2 102 ****\n1 Reflective 2 102 default\n')
            with patch.object(cloaks, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "102 has conflicting ENVMAP modes"):
                    cloaks.opacity_texture_indices()

    @unittest.skipUnless(sys.platform == "win32", "Requires the hidden Windows OpenGL fixture")
    def test_palette_alpha_is_cutout_only_without_an_environment_map(self):
        self.check_palette_coverage((None,))

    @unittest.skipUnless(sys.platform == "win32", "Requires the hidden Windows OpenGL fixture")
    def test_exact_rgb_keeps_reference_cutouts_for_every_palette_layer(self):
        self.check_palette_coverage(range(10))

    def check_palette_coverage(self, rgb_layers):
        from TestTintShaderMaterials import OpenGL, MaterialTest

        # Keep the shipped sampling, palette resolution and output-alpha code;
        # remove lighting only so reflection coverage and opacity are observable.
        adapter = """
#undef LIGHTING
#define LIGHTING 0
#define SPECULAR_LIGHT 1
// Match inc_common's suppression of discard for an opaque placeholder texture.
#if NO_DISCARD == 1 && FORCE_ALLOW_DISCARD != 1
#define discard
#endif
uniform vec4 materialFrontDiffuse;
varying vec2 vVertexTexCoords;
vec2 vTexCoords;
vec4 FragmentColor;
float fEnvMapLevel;
vec3 ApplyColorSpace(vec3 color) { return color; }
void SetupStandardShaderInputs() { vTexCoords = vVertexTexCoords; fEnvMapLevel = 1.0; }
void ApplyStandardShader() {}
"""
        shader = (cloaks.ROOT / "sw_shader/fs_cloaktint.shd").read_text()
        shader = shader.replace('#include "inc_standard"', adapter).replace(
            "gl_FragColor = vec4(FragmentColor.rgb, outputAlpha);",
            "gl_FragColor = vec4(fEnvMapLevel, 0.25, 0.75, outputAlpha);")
        gl = OpenGL()
        try:
            test = MaterialTest(gl)
            clear_color = gl.function("glClearColor", None, ct.c_float, ct.c_float, ct.c_float, ct.c_float)
            clear = gl.function("glClear", None, ct.c_uint)
            enable = gl.function("glEnable", None, ct.c_uint)
            disable = gl.function("glDisable", None, ct.c_uint)
            blend = gl.function("glBlendFuncSeparate", None, ct.c_uint, ct.c_uint, ct.c_uint, ct.c_uint)
            blend(0x0302, 0x0303, 1, 0x0303)  # Source-alpha RGB; source-over alpha.
            sentinel = (0.125, 0.25, 0.375, 0.5)
            mantle = json.loads(cloaks.MANIFEST.read_text())["textures"]["102"]["index"]
            self.assertIn(mantle, cloaks.opacity_texture_indices())
            self.assertNotIn(32, cloaks.opacity_texture_indices())
            cases = [(tile, 0) for tile in cloaks.opacity_texture_indices()] + [(32, 1)]
            for tile, environment, no_discard in [(t, e, n) for t, e in cases for n in (0, 1)]:
                program = test.program(f"#version 120\n#define NO_DISCARD {no_discard}\n" + shader)
                try:
                    test.use(program)
                    test.scalar(test.location(program, b"cloakTexture"), float(tile))
                    # Check actual compositing over a contrasting background, both
                    # in the opaque pass and with source-alpha blending enabled.
                    rows = ("rowSkin", "rowHair", "rowMetal1", "rowMetal2", "rowCloth1", "rowCloth2",
                            "rowLeath1", "rowLeath2", "rowTat1", "rowTat2")
                    for rgb_layer in rgb_layers:
                        for blending in (False, True):
                            (enable if blending else disable)(0x0BE2)
                            for alpha in (0.0, 0.25, 0.49, 0.5, 0.51, 0.75, 1.0):
                                with self.subTest(tile=tile, environment=environment, no_discard=no_discard,
                                                  blending=blending, alpha=alpha, rgb_layer=rgb_layer):
                                    # Each case starts in native palette mode. The fixture
                                    # then publishes an exact-RGB scalar only when requested.
                                    for row in rows:
                                        test.scalar(test.location(program, row.encode()), 0.0)
                                    rgb = None if rgb_layer is None else (rgb_layer, 180, 75, 40)
                                    test.sample(program, 1, alpha, rgb=rgb)
                                    # sample() uploads the packed layer on texture unit 7.
                                    # Bind every atlas to that unit, including atlas 3,
                                    # then redraw after clearing the fixture's setup draw.
                                    for sampler in (6, 8, 9):
                                        test.integer(test.location(program, f"texUnit{sampler}".encode()), 7)
                                    clear_color(*sentinel)
                                    clear(0x4000)
                                    test.begin(0x0007)
                                    for point in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                                        test.vertex(*point)
                                    test.end()
                                    result = (ct.c_float * 4)()
                                    test.read(4, 4, 1, 1, 0x1908, 0x1406, result)
                                    resolved_alpha = (1.0 if environment and rgb_layer is not None
                                                      and rgb_layer not in (2, 3) else alpha)
                                    expected = ((1.0 - resolved_alpha, 0.25, 0.75, 1.0) if environment else
                                                (sentinel if resolved_alpha < 0.5 else (0.0, 0.25, 0.75, 1.0)))
                                    for actual, value in zip(result, expected):
                                        self.assertAlmostEqual(value, actual, places=5)
                finally:
                    test.delete_program(program)
        finally:
            gl.close()

    def test_every_cloak_mdl_must_have_a_recorded_binding(self):
        manifest = json.loads(cloaks.MANIFEST.read_text())
        manifest['models'].pop('sw_pt_cloak/pfh0_cloak_150.mdl')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'CloakTintAssets.json'
            path.write_text(json.dumps(manifest))
            with patch.object(cloaks, 'MANIFEST', path):
                self.assertIn('Cloak tint bindings do not cover exactly the cloak MDLs; rerun with --game-data',
                              cloaks.check())

    def test_only_surfaces_naming_real_textures_or_materials_are_fixed(self):
        # Model 007's visible skin names cala_cloak, which no resource provides.
        ascii_model = (b'newmodel pfo0_cloak_007\nnode skin Cloak_Skin\n  bitmap cala_cloak\n'
                       b'  materialname cloaktint\nendnode\nnode trimesh clasp\n  bitmap gem01\n'
                       b'  materialname gem_mtr\nendnode\n')
        self.assertEqual([], cloaks.fixed_surfaces(Path('x.mdl'), ascii_model, set(), set()))
        self.assertEqual(['gem01', 'gem_mtr'],
                         cloaks.fixed_surfaces(Path('x.mdl'), ascii_model, {'gem01'}, {'gem_mtr', 'cloaktint'}))
        binary = cloaks.ROOT / 'sw_pt_cloak/pfh0_cloak_150.mdl'
        self.assertEqual([], cloaks.fixed_surfaces(binary, binary.read_bytes(), set(), {'cloaktint'}))
        self.assertEqual(['cloak__cl_c8ad14'],
                         cloaks.fixed_surfaces(binary, binary.read_bytes(), {'cloak__cl_c8ad14'}, set()))

    def test_authoring_changes_only_material_references(self):
        before = b'newmodel cloak\nnode skin cape\n  bitmap cloak_004\n  verts 1\n  1 2 3\nendnode\n'
        after = before.replace(b'  bitmap', b'  materialname cloaktint\n  bitmap')
        self.assertEqual(cloaks.geometry_content(None, before), cloaks.geometry_content(None, after))
        self.assertNotEqual(cloaks.geometry_content(None, before),
                            cloaks.geometry_content(None, after.replace(b'1 2 3', b'1 9 3')))


if __name__ == '__main__':
    unittest.main()
