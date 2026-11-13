"""Resample authored head shades without wrapping Lanczos overshoot into BC5."""
import struct

import numpy as np
from PIL import Image

import GenerateTintMapAssets as tint


def resize_shades(shades, size):
    source = np.asarray(shades, dtype=np.float32)
    if source.ndim != 2 or not np.isfinite(source).all():
        raise ValueError('Head shades must be a finite two-dimensional plane')
    if len(size) != 2 or any(side < 1 or side > 16384 for side in size):
        raise ValueError('Invalid head shade dimensions')
    resized = np.asarray(Image.fromarray(source).resize(size, Image.Resampling.LANCZOS))
    return np.clip(np.rint(resized), 0, 255).astype(np.uint8)


def replace_shades(data, shades):
    if data[:4] != b'DDS ' or data[84:88] != b'ATI2':
        raise ValueError('Expected a BC5 head tint map')
    height, width = struct.unpack_from('<II', data, 12)
    blocks = ((width + 3)//4) * ((height + 3)//4)
    if len(data) != 128 + blocks*16 or struct.unpack_from('<I', data, 28)[0] > 1:
        raise ValueError('Head tint maps require a single complete mip level')
    shades = np.asarray(shades)
    if shades.dtype != np.uint8 or shades.shape != (height, width):
        raise ValueError('Clamp head shades to uint8 at the recorded dimensions before encoding')
    payload = np.frombuffer(data, np.uint8, offset=128).reshape(blocks, 16).copy()
    payload[:, :8] = np.frombuffer(tint.compress_bc4(shades), np.uint8).reshape(blocks, 8)
    # Preserve the encoded categorical plane byte for byte, including its
    # endpoint choices. Eye, lip and accessory channel ownership cannot drift.
    return data[:128] + payload.tobytes()
