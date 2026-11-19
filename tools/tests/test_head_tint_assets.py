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



if __name__ == '__main__':
    unittest.main()
