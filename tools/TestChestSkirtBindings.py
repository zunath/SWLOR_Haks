"""Coat skirts, skirt belts, skirts and capes (ChestSkirtBindings.STYLES) must swing like cloth with the
stride, not split between the legs, freeze, stand out like a shell or let the legs through, and hang
beside the legs when the wearer stands.

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
# Hip-bound, the hem moves at most 3 cm against the pelvis in a stride; it must move more.
MIN_SWING = .02
# Share of leg samples that may show behind a skirt or cape where its bones swing (hip-bound: up to 55%).
MAX_EXPOSED = .07
# Where the flaps never move, legs may show behind the skirt this much more often than the
# original did, or up to MAX_EXPOSED_STILL (male Cathar pelvis 035 walking: 9%, originally 0).
EXPOSED_MARGIN = .03
MAX_EXPOSED_STILL = .1
# Share of leg samples that may poke out in front of a closed skirt (originally up to 75%).
MAX_FRONT = .25
# 99.5th-percentile edge stretch while walking or running (the original leg weights: up to 13.7x).
MAX_STRETCH = 4.
# Mean angle of the back panel from upright while running (degrees). The thigh-and-flap binding
# bent the back at the buttocks and held it out level (52 to 97); the flap-and-pelvis binding the
# test server liked, 43 to 63.
MAX_RUN_BACK = 65.
# How far an open coat reaches in front of the hips below the knees while running, on average (m).
# Following the striding legs, the side panels made a tent (up to 50 cm); trailing, at most 25 cm.
MAX_TENT = .3
# How much further the back panel may lean out than the original's when standing (degrees). The
# thigh-and-flap binding bent it into a shell up to 33 degrees further out.
MAX_BACK_LEAN = 20.
# How far either hem edge may sit inside where the original put it beside the feet when standing (m),
# and how much further off-centre (one edge against the other) the hem may sit. Hung from the flap and
# the pelvis alone, the coats narrowed by up to 19 cm and shifted by up to 24 cm.
MAX_IDLE_NARROWING = .17
MAX_IDLE_SHIFT = .2
# The same in /paraderest, whose clip now holds the flaps at rest (FillEmoteFlapTracks.py). Holding
# the idle's flap lean instead, the flap-hung skirts sat up to 22 cm off-centre there.
MAX_PARADE_NARROWING = .12
MAX_PARADE_SHIFT = .05
# Where the flaps never move, how far either hem edge may sit from where the original put it (m).
MAX_STILL_SHIFT = .08
MAX_STILL_PARADE_SHIFT = .12

class ChestSkirtBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(skirts.MANIFEST.read_text())
        cls.models = cls.manifest['models']
        cls.sources = {name: skirts.source(entry['sourceBlob']) for name, entry in cls.models.items()}
        cls.current = {name: (ROOT / entry['path']).read_bytes() for name, entry in cls.models.items()}

    def weights(self, data):
        bones, _ = skirts.skeleton(data)
        return [skirts.influences(data, skin, vertex) for skin in skirts.skins(data, bones) for vertex in range(skin['count'])]

    def shape(self, name):
        return skirts.shape(self.models[name]['path'], self.sources[name])

    def test_catalog_covers_every_skinned_body_of_every_style(self):
        every = lambda part: {f'p{gender}{race}0_{part}' for gender in 'fm' for race in 'deho'}
        cloaked = lambda part: {f'p{gender}{race}0_{part}' for gender in 'fm' for race in 'he'}
        expected = ({f'p{gender}h0_chest154' for gender in 'fm'} | every('chest269') | every('chest028') | every('chest059')
                    # Bothan female chest 310's bone table does not match its skeleton, so it cannot be rebound.
                    | every('chest310') - {'pfe0_chest310'}
                    | every('belt019') | every('belt110') | every('belt112') | every('belt114')
                    # Only the human and half-orc bodies carry pelvis 035.
                    | {f'p{gender}{race}0_pelvis035' for gender in 'fm' for race in 'ho'} - {'pfh0_pelvis035'}
                    # Capes only where the cloak bones animate.
                    | cloaked('neck291') | cloaked('shol260') | cloaked('shor260'))
        self.assertEqual(expected, set(self.models))
        self.assertEqual(set(skirts.STYLES), {skirts.style_key(entry['path']) for entry in self.models.values()})
        folders = {'chest': 'sw_pt_chest', 'belt': 'sw_pt_belt', 'pelvis': 'sw_pt_pelvis', 'neck': 'sw_pt_neck',
                   'shol': 'sw_pt_lshoul', 'shor': 'sw_pt_rshoul'}
        for name, entry in self.models.items():
            with self.subTest(model=name):
                kind = skirts.style_key(entry['path']).rstrip('0123456789')
                self.assertEqual(f'{folders[kind]}/{name}.mdl', entry['path'].lower())
                self.assertEqual(entry['sha256'], hashlib.sha256(self.current[name]).hexdigest())
                self.assertEqual(self.current[name], skirts.build(entry))

    def test_restored_parts_keep_their_original_rigging(self):
        restored = self.manifest['restored']
        self.assertEqual({f'sw_pt_{kind}/p{gender}{race}0_{kind}{number}.mdl' for kind, number in (('belt', 226), ('pelvis', 198))
                          for gender in 'fm' for race in 'deho'} | {'sw_pt_pelvis/pfh0_pelvis035.mdl'}, set(restored))
        for relative, blob in restored.items():
            with self.subTest(model=relative):
                self.assertEqual(skirts.source(blob), (ROOT / relative).read_bytes())

    def test_skirt_weights_follow_the_pelvis_flaps_and_a_share_of_the_legs(self):
        legs = {'lthigh_g', 'rthigh_g', 'lshin_g', 'rshin_g'}
        flaps = {*skirts.FLAP_CHAIN, *skirts.FRONT_CHAIN}
        lower_body = {'pelvis_g', *legs, *skirts.FLAP_CHAIN[1:], *skirts.FRONT_CHAIN[1:]}
        for name in self.models:
            shape = self.shape(name)
            if shape['cape']:
                continue
            with self.subTest(model=name):
                rest = skirts.rest_positions(self.sources[name])
                body = skirts.skeleton(self.sources[name])[1]
                pose, animated = body.pose(), skirts.flaps_animated(body)
                hip, top, _ = skirts.heights(pose)
                rebound = flapped = low = 0
                for before, after, position in zip(self.weights(self.sources[name]), self.weights(self.current[name]), rest, strict=True):
                    self.assertLessEqual(len(after), 4)
                    self.assertAlmostEqual(sum(before.values()), sum(after.values()), delta=2e-6)
                    if not skirts.LEG_BONES & set(before):
                        self.assertEqual(before, after)
                        continue
                    rebound += 1
                    low += position[2] < top
                    flapped += bool(flaps & set(after))
                    # The feet let go and the top flap joints stay out; the waist keeps the pelvis binding.
                    self.assertFalse({'lfoot_g', 'rfoot_g', skirts.FLAP_CHAIN[0], skirts.FRONT_CHAIN[0]} & set(after))
                    self.assertLessEqual(set(after), set(before) | lower_body)
                    if position[2] >= hip:
                        self.assertFalse((legs | flaps) & set(after))
                    if animated:
                        # Below the hips a vertex follows only the leg on its own side, at most LEGS of it.
                        far = 'r' if skirts.bearing(position, pose) < 0 else 'l'
                        self.assertFalse({f'{far}thigh_g', f'{far}shin_g'} & set(after))
                        self.assertLessEqual(sum(after.get(bone, 0.) for bone in legs), skirts.LEGS * sum(after.values()) + 2e-6)
                        if position[2] <= top:
                            self.assertLessEqual(set(after), lower_body)
                    elif position[2] <= top:
                        self.assertLessEqual(set(after), {'pelvis_g', *legs})
                    expected = skirts.skirt_weights(before, position, pose, animated, shape)
                    self.assertEqual(set(expected), set(after))
                    for bone, weight in expected.items():
                        self.assertAlmostEqual(weight, after[bone], delta=2e-6)
                self.assertGreater(rebound, 100)
                if animated:
                    # Short skirts end above the flaps.
                    self.assertGreaterEqual(flapped, low * .3)
                else:
                    self.assertEqual(0, flapped)

    def test_cape_weights_hang_from_the_cloak_bones(self):
        for name in self.models:
            if not self.shape(name)['cape']:
                continue
            with self.subTest(model=name):
                rest = skirts.rest_positions(self.sources[name])
                pose = skirts.skeleton(self.sources[name])[1].pose()
                second = min(pose[column[1]][0][2] for column in skirts.CLOAK_COLUMNS)
                rebound = 0
                for before, after, position in zip(self.weights(self.sources[name]), self.weights(self.current[name]), rest, strict=True):
                    self.assertLessEqual(len(after), 4)
                    self.assertAlmostEqual(sum(before.values()), sum(after.values()), delta=2e-6)
                    if not skirts.LEG_BONES & set(before):
                        self.assertEqual(before, after)
                        continue
                    rebound += 1
                    self.assertFalse(skirts.LEG_BONES & set(after))
                    # Below the cloak's second row only the cloak bones carry the cape.
                    if position[2] < second:
                        self.assertLessEqual(set(after), skirts.CLOAK_BONES)
                    self.assertLessEqual(set(after), set(before) | skirts.CLOAK_BONES | {'torso_g'})
                self.assertGreater(rebound, 50)

    def test_shares_depend_only_on_the_bind_position(self):
        """Away from a seam a lower skirt vertex follows its own leg, near one it stays on the pelvis and the
        flap; the back flap fades round to the front; capes split between neighbouring cloak bones."""
        pose = skirts.skeleton(self.sources['pmh0_chest154'])[1].pose()
        pelvis = pose['pelvis_g'][0]
        around = lambda degrees, height, radius=.3: (pelvis[0] + radius * math.sin(math.radians(degrees)), pelvis[1] - radius * math.cos(math.radians(degrees)), height)
        self.assertEqual(0., skirts.leg_share(around(skirts.SEAM, .5), pose))
        self.assertAlmostEqual(skirts.LEGS, skirts.leg_share(around(skirts.SIDE, .5), pose))
        self.assertAlmostEqual(skirts.LEGS, skirts.leg_share(around(180, .5), pose))
        self.assertEqual(0., skirts.leg_share(around(180 - skirts.SEAM, .5), pose, closed=True))
        steps = [skirts.leg_share(around(degrees, .5), pose) for degrees in range(0, 181, 3)]
        self.assertEqual(steps, sorted(steps))
        self.assertLess(max(b - a for a, b in zip(steps, steps[1:])), .1)
        # Vertices on the body's axis have no meaningful bearing and stay on the pelvis.
        self.assertEqual(0., skirts.leg_share(around(90, .5, 0.), pose))
        self.assertEqual(0., skirts.flap_share(around(0, .5, 0.), pose))
        self.assertLess(skirts.leg_share(around(90, .5, skirts.AXIS / 2), pose), skirts.LEGS)
        _, top, _ = skirts.heights(pose)
        self.assertEqual(1., skirts.flap_share(around(0, top - .01), pose))
        self.assertAlmostEqual(.5, skirts.flap_share(around(90, top - .01), pose))
        self.assertEqual(0., skirts.flap_share(around(180, top - .01), pose))
        self.assertEqual(0., skirts.flap_share(around(0, pose['lthigh_g'][0][2] + .01), pose))
        # A closed skirt's two flaps never share a vertex, so every vertex stays within four bones.
        closed = skirts.style('sw_pt_pelvis/pmh0_pelvis035.mdl')
        for degrees in range(0, 181, 5):
            point = around(degrees, .3)
            self.assertFalse(skirts.flap_share(point, pose, closed['falloff']) and skirts.front_share(point, pose, closed['front_falloff']))
        # Capes: the middle column on the spine, the outer columns beyond them, shares summing to one.
        middle = pose['cm3_g'][0]
        self.assertEqual({'cm3_g': 1.}, skirts.cloak_weights(middle, pose))
        for x in (-.4, -.15, 0., .1, .4):
            for height in (1.7, 1.4, 1., .6, .2):
                cloak = skirts.cloak_weights((x, -.2, height), pose)
                self.assertAlmostEqual(1., sum(cloak.values()))
                self.assertLessEqual(len(cloak), 4)
                self.assertEqual(height > max(pose[column[1]][0][2] for column in skirts.CLOAK_COLUMNS), None in cloak)

    def test_flaps_and_cloak_bones_count_as_animated_only_where_the_clips_reach_them(self):
        bodies = {name: skirts.skeleton(self.sources[name])[1] for name in self.models}
        # Dwarf and half-orc roots number their flap and cloak joints apart from a_ba (client-verified: their flaps stay still).
        self.assertEqual({name for name in self.models if name[2] in 'he'}, {name for name in self.models if skirts.flaps_animated(bodies[name])})
        self.assertEqual({name for name in self.models if name[2] in 'he'}, {name for name in self.models if skirts.cloak_animated(bodies[name])})

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
                    for bone in ('pelvis_g', 'torso_g', 'lthigh_g', 'rthigh_g', 'lshin_g', 'rshin_g', *skirts.FLAP_CHAIN,
                                 *skirts.FRONT_CHAIN, *skirts.CLOAK_BONES):
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
        sources = {**self.current, 'pfh0_pelvis035': (ROOT / 'sw_pt_pelvis/pfh0_pelvis035.mdl').read_bytes()}
        # Shoulder aliases stay listed after their bone mapping changes; only those the render table names are live.
        shoulders = {row.split()[3] for row in (ROOT / 'sw_2da/shoulderrender.2da').read_text().splitlines()[3:] if len(row.split()) == 4}
        aliases = {**json.loads((ROOT / 'tools/PartSkinModels.json').read_text())['models'],
                   **{alias: entry for alias, entry in json.loads((ROOT / 'tools/ShoulderModels.json').read_text())['models'].items()
                      if alias in shoulders}}
        necks = json.loads((ROOT / 'tools/NeckModels.json').read_text())['aliases']
        found = set()
        for alias, entry in [*aliases.items(), *((alias, {**entry, 'path': f'sw_pt_neck/{alias}.mdl'}) for alias, entry in necks.items())]:
            if entry['source'] not in sources:
                continue
            found.add(entry['source'])
            with self.subTest(alias=alias):
                data = (ROOT / entry['path']).read_bytes()
                self.assertEqual(skin_weight_rows(sources[entry['source']]), skin_weight_rows(data))
        # Robe phenotypes cover every rebound skirt; only the human male shoulders and the neck capes have aliases.
        self.assertLessEqual({name for name in self.models if not self.shape(name)['cape']} | {'pfh0_pelvis035'}, found)

    def test_skirts_swing_like_cloth_in_walk_and_run(self):
        """A skirt must follow the stride (not freeze), keep the legs covered where the original did, keep a
        closed front in front of the legs, not tear, not stand out behind like a shell and not tent round the legs."""
        for name, entry in self.models.items():
            source, current = self.sources[name], self.current[name]
            shape = self.shape(name)
            if shape['cape']:
                continue
            animated = skirts.flaps_animated(skirts.skeleton(source)[1])
            for clip in ('walk', 'run'):
                with self.subTest(model=name, clip=clip):
                    swing, exposed, stretch = stride_metrics(source, current, clip)
                    # A skirt that ends above the knee has little hem to swing.
                    if hem_below_knee(source):
                        self.assertGreater(swing, MIN_SWING)
                    self.assertLess(stretch, MAX_STRETCH)
                    if animated:
                        self.assertLess(exposed, MAX_EXPOSED)
                        self.assertLess(back_lean(source, current, clip), MAX_RUN_BACK)
                        if not shape['closed']:
                            self.assertLess(tent(source, current, clip), MAX_TENT)
                    else:
                        self.assertLess(exposed, max(stride_metrics(source, source, clip)[1] + EXPOSED_MARGIN, MAX_EXPOSED_STILL))
                    if shape['front']:
                        self.assertLess(front_clipping(source, current, clip), MAX_FRONT)

    def test_capes_swing_behind_the_legs(self):
        """A cape on the cloak bones must keep the legs covered from behind and not tear where the original split."""
        for name in self.models:
            if not self.shape(name)['cape']:
                continue
            source, current = self.sources[name], self.current[name]
            for clip in ('walk', 'run'):
                with self.subTest(model=name, clip=clip):
                    swing, exposed, stretch = stride_metrics(source, current, clip)
                    self.assertGreater(swing, MIN_SWING)
                    self.assertLess(exposed, MAX_EXPOSED)
                    self.assertLess(stretch, MAX_STRETCH)

    def test_skirt_hangs_beside_the_legs_when_standing(self):
        """In the standing idles the hips sway to one side and the left leg stands forward; the skirt must stay
        beside the legs, nearly as wide and centred as the original, and its back must not lean out like a shell."""
        for name in self.models:
            if self.shape(name)['cape']:
                continue
            source, current = self.sources[name], self.current[name]
            if not skirts.flaps_animated(skirts.skeleton(source)[1]):
                # Where the flaps never move the legs carry the skirt, so it stays close to the original.
                for clip, limit in (('pause1', MAX_STILL_SHIFT), ('custom52lp', MAX_STILL_PARADE_SHIFT)):
                    with self.subTest(model=name, clip=clip):
                        for before, after in zip(idle_gaps(source, source, clip), idle_gaps(source, current, clip), strict=True):
                            self.assertLess(abs(after - before), limit)
                continue
            for clip, narrowing, shift in (('pause1', MAX_IDLE_NARROWING, MAX_IDLE_SHIFT), ('pause2', MAX_IDLE_NARROWING, MAX_IDLE_SHIFT),
                                           ('custom52lp', MAX_PARADE_NARROWING, MAX_PARADE_SHIFT)):
                with self.subTest(model=name, clip=clip):
                    (left, right), (left0, right0) = idle_gaps(source, current, clip), idle_gaps(source, source, clip)
                    self.assertLess(left0 - left, narrowing)
                    self.assertLess(right0 - right, narrowing)
                    self.assertLess(abs((left - right) - (left0 - right0)), shift)
                    self.assertLess(back_lean(source, current, clip) - back_lean(source, source, clip), MAX_BACK_LEAN)


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


def hem_below_knee(source: bytes) -> bool:
    """Whether the skirt reaches below the knee joint in the bind pose."""
    bones, body = skirts.skeleton(source)
    knee = body.pose()['lshin_g'][0][2]
    weights = [skirts.influences(source, skin, vertex) for skin in skirts.skins(source, bones) for vertex in range(skin['count'])]
    return any(skirts.LEG_BONES & set(current) and position[2] < knee for current, position in zip(weights, skirts.rest_positions(source)))


def held_clip(root: str, clip_name: str, before: str = 'pause1'):
    """A clip in which joints it carries no tracks for hold the pose of the clip before it, as the engine does."""
    clip, previous = animation_clip(root, clip_name), animation_clip(root, before)
    tracked = {node[2] for node in clip[1] if node[3]}
    return clip[0], [*(node for node in previous[1] if node[3] and node[2] not in tracked), *clip[1]]


def idle_gaps(source: bytes, current: bytes, clip_name: str = 'pause1'):
    """How far the hem reaches past the left and the right foot when standing still (m)."""
    bones, _ = skirts.skeleton(source)
    body = Model(tint.find_active_models()[bones[0]].read_bytes(), False)
    skirt = [bool(skirts.LEG_BONES & set(weights)) for weights in
             (skirts.influences(source, skin, vertex) for skin in skirts.skins(source, bones) for vertex in range(skin['count']))]
    rest = skirts.rest_positions(source)
    floor = min(rest[index][2] for index, flag in enumerate(skirt) if flag)
    hem = [index for index, flag in enumerate(skirt) if flag and rest[index][2] < floor + .2]
    pose = body.pose(held_clip(bones[0], clip_name), 0.)
    posed = skirts.rest_positions(current, pose)
    sides = [posed[index][0] for index in hem]
    return pose['lfoot_g'][0][0] - min(sides), max(sides) - pose['rfoot_g'][0][0]


def front_clipping(source: bytes, current: bytes, clip_name: str, phases: int = 8):
    """Share of leg samples in front of the hips that poke out in front of the skirt."""
    bones, _ = skirts.skeleton(source)
    body = Model(tint.find_active_models()[bones[0]].read_bytes(), False)
    clip = animation_clip(bones[0], clip_name)
    skirt = [index for index, weights in enumerate(skirts.influences(source, skin, vertex) for skin in skirts.skins(source, bones)
                                                     for vertex in range(skin['count'])) if skirts.LEG_BONES & set(weights)]
    samples = shown = 0
    for phase in range(phases):
        pose = body.pose(clip, clip[0] * phase / phases)
        posed = skirts.rest_positions(current, pose)
        origin, turn, _ = pose['pelvis_g']
        inverse = (-turn[0], -turn[1], -turn[2], turn[3])
        local = [rotate(inverse, tuple(a - b for a, b in zip(posed[index], origin))) for index in skirt]
        for side in 'lr':
            thigh, shin = pose[f'{side}thigh_g'][0], pose[f'{side}shin_g'][0]
            for step in range(1, 5):
                sample = tuple(a + (b - a) * step / 5 for a, b in zip(thigh, shin))
                point = rotate(inverse, tuple(a - b for a, b in zip(sample, origin)))
                if point[1] < 0:
                    continue  # behind the hips
                bearing = math.atan2(point[0], -point[1])
                radii = [math.hypot(q[0], q[1]) for q in local if abs(q[2] - point[2]) < .06 and q[1] > 0
                         and abs((math.atan2(q[0], -q[1]) - bearing + math.pi) % math.tau - math.pi) < math.radians(15)]
                if radii:
                    samples += 1
                    shown += math.hypot(point[0], point[1]) + .04 > max(radii)
    return shown / max(samples, 1)


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


def skirt_vertices(source: bytes) -> list[int]:
    bones, _ = skirts.skeleton(source)
    weights = (skirts.influences(source, skin, vertex) for skin in skirts.skins(source, bones) for vertex in range(skin['count']))
    return [index for index, current in enumerate(weights) if skirts.LEG_BONES & set(current)]


def back_lean(source: bytes, current: bytes, clip_name: str) -> float:
    """Mean angle from upright of the back panel, from just below the hips to the hem, in degrees (outward positive).

    Standing clips are sampled at their start, as the engine holds them; others over eight phases."""
    bones, bind = skirts.skeleton(source)
    body = Model(tint.find_active_models()[bones[0]].read_bytes(), False)
    pose = bind.pose()
    rest = skirts.rest_positions(source)
    skirt = skirt_vertices(source)
    hip = pose['lthigh_g'][0][2]
    floor = min(rest[index][2] for index in skirt)
    back = [index for index in skirt if abs(skirts.bearing(rest[index], pose)) < 25]
    upper = [index for index in back if hip - .25 < rest[index][2] < hip - .1]
    hem = [index for index in back if rest[index][2] < floor + .15]
    standing = clip_name.startswith(('pause', 'custom'))
    clip = held_clip(bones[0], clip_name)
    angles = []
    for phase in range(1 if standing else 8):
        posed = skirts.rest_positions(current, body.pose(clip, clip[0] * phase / 8))
        mean = lambda indices, axis: sum(posed[index][axis] for index in indices) / len(indices)
        angles.append(math.degrees(math.atan2(mean(upper, 1) - mean(hem, 1), mean(upper, 2) - mean(hem, 2))))
    return sum(angles) / len(angles)


def tent(source: bytes, current: bytes, clip_name: str, phases: int = 8) -> float:
    """How far the skirt reaches in front of the root below the knees, on average over the clip (m)."""
    bones, bind = skirts.skeleton(source)
    body = Model(tint.find_active_models()[bones[0]].read_bytes(), False)
    knee = bind.pose()['lshin_g'][0][2]
    skirt = skirt_vertices(source)
    clip = animation_clip(bones[0], clip_name)
    reach = []
    for phase in range(phases):
        pose = body.pose(clip, clip[0] * phase / phases)
        posed = skirts.rest_positions(current, pose)
        ahead = [posed[index][1] - pose['rootdummy'][0][1] for index in skirt if posed[index][2] < knee]
        if ahead:
            reach.append(max(ahead))
    return sum(reach) / len(reach)


if __name__ == '__main__':
    unittest.main()
