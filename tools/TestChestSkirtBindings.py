"""Chest 154 and 269 coat skirts must swing with the stride, not split between the legs or freeze,
and hang beside the legs when the wearer stands.

Needs the installed game's data directory for the stock dwarf and half-orc animations: set
NWN_GAME_DATA, or install the Steam edition in its default location.
"""
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import unittest

import ChestSkirtBindings as skirts
import CompileModels as mdl
import GenerateTintMapAssets as tint
import RestoreRobeBodyTracks as body_tracks
from RobePoseAudit import Model, rotate

ROOT = Path(__file__).resolve().parents[1]
GAME_DATA = Path(os.environ.get(
    'NWN_GAME_DATA', r'C:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights\data'))
# Share of leg samples that may show behind the coat while walking or running (hip-bound: up to 55%).
MAX_EXPOSED = .05
# 99.5th-percentile edge stretch while walking or running (the original leg weights: up to 13.7x).
MAX_STRETCH = 3.5
# How far either hem edge may sit from where the original put it beside the feet in the standing idle (m).
MAX_IDLE_SHIFT = .06


class ChestSkirtBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = json.loads(skirts.MANIFEST.read_text())['models']
        cls.sources = {name: skirts.source(entry['sourceBlob']) for name, entry in cls.models.items()}
        cls.current = {name: (ROOT / entry['path']).read_bytes() for name, entry in cls.models.items()}

    def weights(self, data):
        bones, _ = skirts.skeleton(data)
        return [skirts.influences(data, skin, vertex) for skin in skirts.skins(data, bones) for vertex in range(skin['count'])]

    def test_catalog_covers_every_skinned_body_of_both_styles(self):
        expected = {f'p{gender}h0_chest154' for gender in 'fm'} | {f'p{gender}{race}0_chest269' for gender in 'fm' for race in 'deho'}
        self.assertEqual(expected, set(self.models))
        for name, entry in self.models.items():
            with self.subTest(model=name):
                self.assertEqual(f'sw_pt_chest/{name}.mdl', entry['path'])
                self.assertEqual(entry['sha256'], hashlib.sha256(self.current[name]).hexdigest())
                self.assertEqual(self.current[name], skirts.build(entry))

    def test_leg_weights_move_to_the_pelvis_thighs_and_back_belt_flap(self):
        legs = {'lthigh_g', 'rthigh_g', 'lshin_g', 'rshin_g'}
        lower_body = {'pelvis_g', *legs, *skirts.FLAP_CHAIN[1:]}
        for name in self.models:
            with self.subTest(model=name):
                rest = skirts.rest_positions(self.sources[name])
                body = skirts.skeleton(self.sources[name])[1]
                pose, animated = body.pose(), skirts.flaps_animated(body)
                hip, top, _ = skirts.heights(pose)
                rebound = flapped = 0
                for before, after, position in zip(self.weights(self.sources[name]), self.weights(self.current[name]), rest, strict=True):
                    self.assertLessEqual(len(after), 4)
                    self.assertAlmostEqual(sum(before.values()), sum(after.values()), delta=2e-6)
                    if not skirts.LEG_BONES & set(before):
                        self.assertEqual(before, after)
                        continue
                    rebound += 1
                    flapped += bool(set(skirts.FLAP_CHAIN) & set(after))
                    # The feet let go; the waist keeps the pelvis binding; below the middle flap
                    # joint only the legs and the flap carry the coat where the flap swings, and
                    # the legs and the pelvis where it does not.
                    self.assertFalse({'lfoot_g', 'rfoot_g', skirts.FLAP_CHAIN[0]} & set(after))
                    self.assertLessEqual(set(after), set(before) | lower_body)
                    if position[2] >= hip:
                        self.assertFalse((legs | set(skirts.FLAP_CHAIN)) & set(after))
                    if position[2] <= top:
                        self.assertLessEqual(set(after), lower_body - {'pelvis_g'} if animated else {'pelvis_g', *legs})
                    expected = skirts.skirt_weights(before, position, pose, animated, skirts.flap_strength(self.models[name]['path']))
                    self.assertEqual(set(expected), set(after))
                    for bone, weight in expected.items():
                        self.assertAlmostEqual(weight, after[bone], delta=2e-6)
                self.assertGreater(rebound, 500)
                if animated:
                    self.assertGreater(flapped, rebound * .3)
                else:
                    self.assertEqual(0, flapped)

    def test_the_halves_share_the_legs_at_the_back_seam_and_follow_the_shins_at_the_sides(self):
        """On the back seam a vertex follows both legs equally; around the body it passes smoothly to one leg,
        and only away from the seam does the lower coat follow the shin."""
        pose = skirts.skeleton(self.sources['pmh0_chest154'])[1].pose()
        pelvis = pose['pelvis_g'][0]
        around = lambda degrees, height: (pelvis[0] + .3 * math.sin(math.radians(degrees)), pelvis[1] - .3 * math.cos(math.radians(degrees)), height)
        self.assertAlmostEqual(.5, skirts.left_share(around(0, .5), pose))
        self.assertEqual(1., skirts.left_share(around(-skirts.SEAM, .5), pose))
        self.assertEqual(0., skirts.left_share(around(skirts.SEAM, .5), pose))
        self.assertEqual((1., 0.), (skirts.left_share(around(-179, .5), pose), skirts.left_share(around(179, .5), pose)))
        steps = [skirts.left_share(around(degrees, .5), pose) for degrees in range(-90, 91, 3)]
        self.assertEqual(steps, sorted(steps, reverse=True))
        self.assertLess(max(a - b for a, b in zip(steps, steps[1:])), .1)
        knee = pose['lshin_g'][0][2]
        self.assertEqual(0., skirts.shin_share(around(0, knee - .3), pose))
        self.assertEqual(0., skirts.shin_share(around(120, knee + .01), pose))
        self.assertEqual(1., skirts.shin_share(around(120, knee - skirts.KNEE_BAND), pose))

    def test_flaps_count_as_animated_only_where_the_clips_reach_them(self):
        animated = {name for name in self.models if skirts.flaps_animated(skirts.skeleton(self.sources[name])[1])}
        # Dwarf and half-orc roots number their flap joints apart from a_ba (client-verified: their flaps stay still).
        self.assertEqual({name for name in self.models if name[2] in 'he'}, animated)

    def test_bind_shape_and_every_other_byte_are_unchanged(self):
        for name in self.models:
            with self.subTest(model=name):
                before, after = self.sources[name], self.current[name]
                error = max(math.dist(a, b) for a, b in zip(skirts.rest_positions(before), skirts.rest_positions(after), strict=True))
                self.assertLess(error, 2e-5)
                bones, _ = skirts.skeleton(before)
                tables = bytearray(len(before))
                for skin in skirts.skins(before, bones):
                    for vertex in range(skin['count']):
                        weights = skin['weights'] + vertex * (skin['stride'] or 16)
                        indices = skin['indices'] + vertex * (skin['stride'] or 8)
                        tables[weights:weights + 16] = b'\1' * 16
                        tables[indices:indices + 8] = b'\1' * 8
                    # Bones a skin lacked take free palette slots; their node-to-slot entries change with them.
                    tables[skin['palette_offset']:skin['palette_offset'] + 34] = b'\1' * 34
                    for bone in ('pelvis_g', 'lthigh_g', 'rthigh_g', *skirts.FLAP_CHAIN):
                        entry = skin['bone_map_offset'] + bones.index(bone) * 2
                        tables[entry:entry + 2] = b'\1\1'
                self.assertEqual(len(before), len(after))
                self.assertFalse([offset for offset in range(len(before)) if before[offset] != after[offset] and not tables[offset]])

    def test_palette_slots_and_bone_map_agree(self):
        for name in self.models:
            with self.subTest(model=name):
                data = self.current[name]
                bones, _ = skirts.skeleton(data)
                for skin in skirts.skins(data, bones):
                    for vertex in range(skin['count']):
                        weights = struct.unpack_from('<4f', data, skin['weights'] + vertex * (skin['stride'] or 16))
                        slots = struct.unpack_from('<4h', data, skin['indices'] + vertex * (skin['stride'] or 8))
                        for weight, slot in zip(weights, slots):
                            if weight:
                                self.assertEqual(slot, skin['bone_map'][skin['palette'][slot]])

    def test_robe_body_aliases_carry_the_same_weights(self):
        aliases = json.loads((ROOT / 'tools/PartSkinModels.json').read_text())['models']
        found = set()
        for alias, entry in aliases.items():
            if entry['source'] not in self.models:
                continue
            found.add(entry['source'])
            with self.subTest(alias=alias):
                data = (ROOT / entry['path']).read_bytes()
                self.assertEqual(skin_weight_rows(self.current[entry['source']]), skin_weight_rows(data))
        self.assertEqual(set(self.models), found)

    def test_skirts_swing_over_the_legs_in_walk_and_run(self):
        """The coat must follow the stride (not freeze), keep the legs covered from behind and not tear."""
        for name in self.models:
            for clip in ('walk', 'run'):
                with self.subTest(model=name, clip=clip):
                    swing, exposed, stretch = stride_metrics(self.sources[name], self.current[name], clip)
                    # Hip-bound, the hem moves at most 3 cm against the pelvis.
                    self.assertGreater(swing, .04)
                    self.assertLess(exposed, MAX_EXPOSED)
                    # The original leg weights tore the back open (up to 13.7x).
                    self.assertLess(stretch, MAX_STRETCH)

    def test_coat_hangs_beside_the_legs_when_standing(self):
        """In the standing idle the hips sway to one side; the coat must stay beside the legs as the original did."""
        for name in self.models:
            with self.subTest(model=name):
                original = idle_gaps(self.sources[name], self.sources[name])
                current = idle_gaps(self.sources[name], self.current[name])
                # Hung from the hips and the flap, one side tucked up to 16 cm closer to its leg.
                for before, after in zip(original, current, strict=True):
                    self.assertLess(abs(after - before), MAX_IDLE_SHIFT)


