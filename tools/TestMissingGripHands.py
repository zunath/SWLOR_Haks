"""Guard the missing-hand repair and the cuffs/dye bindings it must preserve."""
import json
from pathlib import Path
import subprocess
import unittest

import numpy as np

import RepairMissingGripHands as repair
import GenerateTintMapAssets as tint
import GeneratePartSkinModels as part_skins


class MissingGripHandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(repair.MANIFEST.read_text())
        cls.entries = cls.manifest['models']

    def test_only_diagnosed_cuff_only_hands_are_owned(self):
        expected = {f'p{gender}{body}0_hand{side}111'
                    for gender in 'mf' for body in 'hedo' for side in 'rl'}
        expected.update(f'pf{body}0_handr100' for body in 'hdo')
        self.assertEqual(expected, {Path(entry['path']).stem.lower() for entry in self.entries})
        for entry in self.entries:
            with self.subTest(path=entry['path']):
                self.assertEqual(Path(entry['donorPath']).stem.lower(), Path(entry['path']).stem.lower()[:-3] + '110')
                self.assertEqual(len(repair.meshes(repair.source(entry['sourceBlob']))), 1)

    def test_original_cuffs_and_added_donor_fingers_are_preserved(self):
        for entry in self.entries:
            with self.subTest(path=entry['path']):
                data = (repair.ROOT / entry['path']).read_bytes()
                repair.validate(entry, data)
                original = repair.meshes(repair.source(entry['sourceBlob']))[0][1]
                fingers = repair.meshes(data)[-1][1]
                # Regression: the original stopped at the cuff. The added mesh
                # must extend beyond it into the actual hand, not duplicate it.
                self.assertLess(min(v[2] for v in fingers['verts']), min(v[2] for v in original['verts']) - .025)
                vertices = np.array(fingers['verts'])
                for face in fingers['faces']:
                    triangle = vertices[face[:3]]
                    self.assertGreater(np.linalg.norm(np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])), 1e-10)

    def test_existing_dye_rows_are_untouched_and_only_added_fingers_are_bound(self):
        before = subprocess.check_output(['git', '-C', str(repair.ROOT), 'show',
                                          self.manifest['sourceCommit'] + ':sw_2da/tintmap.2da'], text=True)
        after = (repair.ROOT / 'sw_2da/tintmap.2da').read_text()
        rows = lambda text: [line.split() for line in text.splitlines()[3:] if line.strip()]
        old, new = rows(before), rows(after)
        self.assertEqual(old, new[:len(old)])
        repaired = {Path(entry['path']).stem.lower() for entry in self.entries if entry['path'].endswith('111.mdl')}
        additions = [row for row in new[len(old):] if row[1].lower() in repaired]
        self.assertEqual(len(additions), 16)
        self.assertEqual({row[1] for row in additions}, repaired)
        for row in additions:
            self.assertEqual(row[2], 'griphand_' + row[1][9])
        for side in 'rl':
            new_material = (repair.ROOT / f'sw_tint_mtr/griphand_{side}.mtr').read_text()
            old_material = repair.source(self.manifest['donorMaterialBlobs'][side]).decode()
            self.assertEqual(new_material.split(), old_material.split())

    def test_repaired_rigid_hands_have_no_generated_skin_or_lod_dependents(self):
        lods = json.loads((repair.ROOT / 'tools/ModelLodAssets.json').read_text())['aliases']
        for entry in self.entries:
            with self.subTest(path=entry['path']):
                data = (repair.ROOT / entry['path']).read_bytes()
                self.assertFalse(list(part_skins.part_skins(data)))
                self.assertFalse([name for name, alias in lods.items()
                                  if alias['canonical'].lower() == entry['path'].lower()])


if __name__ == '__main__':
    unittest.main()
