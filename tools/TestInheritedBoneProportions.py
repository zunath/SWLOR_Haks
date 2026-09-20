"""Inherited player animations must never replace a wearer's own bone proportions.

A clip lives in a shared supermodel chain while the bind pose belongs to each
appearance. Keying a bone's position or scale therefore stamps the authoring
rig's proportions onto every other body, and the replacement latches because
native idle animates rotation only. This covers every clip the player chain
inherits, not just the authored `sw_` ones, and every bone, not just the head.
"""
import math
from pathlib import Path
import re
import unittest

from RobePoseAudit import Model
import StripInheritedBoneTracks as strip_tool


ROOT = Path(__file__).resolve().parents[1]
BONE_PATTERN = re.compile(r"(?:torso|pelvis|neck|head|[lr](?:bicep|forearm|hand|thigh|shin|foot))_g")


class InheritedBoneProportionTests(unittest.TestCase):
    def test_no_player_chain_clip_keys_bone_position_or_scale(self):
        models = strip_tool.chain_models(ROOT)
        self.assertTrue(models, "The humanoid animation chain must be present")
        reported = []
        for path in models:
            data = path.read_bytes()
            if not strip_tool.binary(data):
                continue
            reported += [f"{path.name}/{clip}/{bone}/{kind}" for clip, bone, kind in strip_tool.offenders(data)]
        self.assertEqual([], reported, "Inherited clips must key bone rotations and the root position only")

    def test_editable_bank_sources_match_the_installed_banks(self):
        stale = [path.name for path in strip_tool.chain_models(ROOT)
                 if strip_tool.binary(path.read_bytes()) and strip_tool.update_source(ROOT, path, check_only=True)]
        self.assertEqual([], stale, "Run StripInheritedBoneTracks.py to refresh the editable sources")

    def test_every_player_skeleton_keeps_its_bone_lengths_and_scales(self):
        index = {}
        for directory in strip_tool.ANIMATION_DIRECTORIES:
            for path in sorted((ROOT / directory).glob("*.mdl")):
                index.setdefault(path.stem.lower(), path)
        chains = {}

        def clips_for(name):
            key = (name or "").lower()
            if key in chains:
                return chains[key]
            chains[key] = result = {}
            path = index.get(key)
            if path:
                data = path.read_bytes()
                if strip_tool.binary(data):
                    model = Model(data)
                    for clip, value in model.clips.items():
                        result.setdefault(clip, (path.name, value))
                    for clip, value in clips_for(model.parent).items():
                        result.setdefault(clip, value)
            return result

        skeletons, checked = {}, 0
        for path in sorted((ROOT / "sw_pt_root").glob("*.mdl")):
            data = path.read_bytes()
            if not strip_tool.binary(data):
                continue
            wearer = Model(data, False)
            bind = wearer.pose()
            bones = [(name, wearer.nodes[parent][0]) for name, parent, _, _, _ in wearer.nodes
                     if parent is not None and BONE_PATTERN.fullmatch(name)]
            signature = (wearer.parent, wearer.scale,
                         tuple((name, round(math.dist(bind[name][0], bind[parent][0]), 6), round(bind[name][2], 6))
                               for name, parent in bones))
            if signature in skeletons:
                continue
            skeletons[signature] = path.stem
            for clip, (owner, value) in clips_for(wearer.parent).items():
                for fraction in (0, .5, 1):
                    pose = wearer.pose(value, value[0] * fraction, wearer.scale)
                    for name, parent in bones:
                        context = f"{path.stem}/{owner}/{clip}/{name} at {fraction}"
                        self.assertAlmostEqual(math.dist(bind[name][0], bind[parent][0]),
                                               math.dist(pose[name][0], pose[parent][0]), delta=2e-4, msg=context)
                        self.assertAlmostEqual(bind[name][2], pose[name][2], delta=2e-5, msg=context)
                    checked += 1
        self.assertGreater(len(skeletons), 20, "The player appearance corpus must be present")
        self.assertGreater(checked, 0)


class SourceStrippingTests(unittest.TestCase):
    def test_keyed_controllers_drop_their_value_rows(self):
        text = "\n".join([
            "newanim gesture rig", "  node trimesh head_g", "    parent neck_g",
            "    positionkey 2", "      0.0 1 2 3", "      1.0 1 2 3",
            "    orientationkey 1", "      0.0 0 0 1 0", "    scalekey 1", "      0.0 1",
            "  endnode", "doneanim gesture rig", ""])
        self.assertEqual("\n".join([
            "newanim gesture rig", "  node trimesh head_g", "    parent neck_g",
            "    orientationkey 1", "      0.0 0 0 1 0",
            "  endnode", "doneanim gesture rig", ""]), strip_tool.strip_source_text(text))

    def test_geometry_bind_pose_and_non_bone_nodes_are_preserved(self):
        text = "\n".join([
            "node dummy head_g", "  position 0 0 1", "  scale 1", "endnode",
            "newanim gesture rig", "  node dummy rootdummy", "    position 0 0 1", "  endnode",
            "  node dummy rhand", "    position 0 0 1", "  endnode",
            "doneanim gesture rig", ""])
        self.assertEqual(text, strip_tool.strip_source_text(text))


if __name__ == "__main__":
    unittest.main()