@lru_cache(None)
def library():
    """The HAKs' animation models, then the installed game's stock ones (the dwarf and half-orc chains end there)."""
    if not (GAME_DATA / 'nwn_base.key').is_file():
        raise AssertionError(f"Set NWN_GAME_DATA to the game's data directory (missing {GAME_DATA})")
    return body_tracks.library_for(tint.find_active_models(), GAME_DATA)


def animation_clip(root: str, clip: str):
    """The clip as the wearer's supermodel chain defines it, nearest model first."""
    clips = library().clips(mdl.supermodel(tint.find_active_models()[root].read_bytes()))
    if clip not in clips:
        raise ValueError(f'{root} has no {clip} clip')
    return clips[clip]


def idle_gaps(source: bytes, current: bytes, clip_name: str = 'pause1'):
    """How far the hem reaches past the left and the right foot in the standing idle (m)."""
    bones, _ = skirts.skeleton(source)
    body = Model(tint.find_active_models()[bones[0]].read_bytes(), False)
    skirt = [bool(skirts.LEG_BONES & set(weights)) for weights in
             (skirts.influences(source, skin, vertex) for skin in skirts.skins(source, bones) for vertex in range(skin['count']))]
    rest = skirts.rest_positions(source)
    floor = min(rest[index][2] for index, flag in enumerate(skirt) if flag)
    hem = [index for index, flag in enumerate(skirt) if flag and rest[index][2] < floor + .2]
    pose = body.pose(animation_clip(bones[0], clip_name), 0.)
    posed = skirts.rest_positions(current, pose)
    sides = [posed[index][0] for index in hem]
    return pose['lfoot_g'][0][0] - min(sides), max(sides) - pose['rfoot_g'][0][0]


