#!/usr/bin/env python3
"""Regression coverage for chest154 tint atlas TXI checks on Windows."""

import tempfile
import unittest
from pathlib import Path

import RebuildChest154TintAtlas as atlas


class Chest154AtlasTxiTests(unittest.TestCase):
    def test_lf_and_windows_crlf_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            txi = Path(directory) / "chest154.txi"
            for contents in (b"mipmap 1\n", b"mipmap 1\r\n"):
                with self.subTest(contents=contents):
                    txi.write_bytes(contents)
                    self.assertTrue(atlas.has_expected_txi(txi))

    def test_invalid_mipmap_policy_and_extra_lines_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            txi = Path(directory) / "chest154.txi"
            for contents in (b"mipmap 0\n", b"mipmap 1", b"mipmap 1\nother 2\n", b""):
                with self.subTest(contents=contents):
                    txi.write_bytes(contents)
                    self.assertFalse(atlas.has_expected_txi(txi))

    def test_missing_txi_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(atlas.has_expected_txi(Path(directory) / "missing.txi"))


if __name__ == "__main__":
    unittest.main()
