"""Cloak atlas coverage, palette layer preservation, and geometry sharing regressions."""
import json
import unittest

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

    def test_authoring_changes_only_material_references(self):
        before = b'newmodel cloak\nnode skin cape\n  bitmap cloak_004\n  verts 1\n  1 2 3\nendnode\n'
        after = before.replace(b'  bitmap', b'  materialname cloaktint\n  bitmap')
        self.assertEqual(cloaks.geometry_content(None, before), cloaks.geometry_content(None, after))
        self.assertNotEqual(cloaks.geometry_content(None, before),
                            cloaks.geometry_content(None, after.replace(b'1 2 3', b'1 9 3')))


if __name__ == '__main__':
    unittest.main()
