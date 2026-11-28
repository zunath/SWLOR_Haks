import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import HeadTintAssets as heads


class HeadTextureResrefTests(unittest.TestCase):
    def test_different_payloads_cannot_share_a_case_insensitive_resref(self):
        manifest = {'textures': {
            'sw_pt_head/pfh0_head258_S.dds': {'sha256': 'approved'},
            'sw_tint2/pfh0_head258_s.dds': {'sha256': 'different'},
        }}
        errors = heads.texture_resref_errors(manifest)
        self.assertEqual(1, len(errors))
        self.assertIn('pfh0_head258_s', errors[0])

    def test_existing_hak_texture_outside_the_authored_manifest_is_checked(self):
        manifest = {'textures': {'sw_tint2/zab-horns-imp.dds': {'sha256': 'new'}}}
        blobs = {'sw_pt_head/zab-horns-imp.dds': 'old', 'sw_tint2/ZAB-HORNS-IMP.dds': 'new'}
        errors = heads.texture_resref_errors(manifest, blobs)
        self.assertEqual(1, len(errors))
        self.assertIn('sw_pt_head/zab-horns-imp.dds', errors[0])

    def test_identical_shared_payloads_remain_valid(self):
        manifest = {'textures': {
            'sw_pt_head/shared.dds': {'sha256': 'same'},
            'sw_tint2/SHARED.dds': {'sha256': 'same'},
        }}
        self.assertEqual([], heads.texture_resref_errors(manifest))

    def test_different_resrefs_can_have_different_payloads(self):
        manifest = {'textures': {
            'sw_pt_head/first.dds': {'sha256': 'first'},
            'sw_tint2/second.dds': {'sha256': 'second'},
        }}
        self.assertEqual([], heads.texture_resref_errors(manifest))


class ArtificialEyeChannelTests(unittest.TestCase):
    def test_male_cloth_eye_channels_match_the_remapped_masks(self):
        import GenerateTintMapAssets as tint
        root = heads.ROOT
        manifest = json.loads(heads.MANIFEST.read_text())
        for number in (267, 269, 271, 273):
            name = f'pmh0_head{number}'
            with self.subTest(head=name):
                row = next(row for row in manifest['catalogRows'] if row[0] == name)
                advertised = {int(value) for value in row[2].split(',')}
                entry = manifest['materials'][name]
                texture = next(line.split()[1] for line in entry['declarations'] if line.startswith('texture7 '))
                relative, record = next((p, r) for p, r in manifest['textures'].items() if Path(p).stem == texture)
                width, height = record['size']
                actual = set(tint.decode_dds_layers(root / relative, width, height).ravel())
                self.assertIn(4, actual)
                self.assertNotIn(6, actual)
                self.assertIn(4, advertised)
                self.assertNotIn(6, advertised)
    def test_female_bob_does_not_advertise_cloth1(self):
        import GenerateTintMapAssets as tint
        manifest = json.loads(heads.MANIFEST.read_text())
        row = next(row for row in manifest['catalogRows'] if row[0] == 'pfh0_head286')
        entry = manifest['materials'][row[1]]
        texture = next(line.split()[1] for line in entry['declarations'] if line.startswith('texture7 '))
        relative, record = next((p, r) for p, r in manifest['textures'].items() if Path(p).stem == texture)
        actual = set(tint.decode_dds_layers(heads.ROOT / relative, *record['size']).ravel())
        self.assertNotIn(4, actual)
        self.assertNotIn('4', row[2].split(','))




class CompatibilityChannelTests(unittest.TestCase):
    def test_surviving_compatibility_channels_match_their_replacement_materials(self):
        manifest = heads.load_manifest()
        self.assertEqual([], heads.compatibility_channel_errors(manifest))
        row = next(row for row in manifest['compatibility']['rows'] if row[0] == 'pfh2_head056')
        self.assertTrue({'4', '7'} <= set(row[2].split(',')))

    def test_stale_compatibility_channels_are_rejected(self):
        manifest = heads.load_manifest()
        row = next(row for row in manifest['compatibility']['rows'] if row[0] == 'pfh2_head056')
        row[2] = '0,1,8,9'
        self.assertIn('pfh2_head056: compatibility channels differ from replacement material pfh0_head056',
                      heads.compatibility_channel_errors(manifest))



class HeadCoatingBindingTests(unittest.TestCase):
    def test_coatings_do_not_replace_authored_lip_and_detail_maps(self):
        manifest = heads.load_manifest()
        for name, entry in manifest['materials'].items():
            with self.subTest(material=name):
                source = heads.material_source(entry).splitlines()
                if entry['shader'] in heads.NATIVE_PROFILES:
                    self.assertIn('texture8 progfx_coat', source)
                    self.assertFalse(any(line.startswith('texture4 ') for line in source))
                    for flag in ('progFxCoat', 'smoothTintEdges', 'useNativePalette', 'useTexture1Alpha', 'useTexture9Alpha'):
                        self.assertIn(f'parameter float {flag} 0', source)
                    continue
                self.assertEqual(['texture4 progfx_coat'], [line for line in source if line.startswith('texture4 ')])
                self.assertIn('parameter float progFxCoat 0', source)
                authored = [line for line in entry['declarations'] if line.startswith('texture8 ')]
                self.assertEqual(authored, [line for line in source if line.startswith('texture8 ')])

    def test_native_profile_cannot_discard_authored_effects_or_textures(self):
        entry = {'shader': 'fs_plt_tinter', 'parameters': {'hairMatte': 1}, 'declarations': []}
        with self.assertRaises(ValueError):
            heads.material_source(entry)
        entry['parameters'] = {}
        for slot in (4, 8):
            entry['declarations'] = [f'texture{slot} authored_detail']
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                heads.material_source(entry)

    def test_hair_profile_has_no_bound_roughness_or_self_illumination_maps(self):
        for name, entry in heads.load_manifest()['materials'].items():
            if entry['shader'] != 'fs_head_hair':
                continue
            with self.subTest(material=name):
                for line in entry['declarations']:
                    if line.startswith(('texture3 ', 'texture5 ')):
                        self.assertEqual('null', line.split()[1])


if __name__ == '__main__':
    unittest.main()