def stride_metrics(source: bytes, current: bytes, clip_name: str, phases: int = 8):
    """Hem swing against the pelvis (m), share of leg samples showing behind the coat, and 99.5th-percentile edge stretch."""
    bones, _ = skirts.skeleton(source)
    body = Model(tint.find_active_models()[bones[0]].read_bytes(), False)
    clip = animation_clip(bones[0], clip_name)
    skirt = [bool(skirts.LEG_BONES & set(weights)) for weights in
             (skirts.influences(source, skin, vertex) for skin in skirts.skins(source, bones) for vertex in range(skin['count']))]
    rest = skirts.rest_positions(current)
    edges = set()
    offset = 0
    for _, _, mesh in mdl.binary_nodes(current):
        if not mesh or 'weights' not in mesh:
            continue
        for face in mesh['faces']:
            corners = [offset + corner for corner in face[:3]]
            if any(skirt[corner] for corner in corners):
                edges |= {tuple(sorted(pair)) for pair in zip(corners, corners[1:] + corners[:1])}
        offset += len(mesh['verts'])
    lowest = sorted((index for index, flag in enumerate(skirt) if flag), key=lambda index: rest[index][2])
    hem = lowest[:max(1, len(lowest) // 10)]
    stretches, samples, hidden, hem_back = [], 0, 0, []
    for phase in range(phases):
        pose = body.pose(clip, clip[0] * phase / phases)
        posed = skirts.rest_positions(current, pose)
        stretches += [math.dist(posed[a], posed[b]) / length for a, b in edges if (length := math.dist(rest[a], rest[b])) > 1e-4]
        origin, turn, _ = pose['pelvis_g']
        inverse = (-turn[0], -turn[1], -turn[2], turn[3])
        local = [rotate(inverse, tuple(a - b for a, b in zip(posed[index], origin))) for index in lowest]
        hem_back.append(sum(point[1] for point in local[:len(hem)]) / len(hem))
        for side in 'lr':
            joints = [pose[f'{side}{bone}_g'][0] for bone in ('thigh', 'shin', 'foot')]
            for upper, lower in zip(joints, joints[1:]):
                for step in range(1, 5):
                    sample = tuple(a + (b - a) * step / 5 for a, b in zip(upper, lower))
                    point = rotate(inverse, tuple(a - b for a, b in zip(sample, origin)))
                    if point[1] > 0:
                        continue  # in front of the hips, where the coat is open
                    bearing = math.atan2(point[0], point[1])
                    radii = [math.hypot(q[0], q[1]) for q in local if abs(q[2] - point[2]) < .06
                             and abs((math.atan2(q[0], q[1]) - bearing + math.pi) % math.tau - math.pi) < math.radians(12)]
                    if radii:
                        samples += 1
                        hidden += math.hypot(point[0], point[1]) + .05 <= max(radii)
    stretches.sort()
    return max(hem_back) - min(hem_back), 1 - hidden / max(samples, 1), stretches[int(len(stretches) * .995)]


def skin_weight_rows(data):
    """Every skin's raw vertex weights, without resolving the wearer's skeleton."""
    uint = lambda offset: struct.unpack_from('<I', data, offset)[0]
    raw = 12 + uint(4)
    rows = []

    def visit(pointer):
        node = 12 + pointer
        flags = uint(node + 108)
        if flags & 0x20 and flags & 0x40:
            mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
            count, stride = struct.unpack_from('<H', data, mesh + 448)[0], uint(mesh + 440)
            weights = raw + uint(mesh + 512 + 12)
            rows.append([struct.unpack_from('<4f', data, weights + vertex * (stride or 16)) for vertex in range(count)])
        for index in range(uint(node + 76)):
            visit(uint(12 + uint(node + 72) + index * 4))

    visit(uint(84))
    return rows


if __name__ == '__main__':
    unittest.main()
