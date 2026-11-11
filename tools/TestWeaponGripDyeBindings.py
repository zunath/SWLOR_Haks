"""Preserve installed dye rows while completing generated hand bindings."""
from pathlib import Path
import tempfile
import unittest

import GenerateWeaponGripHands as grip


class WeaponGripDyeBindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tintmap.2da'
        self.header = b'2DA V2.0\r\n\r\nMODEL MATERIAL CHANNEL\r\n'
        self.source = b'7 hand_source metal ARMOR1\r\n'
        self.other = b'19 unrelated cloth CLOTH1\r\n'
        self.alias = b'41 HAND_ALIAS metal ARMOR1\r\n'
        self.rows = [('hand_source', 'hand_alias')]

    def test_complete_interleaved_bindings_validate_without_rewriting(self):
        original = self.header + self.alias + self.other + self.source
        self.path.write_bytes(original)
        grip.synchronize_dye_bindings(self.path, self.rows)
        grip.synchronize_dye_bindings(self.path, self.rows, apply=True)
        self.assertEqual(original, self.path.read_bytes())

    def test_missing_binding_check_fails_without_writing(self):
        original = self.header + self.source + self.other
        self.path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, '1 missing'):
            grip.synchronize_dye_bindings(self.path, self.rows)
        self.assertEqual(original, self.path.read_bytes())

    def test_apply_appends_only_missing_rows_and_is_byte_idempotent(self):
        original = (self.header + self.source + self.alias + self.other
                    + b'82 hand_source cloth CLOTH2\r\n')
        self.path.write_bytes(original)
        grip.synchronize_dye_bindings(self.path, self.rows, apply=True)
        expected = original + b'83 hand_alias cloth CLOTH2\r\n'
        self.assertEqual(expected, self.path.read_bytes())
        grip.synchronize_dye_bindings(self.path, self.rows)
        grip.synchronize_dye_bindings(self.path, self.rows, apply=True)
        self.assertEqual(expected, self.path.read_bytes())

    def test_unexpected_material_binding_is_rejected_without_writing(self):
        original = self.header + self.source + b'9 hand_alias metal CLOTH1\r\n'
        self.path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            grip.synchronize_dye_bindings(self.path, self.rows, apply=True)
        self.assertEqual(original, self.path.read_bytes())

    def test_duplicate_alias_binding_is_rejected_without_writing(self):
        original = self.header + self.source + self.alias + self.alias
        self.path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            grip.synchronize_dye_bindings(self.path, self.rows, apply=True)
        self.assertEqual(original, self.path.read_bytes())

    def test_apply_preserves_unterminated_original_prefix(self):
        original = self.header + self.source.rstrip(b'\r\n')
        self.path.write_bytes(original)
        grip.synchronize_dye_bindings(self.path, self.rows, apply=True)
        self.assertEqual(original + b'\r\n8 hand_alias metal ARMOR1\r\n', self.path.read_bytes())


if __name__ == '__main__':
    unittest.main()
