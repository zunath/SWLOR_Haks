"""Cloak atlas coverage, palette layer preservation, and geometry sharing regressions."""
import json
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
