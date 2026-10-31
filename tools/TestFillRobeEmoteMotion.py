"""Robe cloth must follow the wearer in SWLOR's emote clips instead of latching rigid."""
import json
import math
import unittest

import FillRobeEmoteMotion as emote
import GenerateRobeRgbModels as generator
import RobeAnimationBanks as banks

# Kneel, crouch and sit-legs-spread: the reported emotes and the lowest poses.
LOW_EMOTES = ("custom11lp", "custom7lp", "custom39lp")


class FillRobeEmoteMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = json.loads((generator.ROOT / "tools" / "RobeRgbModels.json").read_text())
        cls.manifest = manifest
        active = generator.fresh_active_models()
        cls.bridges = {head: generator.owned_animation_bytes(head, manifest, active)
                       for head in sorted(manifest["animation_bank_sets"]) if emote.PLAYABLE.fullmatch(head)}

    def bridge(self, family):
        """The head of a family's bridge; regenerating the bridges allocates new version numbers."""
        return next(head for head in sorted(self.bridges) if head.startswith(family + "_"))

    def test_every_playable_body_family_has_a_bridge(self):
        self.assertEqual({head[:3] for head in self.bridges},
                         {f"p{gender}{family}" for gender in "fm" for family in "heod"})

    def test_build_cache_records_describe_the_filled_bridges(self):
        """A regeneration must still reuse the installed bridges instead of recompiling them."""
        for head in self.bridges:
            with self.subTest(bridge=head):
                self.assertEqual(self.manifest["model_builds"][head]["binarySha256"],
                                 self.manifest["animation_bank_sets"][head]["source_sha256"])

    def test_every_emote_clip_animates_every_cloth_helper(self):
        for head, data in self.bridges.items():
            with self.subTest(bridge=head):
                rig = emote.Rig(data)
                self.assertTrue(rig.cloth, "every playable robe bridge carries cloth helpers")
                parts = {rig.model.nodes[index][2] for index in rig.cloth}
                emotes = [name for name in rig.model.clips if emote.EMOTE.fullmatch(name)]
                self.assertGreater(len(emotes), 100)
                for name in emotes:
                    tracked = {part for _, _, part, controls, _ in rig.model.clips[name][1] if controls}
                    if parts & tracked:
                        self.assertLessEqual(parts, tracked, name)
                self.assertEqual([], emote.missing_motion(data))
                self.assertEqual((data, []), emote.fill(data))

    def test_low_emotes_drape_cloth_above_the_floor_and_beside_the_legs(self):
        for head in (self.bridge("pmh"), self.bridge("pfh")):
            rig = emote.Rig(self.bridges[head])
            for clip in LOW_EMOTES:
                length = rig.model.clips[clip][0]
                for phase in (0, .5, 1):
                    pose = rig.model.pose(rig.model.clips[clip], phase * length)
                    hips = pose["pelvis_g"][0]
                    for index in rig.cloth:
                        name = rig.keys[index]
                        with self.subTest(bridge=head, clip=clip, phase=phase, helper=name):
                            position = pose[name][0]
                            self.assertGreater(position[2], -.05)
                            self.assertLess(math.dist(position, hips), 1.4)
                            # A rigid latched coat leaves its knee-height joints hanging
                            # far below the hips; a lowered wearer's cloth reaches the floor.
                            if rig.roles[index][0] == "chain" and rig.roles[index][2] > .5:
                                self.assertLess(position[2], hips[2])

    def test_unchanged_clips_encode_byte_for_byte(self):
        model = banks.CompiledBridge(self.bridges[self.bridge("pmh")])
        for name, component in zip(model.clip_names, model.clips):
            rebuilt = emote.write_clip(*emote.read_clip(component), model.name)
            self.assertEqual(component.relocated(0, model.name), rebuilt.relocated(0, model.name), name)

    def test_drape_turns_cloth_toward_the_raised_knee_only_on_its_side(self):
        rig = emote.Rig(self.bridges[self.bridge("pmh")])
        length = rig.model.clips["custom11lp"][0]
        pose = rig.model.pose(rig.model.clips["custom11lp"], length / 2)
        raised = max("lr", key=lambda side: pose[f"{side}shin_g"][0][2])
        lowered = "l" if raised == "r" else "r"
        knee = pose[f"{raised}shin_g"][0]
        front = {side: pose[f"rg_coat_f{side}2"][0] for side in "lr"}
        back = {side: pose[f"rg_coat_b{side}2"][0] for side in "lr"}
        # Front cloth on the raised side lies out over that knee; the back cloth hangs behind.
        self.assertGreater(front[raised][1], front[lowered][1] + .2)
        self.assertLess(math.dist(front[raised][:2], knee[:2]), .25)
        for side in "lr":
            self.assertLess(back[side][1], pose["pelvis_g"][0][1])


if __name__ == "__main__":
    unittest.main()
