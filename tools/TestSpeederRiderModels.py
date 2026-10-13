"""Speeder riding phenotypes keep every robe and body type animated with the rider."""
import json
from functools import lru_cache
import re
import struct
import unittest

import CompileModels as mdl
import GenerateSpeederRiderModels as rider
import GenerateTintMapAssets as tint
import RobeAnimations as anim


def geometry_rotations(data):
    """Bind orientation quaternion (x, y, z, w) of every geometry node."""
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    result = {}
    def visit(pointer):
        node = 12 + pointer
        name = rider.fixed(data, node + 32, 32)
        result[name] = (0.0, 0.0, 0.0, 1.0)
        keys, count, values = uint(node + 84), uint(node + 88), uint(node + 96)
        for index in range(count):
            kind, _, _, start, _ = struct.unpack_from("<IHHHB", data, 12 + keys + index * 12)
            if kind == 20:
                result[name] = struct.unpack_from("<4f", data, 12 + values + start * 4)
        for index in range(uint(node + 76)):
            visit(uint(12 + uint(node + 72) + index * 4))
    visit(uint(84))
    return result


def clips(data):
    """{clip: {node: set of controller kinds}} for a compiled model's own animations."""
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    result = {}
    for index in range(uint(136)):
        animation = 12 + uint(12 + uint(132) + index * 4)
        nodes = {}
        def visit(pointer):
            node = 12 + pointer
            keys, count = uint(node + 84), uint(node + 88)
            nodes[rider.fixed(data, node + 32, 32)] = {
                struct.unpack_from("<I", data, 12 + keys + item * 12)[0] for item in range(count)}
            for child in range(uint(node + 76)):
                visit(uint(12 + uint(node + 72) + child * 4))
        visit(uint(animation + 72))
        result[rider.fixed(data, animation + 8, 64)] = nodes
    return result


class SpeederRiderModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.active = tint.find_active_models()
        cls.load = staticmethod(lru_cache(None)(lambda name: cls.active[name].read_bytes() if name in cls.active else None))

    def read(self, name):
        self.assertIn(name, self.active, f"{name} is missing; run tools/GenerateSpeederRiderModels.py --apply")
        return self.active[name].read_bytes()

    def test_banks_replace_mounted_clips_with_rotation_only_poses(self):
        for parent, name in rider.BANKS.items():
            data = self.read(name)
            self.assertEqual(mdl.supermodel(data), parent)
            anim.validate_animation_parts(data)
            if parent in self.active and mdl.binary(self.active[parent].read_bytes()):
                self.assertEqual(anim.binary_parts(data), anim.binary_parts(self.active[parent].read_bytes()),
                                 f"{name}: animation part IDs differ from {parent}")
            own = clips(data)
            self.assertTrue({"pause1", "walk", "run"} <= set(own), name)
            for clip, nodes in own.items():
                for node, kinds in nodes.items():
                    # 8 = position, 20 = orientation. Bones may only rotate; the root carries the seat height.
                    allowed = {8, 20} if node == "rootdummy" else {20}
                    self.assertLessEqual(kinds, allowed, f"{name}/{clip}/{node}")

    def test_every_body_root_has_a_level_riding_root(self):
        bodies = [name for name in self.active if rider.BODY.match(name)]
        self.assertTrue(bodies)
        for base in bodies:
            name = rider.riding_name(base)
            data, original = self.read(name), self.active[base].read_bytes()
            self.assertTrue(mdl.binary(data), name)
            self.assertEqual(mdl.supermodel(data), rider.BANKS[mdl.supermodel(original)])
            anim.validate_animation_parts(data)
            actual = anim.binary_parts(data)
            load = lambda model: self.active[model].read_bytes() if model in self.active else None
            for path, part in rider.inherited_parts(mdl.supermodel(data), load).items():
                if path in actual:
                    self.assertEqual(actual[path], part, f"{name}/{'/'.join(path)}: inherited clips cannot reach this joint")
            # A vehicle must remain level while the pelvis bends. Its attachment is
            # rooted outside the animated rider skeleton.
            model = __import__("RobePoseAudit").Model(data, False)
            tail = next(node for node in model.nodes if node[0] == "tail")
            self.assertEqual(tail[1], 0, name)
            self.assertEqual(geometry_rotations(data)["tail"], (0.0, 0.0, 0.0, 1.0), name)

    def test_every_robe_has_a_riding_copy_bound_to_the_riding_chain(self):
        robes = [name for name in self.active if rider.ROBE.match(name)]
        self.assertGreater(len(robes), 1000)
        for base in robes:
            original = self.active[base].read_bytes()
            name, expected = rider.robe_copy(base, original)
            if mdl.binary(original):
                self.assertEqual(self.read(name), rider.match_inherited_parts(expected, self.load), f"{name} is stale; run tools/GenerateSpeederRiderModels.py --apply")
            else:
                self.assertTrue(mdl.binary(self.read(name)), f"{name}: ASCII robes must be compiled before tint binding")
                self.assertEqual(mdl.supermodel(self.read(name)), mdl.supermodel(expected), name)
            parent, base_parent = mdl.supermodel(expected), mdl.supermodel(original)
            if parent != base_parent:
                self.assertIn(parent, self.active, f"{name}: supermodel {parent} is missing")
            elif base_parent:
                self.assertNotIn(base_parent, self.active, f"{name}: {base_parent} has no riding replacement")
            if mdl.binary(expected):
                self.assertEqual(len(expected), len(original))
                self.assertFalse(set(clips(expected)) & set(rider.CLIPS), f"{name}: private clip overrides the seated pose")

    def test_no_riding_robe_is_left_without_a_base(self):
        riding = re.compile(rf"^(p[mf][a-z])({'|'.join(rider.PHENOTYPES.values())})(_robe\d+)$")
        bases = {value: key for key, value in rider.PHENOTYPES.items()}
        known = {*self.active, *json.loads(rider.STOCK_ROBES.read_text(encoding="utf8"))}
        for name in self.active:
            match = riding.match(name)
            if match:
                base = f"{match[1]}{bases[match[2]]}{match[3]}"
                self.assertIn(base, known, name)

    def test_mounted_robe_joints_match_their_animation_parents(self):
        @lru_cache(None)
        def inherited(name):
            return rider.inherited_parts(name, self.load)
        for name in self.active:
            if not re.fullmatch(r"p[fm][a-z]22_robe\d+", name):
                continue
            data = self.read(name)
            expected = inherited(mdl.supermodel(data))
            for path, part in anim.binary_parts(data).items():
                if path and part >= 0 and path in expected and expected[path] >= 0:
                    self.assertEqual(part, expected[path], f"{name}/{'/'.join(path)}")

    def test_mounted_bike_uses_the_existing_textured_model(self):
        from ModelLodAssets import rename_identity
        self.assertEqual(self.read("tail_spdr_rider"),
                         rename_identity(self.read("c_motojet"), "tail_spdr_rider"))

    def test_every_mounted_robe_preserves_source_tint_bindings(self):
        models = {name: path.read_bytes() for name, path in self.active.items()
                  if re.fullmatch(r"p[fm][a-z]22_robe\d+", name)}
        table = (rider.ROOT / "sw_2da" / "tintmap.2da").read_bytes()
        updated, count = rider.append_riding_tint_rows(table, models)
        self.assertEqual(count, 0, "Mounted robes are missing saved-color bindings; regenerate the rider models")
        self.assertEqual(updated, table)

    def test_tint_rows_append_without_changing_existing_ids_or_bytes(self):
        table = b"2DA V2.0\r\n\r\n MODEL MATERIAL LAYERS\r\n0 pmh0_robe003 pmh0_robe003 2,3,4,5,6\r\n"
        models = {"pmh22_robe003": self.read("pmh22_robe003")}
        updated, count = rider.append_riding_tint_rows(table, models)
        self.assertEqual(count, 1)
        self.assertTrue(updated.startswith(table))
        self.assertEqual(updated.splitlines()[-1].split(),
                         [b"1", b"pmh22_robe003", b"pmh0_robe003", b"2,3,4,5,6"])
        self.assertEqual(rider.append_riding_tint_rows(updated, models), (updated, 0))


if __name__ == "__main__":
    unittest.main()
