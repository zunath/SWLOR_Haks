"""Speeder riding phenotypes keep every robe and body type animated with the rider."""
import json
import math
import re
import struct
import unittest

import CompileModels as mdl
import GenerateSpeederRiderModels as rider
import GenerateTintMapAssets as tint
import RobeAnimations as anim


def rotation(axis_angle):
    x, y, z, angle = axis_angle
    length = math.sqrt(x * x + y * y + z * z)
    if not length:
        return (0.0, 0.0, 0.0, 1.0)
    scale = math.sin(angle / 2) / length
    return (x * scale, y * scale, z * scale, math.cos(angle / 2))


def compose(left, right):
    ax, ay, az, aw = left
    bx, by, bz, bw = right
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


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
            pelvis = rotation(rider.POSE["female" if name[1] == "f" else "male"]["bones"]["pelvis_g"])
            level = compose(pelvis, geometry_rotations(data)["tail"])
            self.assertAlmostEqual(abs(level[3]), 1.0, places=5, msg=f"{name}: bike attachment is not level when seated")

    def test_every_robe_has_a_riding_copy_bound_to_the_riding_chain(self):
        robes = [name for name in self.active if rider.ROBE.match(name)]
        self.assertGreater(len(robes), 2000)
        for base in robes:
            original = self.active[base].read_bytes()
            name, expected = rider.robe_copy(base, original)
            if mdl.binary(original):
                self.assertEqual(self.read(name), expected, f"{name} is stale; run tools/GenerateSpeederRiderModels.py --apply")
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
                self.assertIn(f"{match[1]}{bases[match[2]]}{match[3]}", known, name)


if __name__ == "__main__":
    unittest.main()
