"""Keep the short male coat cuff attached through bent-elbow combat poses."""
from functools import lru_cache
import math
from pathlib import Path
import struct
import unittest

import CompileModels as mdl
import GenerateTintMapAssets as tint
from RobePoseAudit import Model, rotate


class RobeCuffTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.active = tint.find_active_models()

    def test_short_male_coat_sleeves_follow_forearms_and_blend_at_elbows(self):
        data = (Path(__file__).resolve().parents[1] / "sw_pt_robe/pmd0_robe236.mdl").read_bytes()
        meshes = {name: mesh for name, _, mesh in mdl.binary_nodes(data) if mesh is not None}
        for side in "lr":
            mesh = meshes["arm_" + side]
            # This authored sleeve has 27 distinct distal vertices, with UV-seam
            # duplicates in the native mesh. Its elbow transition is above -0.09.
            cuff = [i for i, vertex in enumerate(mesh["verts"]) if vertex[2] < -0.09]
            self.assertEqual(27, len({tuple(mesh["verts"][i]) for i in cuff}))
            for index in cuff:
                self.assertEqual([side + "forearm_g", 1.0], mesh["weights"][index], (side, index))
            elbow = [i for i, vertex in enumerate(mesh["verts"]) if -0.03 < vertex[2] < 0.07]
            self.assertEqual(18, len({tuple(mesh["verts"][i]) for i in elbow}))
            for index in elbow:
                weights = mesh["weights"][index]
                self.assertEqual({side + "forearm_g": 0.5, side + "bicep_g": 0.5},
                                 dict(zip(weights[::2], weights[1::2])), (side, index))

    def test_generated_cuff_stays_with_the_wrist_through_unarmed_and_stock_combat(self):
        root = "pmd200"
        phases = ("", "_in", "_out")
        moves = ("sw_jab", "sw_palmstr", "sw_fincombo", "sw_flowst", "sw_perfform",
                 "sw_dirtyjab", "sw_uppercut", "sw_lowkick", "sw_bruisst", "sw_haymaker")
        wanted = {move + phase for move in moves for phase in phases}
        wanted.update(("pause1", "nwslashr", "nwslasho", "nwstab"))

        @lru_cache(None)
        def load(name):
            data = self.active[name].read_bytes()
            model = Model(data, False)
            clips = {}
            for index in range(model.uint(136)):
                offset = 12 + model.uint(12 + model.uint(132) + index * 4)
                clip = data[offset + 8:offset + 72].split(b"\0", 1)[0].decode().lower()
                if clip in wanted:
                    clips[clip] = (struct.unpack_from("<f", data, offset + 112)[0],
                                   model.read_nodes(model.uint(offset + 72)))
            return model, clips

        wearer, _ = load(root)
        clips, current = {}, root
        while current:
            model, local = load(current)
            for name, clip in local.items():
                clips.setdefault(name, clip)
            current = model.parent
        self.assertEqual(wanted, clips.keys())
        meshes = {name: mesh for name, _, mesh in mdl.binary_nodes(wearer.data) if mesh is not None}
        mesh = meshes["rm_arm_r"]
        bindings = wearer.skin_bindings()["rm_arm_r"]
        cuff = [i for i, vertex in enumerate(mesh["verts"]) if vertex[2] < -0.09]
        self.assertEqual(27, len({tuple(mesh["verts"][i]) for i in cuff}))
        distances = {index: [] for index in cuff}
        for name, clip in clips.items():
            for phase in (0, 0.25, 0.5, 0.75, 1):
                pose = wearer.pose(clip, phase * clip[0], wearer.scale)
                for index in cuff:
                    actual = [0.0, 0.0, 0.0]
                    weights = mesh["weights"][index]
                    for bone, weight in zip(weights[::2], weights[1::2]):
                        qoffset, toffset = bindings[bone]
                        w, x, y, z = struct.unpack_from("<4f", wearer.data, qoffset)
                        translation = struct.unpack_from("<3f", wearer.data, toffset)
                        local = [a + b for a, b in zip(rotate((x, y, z, w), mesh["verts"][index]), translation)]
                        position, rotation, scale = pose[bone]
                        transformed = [a + b for a, b in zip(rotate(rotation, [v * scale for v in local]), position)]
                        actual = [a + weight * b for a, b in zip(actual, transformed)]
                    distances[index].append(math.dist(actual, pose["rhand_g"][0]))
        # A cuff bound to the upper arm can look connected at rest, then move
        # half a metre away when the elbow bends. Wrist-relative distances must
        # stay constant across all thirty phases and the older combat clips.
        for index, values in distances.items():
            self.assertLess(max(values) - min(values), 2e-5, index)
            self.assertLess(max(values), 0.25, index)


if __name__ == "__main__":
    unittest.main()
