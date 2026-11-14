"""Violin regeneration must retain the complete native rifle partition chain."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import GenerateViolinBowContact as violin


def compiled(parent):
    data = bytearray(512)
    struct.pack_into('<I', data, 84, 232)
    struct.pack_into('<f', data, 176, 1)
    data[180:244] = parent.encode('ascii').ljust(64, b'\0')
    struct.pack_into('<i', data, 272, -1)
    data[276:308] = b'root'.ljust(32, b'\0')
    data[-32:] = bytes(range(32))
    return bytes(data)


class ViolinRiflePartitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'sw_cr_creature'
        self.directory.mkdir()
        self.body = 'pfe0'
        self.head = 'a_rfl_' + self.body
        self.violin = violin.bank_name(self.body)
        self.invalidated = []
        class Library:
            def data(inner, name):
                path = self.directory / (name + '.mdl')
                return path.read_bytes() if path.is_file() else None
            def invalidate(inner, name):
                self.invalidated.append(name)
        self.library = Library()
        self.write(self.head, self.head + '_b01')
        self.write(self.head + '_b01', self.head + '_b02')
        self.write(self.head + '_b02', self.violin)
        self.write(self.violin, 'a_ba_non_combat')

    def write(self, name, parent):
        (self.directory / (name + '.mdl')).write_bytes(compiled(parent))

    def test_regeneration_resolves_beyond_every_rifle_part_and_prior_violin_fit(self):
        self.assertEqual(violin.original_parent(self.library, self.body), 'a_ba_non_combat')

    def test_finalization_changes_only_the_tail_parent(self):
        self.write(self.head + '_b02', 'a_ba_non_combat')
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        with patch.object(violin, 'ROOT', self.root):
            self.assertEqual(violin.splice_violin(self.library, self.body), self.head + '_b02')
        for path in self.directory.iterdir():
            expected = bytearray(before[path.name])
            if path.stem == self.head + '_b02':
                expected[180:244] = self.violin.encode('ascii').ljust(64, b'\0')
            self.assertEqual(path.read_bytes(), bytes(expected), path.name)
        self.assertEqual(self.invalidated, [self.head + '_b02'])
        self.assertEqual(violin.original_parent(self.library, self.body), 'a_ba_non_combat')

    def test_repeated_finalization_is_byte_identical(self):
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        with patch.object(violin, 'ROOT', self.root):
            violin.splice_violin(self.library, self.body)
            violin.splice_violin(self.library, self.body)
        self.assertEqual({path.name: path.read_bytes() for path in self.directory.iterdir()}, before)

    def test_flat_bank_remains_supported(self):
        self.write(self.head, 'a_ba_non_combat')
        with patch.object(violin, 'ROOT', self.root):
            self.assertEqual(violin.splice_violin(self.library, self.body), self.head)
        self.assertEqual(violin.original_parent(self.library, self.body), 'a_ba_non_combat')

    def test_cyclic_rifle_parts_are_rejected(self):
        self.write(self.head + '_b02', self.head)
        with self.assertRaisesRegex(ValueError, 'Cyclic owned rifle'):
            violin.original_parent(self.library, self.body)

    def test_prior_violin_fit_cannot_inherit_its_own_rifle_partition(self):
        self.write(self.violin, self.head + '_b01')
        with self.assertRaisesRegex(ValueError, 'Cyclic violin/rifle'):
            violin.original_parent(self.library, self.body)

    def test_missing_rifle_partition_is_rejected(self):
        self.write(self.head + '_b02', self.head + '_b03')
        with self.assertRaisesRegex(ValueError, 'compiled rifle bank partition'):
            violin.original_parent(self.library, self.body)


if __name__ == '__main__':
    unittest.main()
