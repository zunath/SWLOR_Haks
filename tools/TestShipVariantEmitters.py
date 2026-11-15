"""Faction recolors of one ship hull must light the same engine thrusters, with matching glows."""
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
VEHICLES = ROOT / "sw_cr_vehicle"
EMITTER_FLAG = 0x04
POSITION_CONTROLLER = 8
SIZE_START_CONTROLLER = 168
SIZE_END_CONTROLLER = 172
# Emitters this close in depth and height are a left/right pair of identical thrusters.
PAIR_TOLERANCE = 0.01

# Republic, Sith and Neutral models that share one hull and differ only in textures.
SHARED_HULL_FAMILIES = {
    "foray": ("c_rep_foray", "c_sith_foray", "c_n_foray"),
    "hammerhead": ("c_rep_hhead", "c_sith_hhead", "c_n_hhead"),
    "paladin": ("c_rep_palad", "c_sith_palad"),
    "praetorian": ("c_rep_preat", "c_sith_preat", "c_n_preat"),
    "quarterstaff": ("c_rep_quarter", "c_sith_quarter", "c_n_quarter"),
    "thranta": ("c_rep_thranta", "c_sith_thranta", "c_n_thranta"),
}


def emitters(data: bytes) -> list[tuple[tuple[float, float, float], float, float]]:
    """Read every geometry emitter's static position and glow size (sizeStart, sizeEnd) from a compiled MDL."""
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    if uint(0) != 0:
        raise ValueError("expected a compiled (binary) MDL")
    found = []

    def walk(pointer):
        node = 12 + pointer
        if uint(node + 108) & EMITTER_FLAG:
            keys, count, values = uint(node + 84), uint(node + 88), uint(node + 96)
            position, size_start, size_end = (0.0, 0.0, 0.0), 0.0, 0.0
            for index in range(count):
                kind, _, _, start, _ = struct.unpack_from("<IHHHB", data, 12 + keys + index * 12)
                if kind == POSITION_CONTROLLER:
                    position = struct.unpack_from("<3f", data, 12 + values + start * 4)
                elif kind == SIZE_START_CONTROLLER:
                    size_start = struct.unpack_from("<f", data, 12 + values + start * 4)[0]
                elif kind == SIZE_END_CONTROLLER:
                    size_end = struct.unpack_from("<f", data, 12 + values + start * 4)[0]
            found.append((tuple(round(value, 3) for value in position), round(size_start, 3), round(size_end, 3)))
        children, count = uint(node + 72), uint(node + 76)
        for index in range(count):
            walk(uint(12 + children + index * 4))

    walk(uint(84))
    return sorted(found)


def model_emitters(model):
    return emitters((VEHICLES / f"{model}.mdl").read_bytes())


class ShipVariantEmitterTests(unittest.TestCase):
    def test_reader_finds_every_thranta_thruster(self):
        found = model_emitters("c_rep_thranta")
        self.assertEqual(10, len(found))
        self.assertIn(((0.202, -1.102, 0.218), 0.15, 0.1), found)
        self.assertIn(((-0.056, -0.94, 0.339), 0.25, 0.17), found)

    def test_shared_hull_variants_light_the_same_thrusters(self):
        for family, models in SHARED_HULL_FAMILIES.items():
            reference = model_emitters(models[0])
            self.assertTrue(reference, f"{models[0]} has no thruster emitters")
            for model in models[1:]:
                with self.subTest(family=family, model=model):
                    self.assertEqual(reference, model_emitters(model))

    def test_mirrored_thrusters_share_glow_size(self):
        for models in SHARED_HULL_FAMILIES.values():
            for model in models:
                found = model_emitters(model)
                for index, (left, left_start, left_end) in enumerate(found):
                    for right, right_start, right_end in found[index + 1:]:
                        if abs(left[1] - right[1]) > PAIR_TOLERANCE or abs(left[2] - right[2]) > PAIR_TOLERANCE:
                            continue
                        with self.subTest(model=model, left=left, right=right):
                            self.assertEqual((left_start, left_end), (right_start, right_end))


if __name__ == "__main__":
    unittest.main()
