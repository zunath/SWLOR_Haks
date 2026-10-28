"""Regression coverage for the lossless DXT1 horizontal flip."""
import struct
import unittest

import FlipDdsHorizontal as flip


def header(width, height, mips):
    data = bytearray(128)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<II", data, 12, height, width)
    struct.pack_into("<I", data, 28, mips)
    data[84:88] = b"DXT1"
    return data


def block(index_rows):
    out = bytearray(b"\x01\x02\x03\x04")
    for row in index_rows:
        out.append(sum(pixel << (2 * column) for column, pixel in enumerate(row)))
    return bytes(out)


class FlipDdsHorizontalTests(unittest.TestCase):
    def test_blocks_and_pixels_reverse_and_flip_twice_is_identity(self):
        left = block([[0, 1, 2, 3]] * 4)
        right = block([[3, 3, 0, 0]] * 4)
        source = bytes(header(8, 4, 1)) + left + right
        flipped = flip.flip_dds(source)
        self.assertEqual(flipped[128:136], block([[0, 0, 3, 3]] * 4))
        self.assertEqual(flipped[136:144], block([[3, 2, 1, 0]] * 4))
        self.assertEqual(flipped[:128], source[:128])
        self.assertEqual(flip.flip_dds(flipped), source)

    def test_narrow_mip_mirrors_only_used_columns(self):
        source = bytes(header(2, 2, 1)) + block([[0, 1, 2, 3]] * 4)
        flipped = flip.flip_dds(source)
        self.assertEqual(flipped[128:], block([[1, 0, 2, 3]] * 4))

    def test_every_mip_level_is_flipped_and_size_is_kept(self):
        mips = [block([[0, 1, 2, 3]] * 4)] * 4 + [block([[0, 1, 2, 3]] * 4)]
        source = bytes(header(8, 8, 2)) + b"".join(mips[:4]) + mips[4]
        flipped = flip.flip_dds(source)
        self.assertEqual(len(flipped), len(source))
        self.assertEqual(flip.flip_dds(flipped), source)

    def test_other_formats_are_rejected(self):
        data = bytearray(header(4, 4, 1)) + bytes(16)
        data[84:88] = b"DXT5"
        with self.assertRaises(ValueError):
            flip.flip_dds(bytes(data))


if __name__ == "__main__":
    unittest.main()
