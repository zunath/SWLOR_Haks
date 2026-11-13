"""Faction recolors of one ship hull must light the same engine thrusters."""
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
VEHICLES = ROOT / "sw_cr_vehicle"
EMITTER_FLAG = 0x04
POSITION_CONTROLLER = 8

# Republic, Sith and Neutral models that share one hull and differ only in textures.
SHARED_HULL_FAMILIES = {
    "foray": ("c_rep_foray", "c_sith_foray", "c_n_foray"),
    "hammerhead": ("c_rep_hhead", "c_sith_hhead", "c_n_hhead"),
    "paladin": ("c_rep_palad", "c_sith_palad"),
    "praetorian": ("c_rep_preat", "c_sith_preat", "c_n_preat"),
    "quarterstaff": ("c_rep_quarter", "c_sith_quarter", "c_n_quarter"),
    "thranta": ("c_rep_thranta", "c_sith_thranta", "c_n_thranta"),
}


def emitter_positions(data: bytes) -> list[tuple[float, float, float]]:
    """Read every geometry emitter's static position from a compiled MDL."""
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    if uint(0) != 0:
        raise ValueError("expected a compiled (binary) MDL")
    positions = []

    def walk(pointer):
        node = 12 + pointer
        if uint(node + 108) & EMITTER_FLAG:
            keys, count, values = uint(node + 84), uint(node + 88), uint(node + 96)
            position = (0.0, 0.0, 0.0)
            for index in range(count):
                kind, _, _, start, _ = struct.unpack_from("<IHHHB", data, 12 + keys + index * 12)
                if kind == POSITION_CONTROLLER:
                    position = struct.unpack_from("<3f", data, 12 + values + start * 4)
            positions.append(tuple(round(value, 3) for value in position))
        children, count = uint(node + 72), uint(node + 76)
        for index in range(count):
            walk(uint(12 + children + index * 4))

    walk(uint(84))
    return sorted(positions)


class ShipVariantEmitterTests(unittest.TestCase):
    def test_reader_finds_every_thranta_thruster(self):
        positions = emitter_positions((VEHICLES / "c_rep_thranta.mdl").read_bytes())
        self.assertEqual(10, len(positions))
        self.assertIn((0.202, -1.102, 0.218), positions)

    def test_shared_hull_variants_light_the_same_thrusters(self):
        for family, models in SHARED_HULL_FAMILIES.items():
            reference = emitter_positions((VEHICLES / f"{models[0]}.mdl").read_bytes())
            self.assertTrue(reference, f"{models[0]} has no thruster emitters")
            for model in models[1:]:
                with self.subTest(family=family, model=model):
                    self.assertEqual(reference, emitter_positions((VEHICLES / f"{model}.mdl").read_bytes()))


if __name__ == "__main__":
    unittest.main()
