"""Guard the exact scope of authored Droid 218 child-node repairs."""
import json
import subprocess
import unittest

import numpy as np

import RepairHandNodeNames as repair
from RobePoseAudit import Model


class HandNodeNameRepairTests(unittest.TestCase):
    def test_only_child_name_bytes_change_and_every_native_node_is_unique(self):
        entries = json.loads(repair.MANIFEST.read_text())['models']
        self.assertEqual(4, len(entries))
        for entry in entries:
            with self.subTest(hand=entry['path']):
                original = subprocess.check_output(['git', '-C', str(repair.ROOT), 'cat-file', 'blob', entry['sourceBlob']])
                output = repair.build(entry)
                self.assertEqual(output, (repair.ROOT / entry['path']).read_bytes())
                before, after = Model(original, False), Model(output, False)
                self.assertEqual(before.nodes[0], after.nodes[0])
                self.assertLess(len({n[0] for n in before.nodes}), len(before.nodes))
                self.assertEqual(len({n[0] for n in after.nodes}), len(after.nodes))
                self.assertEqual([n[1:] for n in before.nodes], [n[1:] for n in after.nodes])
                allowed = np.zeros(len(original), bool)
                for _, parent, _, _, offset in before.nodes:
                    if parent is not None:
                        allowed[offset + 32:offset + 64] = True
                old, new = np.frombuffer(original, dtype=np.uint8), np.frombuffer(output, dtype=np.uint8)
                self.assertTrue((old[~allowed] == new[~allowed]).all())
                self.assertTrue((old[allowed] != new[allowed]).any())

    def test_unrelated_hand_is_not_owned(self):
        with self.assertRaisesRegex(ValueError, 'only normal Droid hand 218'):
            repair.build({'path': 'sw_pt_rhand/pmz0_handr219.mdl'})


if __name__ == '__main__':
    unittest.main()
