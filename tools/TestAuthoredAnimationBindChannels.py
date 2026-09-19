"""Authored body and robe animations must retain each wearer's proportions."""
import json
import math
from pathlib import Path
import re
import unittest

from RobePoseAudit import Model


ROOT = Path(__file__).resolve().parents[1]


class AuthoredAnimationBindChannelTests(unittest.TestCase):
    def assert_wearer_bind_channels(self, paths):
        checked = 0
        for path in paths:
            model = Model(path.read_bytes())
            for clip, (_, nodes) in model.clips.items():
                if not clip.startswith("sw_"):
                    continue
                for name, _, _, controllers, _ in nodes:
                    if name not in ("neck_g", "head_g"):
                        continue
                    self.assertNotIn(8, controllers, f"{path.name}/{clip}/{name}: overrides wearer position")
                    self.assertNotIn(36, controllers, f"{path.name}/{clip}/{name}: overrides wearer scale")
                    checked += 1
        self.assertGreater(checked, 0, "The compiled animation corpus must be present")

    def test_body_banks_preserve_head_and_neck_proportions(self):
        sources = sorted((ROOT / "model_sources/sw_cr_creature").glob("*.mdl.ascii"))
        self.assert_wearer_bind_channels([
            ROOT / "sw_cr_creature" / source.name.removesuffix(".ascii") for source in sources
            if re.fullmatch(r"(?:an_a_[bf]a|ab_a_[bf]a_\d+)\.mdl\.ascii", source.name)
        ])

    def test_robe_banks_preserve_head_and_neck_proportions(self):
        manifest = json.loads((ROOT / "tools/RobeRgbModels.json").read_text())
        paths = [ROOT / ("sw_anim_f" if head[1] == "f" else "sw_anim_m") / f"{part}.mdl"
                 for head, bank in manifest["animation_bank_sets"].items() for part in bank["parts"]]
        self.assert_wearer_bind_channels(paths)

    def assert_skeleton_proportions(self, wearer, clips, label):
        # Measure the actual compiled skeleton after animation inheritance. Rotating
        # limbs and moving the root may crouch the body, but must not shorten bones.
        bones = [(name, wearer.nodes[parent][0]) for name, parent, _, _, _ in wearer.nodes
                 if re.fullmatch(r"(?:torso|pelvis|neck|head|[lr](?:bicep|forearm|hand|thigh|shin|foot))_g", name)]
        self.assertEqual(16, len(bones), label)
        bind = wearer.pose()
        for name, clip in clips.items():
            for fraction in (0, .25, .5, .75, 1):
                pose = wearer.pose(clip, clip[0] * fraction, wearer.scale)
                for bone, parent in bones:
                    context = f"{label}/{name}/{bone} at {fraction}"
                    self.assertAlmostEqual(math.dist(bind[bone][0], bind[parent][0]),
                                           math.dist(pose[bone][0], pose[parent][0]), delta=2e-5, msg=context)
                    self.assertAlmostEqual(bind[bone][2], pose[bone][2], delta=2e-6, msg=context)

    def test_fury_stance_preserves_cathar_and_horc_skeletons(self):
        # Cathar's appearance uses the O (Horc) skeleton, with longer neck/arm
        # offsets than the human authoring rig. Cover both sexes and every robe.
        names = {"sw_furystn", "sw_furystn_in", "sw_furystn_out"}
        manifest = json.loads((ROOT / "tools/RobeRgbModels.json").read_text())

        def clips_from(paths):
            clips = {}
            for path in paths:
                model = Model(path.read_bytes())
                for name in names & model.clips.keys():
                    self.assertNotIn(name, clips, f"Duplicate Fury phase in {path}")
                    clips[name] = model.clips[name]
            self.assertEqual(names, clips.keys(), "All three Fury phases must be installed")
            return clips

        for prefix, rig in (("pmo", "a_ba"), ("pfo", "a_fa")):
            body_paths = sorted((ROOT / "sw_cr_creature").glob(f"ab_{rig}_*.mdl"))
            body_paths.append(ROOT / "sw_cr_creature" / f"an_{rig}.mdl")
            body = Model((ROOT / "sw_pt_root" / f"{prefix}0.mdl").read_bytes(), False)
            self.assert_skeleton_proportions(body, clips_from(body_paths), prefix + "0")

            robes = [ROOT / path for path in manifest["files"]
                     if re.fullmatch(rf"sw_pt_root/{prefix}[1-9]\d*\.mdl", path)]
            self.assertTrue(robes, f"The {prefix} robe corpus must be present")
            cached = {}
            for path in robes:
                wearer = Model(path.read_bytes(), False)
                if wearer.parent not in cached:
                    parts = manifest["animation_bank_sets"][wearer.parent]["parts"]
                    directory = "sw_anim_f" if prefix[1] == "f" else "sw_anim_m"
                    cached[wearer.parent] = clips_from(ROOT / directory / f"{part}.mdl" for part in parts)
                self.assert_skeleton_proportions(wearer, cached[wearer.parent], path.stem)


if __name__ == "__main__":
    unittest.main()
