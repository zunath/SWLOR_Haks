"""The carry overlay must never mask a joint or change existing model data."""
import struct
import unittest
from pathlib import Path

import EnsureWeaponCarryOverlay as carry


class WeaponCarryOverlayTests(unittest.TestCase):
    def test_installed_overlay_is_compiled_controller_free_and_idempotent(self):
        path = Path(__file__).resolve().parents[1]/"sw_cr_creature/a_ba_casts.mdl"
        data = path.read_bytes()
        model, header, node = carry.validate_overlay(data)
        self.assertEqual(model.clips[carry.NAME][0], 1)
        self.assertEqual(carry.append_overlay(data, b"not needed"), data)
        for relative in (76, 88, 100):
            corrupt = bytearray(data)
            struct.pack_into("<I", corrupt, node+relative, 1)
            with self.subTest(offset=relative), self.assertRaises((ValueError, struct.error)):
                carry.validate_overlay(bytes(corrupt))

    def test_append_preserves_old_model_sections_and_rejects_unrelated_mutations(self):
        path = Path(__file__).resolve().parents[1]/"sw_cr_creature/a_ba_casts.mdl"
        installed = path.read_bytes()
        _, header, _ = carry.validate_overlay(installed)
        original = bytearray(installed)
        original[header+8:header+72] = b"empty_old".ljust(64, b"\0")
        # The installed animation header/root are the compiler-produced donor.
        # Isolate the donor's array to avoid copying its unrelated animations.
        donor = bytearray(installed)
        struct.pack_into("<III", donor, 132, header-16, 1, 1)
        struct.pack_into("<I", donor, header-4, header-12)
        after = carry.append_overlay(bytes(original), bytes(donor))
        carry.validate_preservation(bytes(original), after)
        broken = bytearray(after)
        broken[160] ^= 1
        with self.assertRaises(ValueError):
            carry.validate_preservation(bytes(original), bytes(broken))


if __name__ == "__main__":
    unittest.main()
