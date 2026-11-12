"""Verify lossless normal-body rifle partitioning without widening robe catalogs."""
from pathlib import Path
import unittest
import RobeAnimationBanks as banks

class RifleAnimationBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (Path(__file__).resolve().parents[1] / 'sw_cr_creature/a_rfl_pfe0.mdl').read_bytes()
        cls.model = banks.CompiledBridge(cls.original)

    def test_partition_and_join_preserve_the_complete_native_bank(self):
        overhead = 244 + len(self.model.geometry.data)
        largest = max(len(c.data) + 4 for c in self.model.clips)
        target = max(overhead + largest + 1, len(self.original) // 2)
        parts = banks.split(self.original, target)
        self.assertGreater(len(parts), 1)
        self.assertEqual(banks.join(list(parts.values())), self.original)
        self.assertEqual(list(parts)[0], 'a_rfl_pfe0')
        self.assertTrue(all(len(data) < target for data in parts.values()))

    def test_rifle_resources_do_not_enter_the_robe_catalog(self):
        self.assertTrue(banks.is_animation_bank_name('a_rfl_pfz0_b01'))
        self.assertFalse(banks.is_bank_name('a_rfl_pfz0_b01'))
        with self.assertRaisesRegex(ValueError, 'Invalid robe animation bank'):
            banks.bank_names({'animation_bridges': {'pfz0': 'a_rfl_pfz0'}})
        for name in ['a_rfl_pfh1', 'a_rfl_pfa0', 'a_rfl_pfh0_b00001']:
            self.assertFalse(banks.is_animation_bank_name(name))

    def test_reordered_parts_are_rejected(self):
        target = max(245 + len(self.model.geometry.data) + max(len(c.data) + 4 for c in self.model.clips), len(self.original) // 2)
        parts = list(banks.split(self.original, target).values())
        with self.assertRaises(ValueError):
            banks.join(parts[::-1])

if __name__ == '__main__':
    unittest.main()
