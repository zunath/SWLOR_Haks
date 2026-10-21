"""Racial part refits must pick the native palette donor and tolerate later tint rebinding."""
from pathlib import Path
import unittest

import GenerateRacialPartVariants as variants
import GenerateTintMapAssets as tint


class FakeResources:
    def __init__(self, models):
        self.models = set(models)

    def exists(self, model):
        return model in self.models


class DonorTests(unittest.TestCase):
    def test_human_of_the_same_gender_donates_first(self):
        resources = FakeResources({'pfh0_robe022', 'pmh0_robe022', 'pmd0_robe022'})
        self.assertEqual(variants.donor_for('pfd0_robe022', resources), 'pfh0_robe022')

    def test_male_human_then_other_gender_of_the_same_body(self):
        self.assertEqual(variants.donor_for('pfe0_chest263', FakeResources({'pmh0_chest263'})), 'pmh0_chest263')
        self.assertEqual(variants.donor_for('pmo0_belt200', FakeResources({'pfo0_belt200'})), 'pfo0_belt200')

    def test_droids_only_take_the_other_droid_gender(self):
        resources = FakeResources({'pfh0_pelvis020', 'pmh0_pelvis020', 'pmz0_pelvis020'})
        self.assertEqual(variants.donor_for('pfz0_pelvis020', resources), 'pmz0_pelvis020')
        self.assertIsNone(variants.donor_for('pfz0_pelvis020', FakeResources({'pfh0_pelvis020'})))

    def test_cloak_refits_install_beside_the_cloak_parts(self):
        self.assertEqual(variants.output_path('pmd0_cloak_100').parent.name, 'sw_pt_cloak')


class CheckTests(unittest.TestCase):
    def test_only_material_names_may_differ_from_the_refit(self):
        path = tint.find_active_models()['pmh0_shinr198']
        data = path.read_bytes()
        fields = tint.read_binary_model_material_fields(path, data)
        self.assertTrue(fields)
        offset = fields[0][2]
        rebound = bytearray(data)
        rebound[offset:offset + 64] = b'pmh0_s_rs_699f51'.ljust(64, b'\0')
        self.assertEqual(variants.without_materials(path, bytes(rebound)), variants.without_materials(path, data))
        moved = bytearray(data)
        moved[-1] ^= 0xFF
        self.assertNotEqual(variants.without_materials(path, bytes(moved)), variants.without_materials(path, data))


if __name__ == '__main__':
    unittest.main()
