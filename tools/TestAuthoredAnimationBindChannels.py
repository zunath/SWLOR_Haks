"""Authored animations must cover robe rigs and retain each wearer's proportions."""
import json
import math
from pathlib import Path
import re
import shlex
import unittest

import CompileModels as mdl
import RobeAnimationBanks as banks
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

    def test_unarmed_phases_resolve_in_every_playable_robe_chain(self):
        # Body fallback can play the gesture while bypassing the robe's garment
        # joints. Require all phases before leaving the generated bank chain.
        names = {name + phase for name in (
            "sw_bruisst", "sw_dirtyjab", "sw_fincombo", "sw_flowst", "sw_haymaker",
            "sw_jab", "sw_lowkick", "sw_palmstr", "sw_perfform", "sw_uppercut")
            for phase in ("", "_in", "_out")}

        def table(name):
            lines = (ROOT / "sw_2da" / f"{name}.2da").read_text().splitlines()
            columns = lines[2].split()
            return {values[0]: dict(zip(columns, values[1:]))
                    for line in lines[3:] if (values := shlex.split(line)) and values[0].isdigit()}

        appearances = table("appearance")
        families = {appearances[row["Appearance"]]["RACE"].lower()
                    for row in table("racialtypes").values() if row["PlayerRace"] == "1"}
        roots = {row["MODEL"][:3] + row["PHENOTYPE"] for row in table("roberender").values()
                 if row["MODEL"][2] in families}
        # Droids have no RGB robes; both sexes of the other playable rigs do.
        self.assertEqual({f"p{sex}{family}" for sex in "mf" for family in families - {"z"}},
                         {name[:3] for name in roots})
        self.assertTrue(roots, "The playable RGB robe catalog must be present")
        manifest = json.loads((ROOT / "tools/RobeRgbModels.json").read_text())
        paths = {Path(path).stem: ROOT / path for path in manifest["files"]
                 if path.startswith(("sw_anim_m/", "sw_anim_f/")) and path.endswith(".mdl")}
        cached = {}
        for root in sorted(roots):
            parent = mdl.supermodel((ROOT / "sw_pt_root" / f"{root}.mdl").read_bytes())
            self.assertIn(parent, paths, f"{root}: missing generated robe animation parent {parent}")
            if parent not in cached:
                current, seen, clips = parent, set(), set()
                while banks.is_bank_name(current):
                    self.assertNotIn(current, seen, f"{root}: cyclic robe animation chain")
                    self.assertIn(current, paths, f"{root}: missing robe bank {current}")
                    seen.add(current)
                    bank = banks.CompiledBridge(paths[current].read_bytes())
                    clips.update(bank.clip_names)
                    current = bank.parent
                cached[parent] = clips
            self.assertFalse(names - cached[parent],
                             f"{root}: Unarmed phases fall through to the body: {sorted(names - cached[parent])}")

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
