"""Neck 32's cloak (NeckCloakSkins.py) must hang from the cloak bones and cover the legs from behind.

Needs CleanModels (SetupModelCompiler.ps1) to rebuild the skins, and the installed game's data
directory for the stock animations: set NWN_GAME_DATA, or install the Steam edition in its
default location.
"""
import hashlib
import json
import math
from pathlib import Path
import unittest

import ChestSkirtBindings as skirts
import CompileModels as mdl
import GenerateTintMapAssets as tint
import NeckCloakSkins as cloaks
from RobePoseAudit import Model, rotate
from TestChestSkirtBindings import MAX_EXPOSED, MAX_STRETCH, animation_clip

ROOT = Path(__file__).resolve().parents[1]


def cloak_points(data: bytes, pose: dict) -> list[tuple[float, float, float]]:
    """The cloak's vertices in a pose: skinned, or rigid on the neck as the original was."""
    if mdl.external_skeleton_name(data) and any(mesh and 'weights' in mesh for _, _, mesh in mdl.binary_nodes(data)):
        return skirts.rest_positions(data, pose)
    origin, turn, _ = pose['neck_g']
    bind = skirts.skeleton(data)[1].pose()['neck_g'][0]
    return [tuple(a + b for a, b in zip(origin, rotate(turn, tuple(p - q for p, q in zip(point, bind)))))
            for point in cloaks.original_positions(data, skirts.skeleton(data)[1].pose())]


def stride(data: bytes, clip_name: str, phases: int = 8):
    """Share of leg samples behind the hips that show through the cloak, and the 99.5th-percentile edge stretch."""
    bones, bind = skirts.skeleton(data)
    body = Model(tint.find_active_models()[bones[0]].read_bytes(), False)
    rest = cloak_points(data, bind.pose())
    neck = bind.pose()['neck_g'][0][2]
    cape = [index for index, point in enumerate(rest) if point[2] < neck - .4]
    edges = set()
    offset = 0
    for _, _, mesh in mdl.binary_nodes(data):
        if not mesh or not mesh.get('verts'):
            continue
        for face in mesh['faces']:
            corners = [offset + corner for corner in face[:3]]
            edges |= {tuple(sorted(pair)) for pair in zip(corners, corners[1:] + corners[:1])}
        offset += len(mesh['verts'])
    clip = animation_clip(bones[0], clip_name)
    samples = hidden = 0
    stretches = []
    for phase in range(phases):
        pose = body.pose(clip, clip[0] * phase / phases)
        posed = cloak_points(data, pose)
        stretches += [math.dist(posed[a], posed[b]) / length for a, b in edges if (length := math.dist(rest[a], rest[b])) > 1e-4]
        origin, turn, _ = pose['pelvis_g']
        inverse = (-turn[0], -turn[1], -turn[2], turn[3])
        local = [rotate(inverse, tuple(a - b for a, b in zip(posed[index], origin))) for index in cape]
        for side in 'lr':
            joints = [pose[f'{side}{bone}_g'][0] for bone in ('thigh', 'shin', 'foot')]
            for upper, lower in zip(joints, joints[1:]):
                for step in range(1, 5):
                    sample = rotate(inverse, tuple(a + (b - a) * step / 5 - c for a, b, c in zip(upper, lower, origin)))
                    if sample[1] > 0:
                        continue  # in front of the hips
                    bearing = math.atan2(sample[0], sample[1])
                    radii = [math.hypot(q[0], q[1]) for q in local if abs(q[2] - sample[2]) < .06
                             and abs((math.atan2(q[0], q[1]) - bearing + math.pi) % math.tau - math.pi) < math.radians(12)]
                    if radii:
                        samples += 1
                        hidden += math.hypot(sample[0], sample[1]) + .05 <= max(radii)
    stretches.sort()
    return 1 - hidden / max(samples, 1), stretches[int(len(stretches) * .995)]


class NeckCloakSkinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = json.loads(cloaks.MANIFEST.read_text())['models']
        cls.sources = {name: skirts.source(entry['sourceBlob']) for name, entry in cls.models.items()}
        cls.current = {name: (ROOT / entry['path']).read_bytes() for name, entry in cls.models.items()}

    def test_catalog_covers_every_body_whose_cloak_bones_animate(self):
        self.assertEqual({f'p{gender}{race}0_neck032' for gender in 'fm' for race in 'he'}, set(self.models))
        for name, entry in self.models.items():
            with self.subTest(model=name):
                self.assertEqual(f'sw_pt_neck/{name}.mdl', entry['path'])
                self.assertEqual(entry['sha256'], hashlib.sha256(self.current[name]).hexdigest())
                self.assertEqual(self.current[name], cloaks.build(entry))
        # The dwarf and half-orc bodies never move their cloak bones and keep the dangly original.
        for path in sorted((ROOT / 'sw_pt_neck').glob('p??0_neck032.mdl')):
            if path.stem not in self.models:
                with self.subTest(model=path.stem):
                    self.assertFalse(skirts.cloak_animated(skirts.skeleton(path.read_bytes())[1]))
                    self.assertFalse(any(mesh and 'weights' in mesh for _, _, mesh in mdl.binary_nodes(path.read_bytes())))

    def test_hood_stays_on_the_neck_and_cloak_hangs_from_the_cloak_bones(self):
        for name, data in self.current.items():
            with self.subTest(model=name):
                bones, body = skirts.skeleton(data)
                pose = body.pose()
                first = max(pose[column[0]][0][2] for column in skirts.CLOAK_COLUMNS)
                second = min(pose[column[1]][0][2] for column in skirts.CLOAK_COLUMNS)
                weights = [skirts.influences(data, skin, vertex) for skin in skirts.skins(data, bones) for vertex in range(skin['count'])]
                for current, position in zip(weights, skirts.rest_positions(data), strict=True):
                    self.assertLessEqual(len(current), 4)
                    self.assertLessEqual(set(current), skirts.CLOAK_BONES | {'neck_g'})
                    if position[2] > first:
                        self.assertEqual({'neck_g'}, set(current))
                    if position[2] < second:
                        self.assertLessEqual(set(current), skirts.CLOAK_BONES)

    def test_cloak_covers_the_legs_from_behind_in_walk_and_run(self):
        """Fixed to the neck, the cloak let the striding legs through; on the cloak bones it covers them."""
        for name in self.models:
            for clip in ('walk', 'run'):
                with self.subTest(model=name, clip=clip):
                    before, _ = stride(self.sources[name], clip)
                    exposed, stretch = stride(self.current[name], clip)
                    self.assertLess(exposed, MAX_EXPOSED)
                    self.assertLessEqual(exposed, before)
                    self.assertLess(stretch, MAX_STRETCH)


if __name__ == '__main__':
    unittest.main()
