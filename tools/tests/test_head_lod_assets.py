import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import HeadLodAssets as assets
import ModelLodAssets as shared


class HeadLodAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'tools').mkdir()
        (self.root / 'sw_pt_head').mkdir()
        self.counts = {'pfh0_head258': 29749, 'cf258_l1': 9950, 'cf258_l2': 2970}
        self.heads = {'pfh0_head258': {'closeSha256': 'close', 'levels': [
            {'model': 'cf258_l1', 'distance': 3, 'sha256': 'mid', 'triangles': 9950},
            {'model': 'cf258_l2', 'distance': 6, 'sha256': 'far', 'triangles': 2970}]}}
        self.digests = dict(zip(self.counts, ('close', 'mid', 'far')))
        for name in self.counts:
            (self.root / 'sw_pt_head' / (name + '.mdl')).write_bytes(b'native')
        (self.root / 'sw_pt_head/pfh0_head258.lod').write_text('pfh0_head258\n3 cf258_l1\n6 cf258_l2\n')
        self.write()
        self.patch = patch.object(assets, '_inspect', side_effect=self.inspect)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self):
        (self.root / assets.MANIFEST).write_text(json.dumps({'version': 1, 'heads': self.heads}))

    def inspect(self, path):
        path.read_bytes()
        return dict(identity=path.stem, sha256=self.digests[path.stem],
                    triangles=self.counts[path.stem], materials=[('plt_white', 'same_material')])

    def test_native_close_and_both_lods_are_required(self):
        self.assertEqual([], assets.check(self.root))
        (self.root / 'sw_pt_head/cf258_l2.mdl').unlink()
        self.assertTrue(assets.check(self.root))

    def test_actual_counts_enforce_each_cap_even_with_matching_metadata(self):
        self.counts['cf258_l2'] = 3001
        self.heads['pfh0_head258']['levels'][1]['triangles'] = 3001
        self.write()
        self.assertIn('exceeds 3000', assets.check(self.root)[0])

    def test_changed_close_and_changed_materials_fail(self):
        self.digests['pfh0_head258'] = 'changed'
        self.assertIn('close model changed', assets.check(self.root)[0])
        self.digests['pfh0_head258'] = 'close'
        normal = self.inspect
        def wrong(path):
            result = normal(path)
            if path.stem == 'cf258_l1':
                result['materials'] = [('plt_white', 'different_material')]
            return result
        with patch.object(assets, '_inspect', side_effect=wrong):
            self.assertIn('material bindings differ', assets.check(self.root)[0])

    def test_nan_distance_and_redirecting_close_fail(self):
        self.heads['pfh0_head258']['levels'][0]['distance'] = float('nan')
        self.write()
        self.assertIn('distances must increase', assets.check(self.root)[0])
        self.heads['pfh0_head258']['levels'][0]['distance'] = 3
        self.write()
        (self.root / 'sw_pt_head/pfh0_head258.lod').write_text('cf258_l1\n3 cf258_l1\n6 cf258_l2\n')
        self.assertIn('own close MDL', assets.check(self.root)[0])

    def test_enumeration_allows_registered_native_lod_but_rejects_unknown_shadow(self):
        with patch.object(shared, 'ROOT', self.root):
            self.assertEqual(3, len(shared.model_paths(self.root / 'sw_pt_head')))
            (self.root / 'sw_pt_head/cf258_l1.lod').write_text('cf258_l2\n')
            with self.assertRaisesRegex(ValueError, 'shadows an MDL'):
                shared.model_paths(self.root / 'sw_pt_head')

    def test_explicit_eyelash_omission_allowed_but_skin_omission_rejected(self):
        original = self.inspect
        def with_lashes(path):
            result = original(path)
            if path.stem == 'pfh0_head258':
                result['materials'].append(('plt_white', 'h324lash'))
            return result
        self.heads['pfh0_head258']['omittedEyelashMaterials'] = ['h324lash']
        self.write()
        with patch.object(assets, '_inspect', side_effect=with_lashes):
            self.assertEqual([], assets.check(self.root))
            self.heads['pfh0_head258']['omittedEyelashMaterials'] = ['same_material']
            self.write()
            self.assertIn('only existing separate eyelash', assets.check(self.root)[0])

    def test_shared_material_can_remain_after_an_eyelash_node_is_removed(self):
        original = self.inspect
        def duplicated(path):
            result = original(path)
            if path.stem == 'pfh0_head258':
                result['materials'] *= 2
            return result
        with patch.object(assets, '_inspect', side_effect=duplicated):
            self.assertEqual([], assets.check(self.root))


if __name__ == '__main__':
    unittest.main()
