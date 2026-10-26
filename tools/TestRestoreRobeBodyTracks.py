"""Belts and shoulder pads on robe phenotypes must follow the wearer like on the plain body.

Needs the installed game's data directory for stock body animations: set NWN_GAME_DATA,
or install the game in the default Steam location.
"""
import json
import os
from pathlib import Path
import unittest

import CompileModels as mdl
import GenerateRobeRgbModels as generator
import RestoreRobeBodyTracks as body_tracks
import RobePoseAudit as poses

GAME_DATA = Path(os.environ.get(
    "NWN_GAME_DATA", r"C:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights\data"))
# Idle, walk and run: the reported standing pose and the most common motion.
CLIPS = ("pause1", "walk", "run")


def pose(model, clip, time):
    """World transforms with the engine's mapping: numbered joints by part ID, -1 joints by name."""
    numbered = {part: controls for _, _, part, controls, _ in clip[1] if part >= 0}
    named = {name: controls for name, _, part, controls, _ in clip[1] if part < 0 and controls}
    result, world = {}, []
    for name, parent, part, defaults, _ in model.nodes:
        controls = {**defaults, **(numbered.get(part, {}) if part >= 0 else named.get(name, {}))}
        p = poses.sample(controls[8], time) if 8 in controls else (0, 0, 0)
        q = poses.sample(controls[20], time, True) if 20 in controls else (0, 0, 0, 1)
        if parent is not None:
            pp, pq = world[parent]
            p = tuple(a + b for a, b in zip(pp, poses.rotate(pq, p)))
            q = poses.multiply(pq, q)
        world.append((p, q))
        result.setdefault(name, (p, q))
    return result


class RestoreRobeBodyTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (GAME_DATA / "nwn_base.key").is_file():
            raise AssertionError(f"Set NWN_GAME_DATA to the game's data directory (missing {GAME_DATA})")
        cls.manifest = json.loads((generator.ROOT / "tools" / "RobeRgbModels.json").read_text())
        cls.active = generator.fresh_active_models()
        cls.library = body_tracks.library_for(cls.active, GAME_DATA)
        cls.bridges = {head: generator.owned_animation_bytes(head, cls.manifest, cls.active)
                       for head in sorted(cls.manifest["animation_bank_sets"])
                       if body_tracks.PLAYABLE.fullmatch(head)}
        # One robe phenotype root per playable body family.
        cls.wearers = {}
        rows = (generator.ROOT / "sw_2da" / "roberender.2da").read_text().splitlines()[3:]
        for row in (line.split() for line in rows if line.strip()):
            prefix = row[1][:3]
            if body_tracks.PLAYABLE.fullmatch(prefix + "_ra000"):
                cls.wearers.setdefault(prefix, f"{prefix}{row[2]}")

    def test_every_playable_body_family_has_a_bridge_and_a_wearer(self):
        families = {f"p{gender}{family}" for gender in "fm" for family in "heod"}
        self.assertEqual(families, {head[:3] for head in self.bridges})
        self.assertEqual(families, set(self.wearers))

    def test_body_clips_sway_belts_and_turn_female_shoulder_pads(self):
        for head, data in self.bridges.items():
            base = head[:3] + "0"
            with self.subTest(body=base):
                joints = body_tracks.moving_joints(self.library, base)
                self.assertIn("belt_g1", joints)
                if base in ("pfh0", "pfe0"):
                    self.assertTrue({"lshoulder_g", "rshoulder_g"} <= joints)

    def test_installed_bridges_carry_every_moving_body_attachment_track(self):
        for head, data in self.bridges.items():
            with self.subTest(bridge=head):
                self.assertEqual({}, body_tracks.missing_tracks(data, self.library))
                self.assertEqual((data, []), body_tracks.restore(data, self.library))

    def test_restored_tracks_are_the_body_clip_bytes(self):
        for head, data in self.bridges.items():
            base = head[:3] + "0"
            bridge = poses.Model(data)
            for clip in CLIPS:
                body = self.library.clips(base)[clip][1]
                source = self.library.model(self.library.owners(base)[clip])
                installed = {name: offset for name, _, _, _, offset in bridge.clips[clip][1]}
                for name, _, part, controls, offset in body:
                    if (part >= 0 or name not in body_tracks.moving_joints(self.library, base) or
                            not body_tracks.raw_controllers(source, offset)[0]):
                        continue
                    with self.subTest(bridge=head, clip=clip, joint=name):
                        self.assertIn(name, installed)
                        self.assertEqual(body_tracks.raw_controllers(source, offset),
                                         body_tracks.raw_controllers(bridge, installed[name]))

    def test_robe_wearers_turn_belts_and_shoulder_pads_with_the_plain_body(self):
        for prefix, wearer in sorted(self.wearers.items()):
            base = prefix + "0"
            body, robed = self.library.model(base), self.library.model(wearer)
            joints = ["belt_g"] + [joint for joint in ("lshoulder_g", "rshoulder_g")
                                   if joint in body_tracks.moving_joints(self.library, base)]
            for clip in CLIPS:
                expected, actual = self.library.clips(base)[clip], self.library.clips(wearer)[clip]
                for phase in (0, .25, .5, .75, 1):
                    before = pose(body, expected, phase * expected[0])
                    after = pose(robed, actual, phase * actual[0])
                    for joint in joints:
                        with self.subTest(wearer=wearer, clip=clip, phase=phase, joint=joint):
                            self.assertTrue(mdl.equivalent_quaternion(before[joint][1], after[joint][1]),
                                            (before[joint][1], after[joint][1]))

    def test_the_plain_body_belt_turns_with_the_hips_only_through_its_named_track(self):
        """Negative control: without the -1 track the belt stays square while the hips turn."""
        body = self.library.model("pfh0")
        clip = self.library.clips("pfh0")["pause1"]
        stripped = (clip[0], [node if node[0] != "belt_g1" else (*node[:3], {}, node[4]) for node in clip[1]])
        turned, square = pose(body, clip, 0), pose(body, stripped, 0)
        self.assertTrue(mdl.equivalent_quaternion(turned["belt_g"][1], turned["pelvis_g"][1]))
        self.assertFalse(mdl.equivalent_quaternion(square["belt_g"][1], turned["pelvis_g"][1]))


if __name__ == "__main__":
    unittest.main()
