"""Neck and shoulder parts that hang below the hips must be skinned to move with the lower body,
unless HangingPartAudit.json lists them as reviewed (HangingPartAudit.py)."""
import json
import unittest

import ChestSkirtBindings as skirts
import HangingPartAudit as audit


class HangingPartAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = json.loads(audit.INVENTORY.read_text())
        cls.found = audit.audit()

    def test_no_unreviewed_part_hangs_stiffly(self):
        self.assertEqual(audit.HANG, self.inventory['hang'])
        unreviewed = sorted(set(self.found) - set(self.inventory['parts']))
        self.assertFalse(unreviewed, 'Hanging neck/shoulder parts with rigid or upper-body-only binding: review them '
                                     '(HangingPartAudit.py) and skin them to the lower body or list them as rigid by design')

    def test_inventory_lists_only_parts_that_still_hang_stiffly(self):
        fixed = sorted(set(self.inventory['parts']) - set(self.found))
        self.assertFalse(fixed, 'These parts no longer hang stiffly; remove them from HangingPartAudit.json')

    def test_rebound_capes_hang_from_the_lower_body(self):
        """Neck 289's tail followed the left upper arm, and the 291/260 capes stood off the back on the stock cloak
        bones; on every body that swings the flaps they now hang from the hips like the long coats."""
        capes = [name for name, entry in json.loads(skirts.MANIFEST.read_text())['models'].items()
                 if skirts.style(entry['path'])['cape']]
        self.assertEqual({f'p{gender}{race}0_{part}' for gender in 'fm' for race in 'he'
                          for part in ('neck289', 'neck291', 'shol260', 'shor260')}, set(capes))
        for name in capes:
            with self.subTest(model=name):
                self.assertNotIn(name, self.found)


if __name__ == '__main__':
    unittest.main()
