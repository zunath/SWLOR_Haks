import tempfile
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

import GenerateTintMapAssets as tint
from HeadTintResampling import replace_shades, resize_shades


class HeadTintResamplingTests(unittest.TestCase):
    def test_lanczos_overshoot_does_not_turn_dark_edges_white(self):
        source = np.zeros((8, 8), np.float32)
        source[2:6, 2:6] = 255
        unsafe = np.asarray(Image.fromarray(source).resize((16, 16), Image.Resampling.LANCZOS))
        self.assertLess(unsafe.min(), -1)
        self.assertGreater(unsafe.max(), 255)
        safe = resize_shades(source, (16, 16))
        self.assertEqual(safe.dtype, np.uint8)
        self.assertEqual((int(safe.min()), int(safe.max())), (0, 255))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'mask.dds'
            layers = np.tile(np.array([0, 1, 8, 9], np.uint8), (16, 4))
            tint.write_packed_dds(path, 16, 16, unsafe, layers)
            original = path.read_bytes()
            bad = np.asarray(Image.open(path).convert('RGB'))[:, :, 0]
            self.assertGreater(int(((bad > 200) & (safe < 100)).sum()), 0)
            repaired = replace_shades(original, safe)
            path.write_bytes(repaired)
            actual = np.asarray(Image.open(path).convert('RGB'))[:, :, 0]
            self.assertEqual(int(((actual > 200) & (safe < 100)).sum()), 0)
            before = np.frombuffer(original, np.uint8, offset=128).reshape(-1, 16)
            after = np.frombuffer(repaired, np.uint8, offset=128).reshape(-1, 16)
            np.testing.assert_array_equal(before[:, 8:], after[:, 8:])
            np.testing.assert_array_equal(tint.decode_dds_layers(path, 16, 16), layers)

    def test_encoder_rejects_unclamped_or_malformed_planes(self):
        with self.assertRaisesRegex(ValueError, 'finite'):
            resize_shades(np.array([[float('nan')]]), (4, 4))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'mask.dds'
            tint.write_packed_dds(path, 4, 4, np.zeros((4, 4), np.uint8), np.zeros((4, 4), np.uint8))
            with self.assertRaisesRegex(ValueError, 'uint8'):
                replace_shades(path.read_bytes(), np.zeros((4, 4), np.float32))


if __name__ == '__main__':
    unittest.main()
