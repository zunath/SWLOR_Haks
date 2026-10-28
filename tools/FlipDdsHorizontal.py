#!/usr/bin/env python3
"""Mirror DXT1 DDS textures left to right without recompressing.

Every mip level is mirrored at the block level: the 4x4 blocks of each row are
reversed and the pixel indices inside each block are reversed, so the colour
endpoints (and therefore the image quality) stay byte-identical. Mip levels
narrower than four pixels mirror only their used columns. The header, mip
count and all other bytes are kept.

Usage: FlipDdsHorizontal.py <file.dds> [<file.dds> ...]
"""
import struct
import sys
from pathlib import Path


def mirror_row_byte(value, columns):
    """Reverse the first `columns` 2-bit pixels of one DXT1 index row byte."""
    pixels = [(value >> (2 * column)) & 3 for column in range(4)]
    pixels = pixels[:columns][::-1] + pixels[columns:]
    return sum(pixel << (2 * column) for column, pixel in enumerate(pixels))


def flip_dxt1_level(data, width, height):
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)
    columns = min(4, width)
    out = bytearray(len(data))
    for by in range(blocks_y):
        row = by * blocks_x
        for bx in range(blocks_x):
            source = (row + bx) * 8
            target = (row + (blocks_x - 1 - bx)) * 8
            block = bytearray(data[source:source + 8])
            for index in range(4, 8):
                block[index] = mirror_row_byte(block[index], columns)
            out[target:target + 8] = block
    return bytes(out)


def flip_dds(blob):
    if blob[:4] != b"DDS " or blob[84:88] != b"DXT1":
        raise ValueError("only DXT1 DDS files are supported")
    height, width = struct.unpack_from("<II", blob, 12)
    mips = max(1, struct.unpack_from("<I", blob, 28)[0])
    if struct.unpack_from("<I", blob, 112)[0] & 0x200:
        raise ValueError("cube maps are not supported")
    out = bytearray(blob[:128])
    offset = 128
    for _ in range(mips):
        size = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 8
        out += flip_dxt1_level(blob[offset:offset + size], width, height)
        offset += size
        width, height = max(1, width // 2), max(1, height // 2)
    if offset != len(blob):
        raise ValueError("unexpected trailing data")
    return bytes(out)


def main(paths):
    for path in map(Path, paths):
        path.write_bytes(flip_dds(path.read_bytes()))
        print(f"flipped {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
