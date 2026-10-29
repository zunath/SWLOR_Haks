"""Hang long skirts and capes of body parts (STYLES) from the pelvis, the belt flaps and the cloak bones.

Chest, belt and pelvis parts carry coat skirts, skirt belts or skirts in the part mesh,
and neck 291 and shoulders 260 a full-length cape. Each half of a skirt or cape was
weighted to its own thigh and shin, so walking and running pulled the halves apart: the
back tore open and the legs showed through it. A body part only reaches the normal
body's bones, so robe 236's coat helpers are out of reach.

Skirts (October 2026, four test-server rounds):
- Pelvis only (chest 154, 269): no tear, but a frozen shell the legs strode through.
- Pelvis plus the stock back belt flap (fb1_g01, fb2_g01, fb4_g01), which the walk, run,
  sit, kneel, knockdown and ability clips swing like coat tails: it moved like fabric,
  but stood off-centre in the idles. The flaps hang from rootdummy, not the pelvis, and
  the stock idles stand in a contrapposto (pause1: the root 7 cm to the right of the
  feet, the left foot 10 cm forward and out, the right one back), so a coat that ignores
  the legs hangs to one side with its left panel behind the forward leg.
- Each side on its own leg below the hips, the back seam on both thighs and the flap
  below: centred, but the back's upper band followed the thighs while its lower part
  hung from the flap, so it bent at the buttocks into a stiff shell, and in a run the
  side panels followed the striding legs into a tent while the back flew out level.
Now the back keeps the flap-and-pelvis binding that moved like fabric, and only a share
of each side panel follows its own leg (LEGS): enough to centre the coat and bring the
side panel forward with the leg in the idles, little enough that the flap still carries
the coat behind runners. Each leg-weighted vertex is weighted by its bind position:
- Above the hip joints it keeps the pelvis binding (leg weight moved onto the pelvis),
  so the waist cannot tear.
- From the hip joints down to the middle back flap joint it eases onto the pelvis and,
  away from a seam (SEAM to SIDE degrees round from the back, and from the front of a
  skirt closed in front), a LEGS share of its own leg: the thigh, and below the knee
  the shin (KNEE_BAND). Vertices near the body's axis (AXIS) have no bearing to speak
  of and stay on the pelvis.
- Over the same band the back flap takes a share that is full at the back and fades to
  nothing round the body (FALLOFF), and a closed front hangs from the front tabard flap
  (FRONT_CHAIN, FRONT_FALLOFF), each scaled per style.
Below the hips the weights depend only on the bind position, so neighbouring vertices
move together even where the source weights were uneven.

Capes hang below the shoulders from the stock cloak bones (CLOAK_COLUMNS: three columns
of four joints that the walk, run and idle clips swing like a stock cloak), split by
the vertex's height between the bracketing joints and sideways between the bracketing
columns; above the second row they ease back onto the source's torso binding.

Every vertex keeps at most four bones and its source weight sum; the geometry, inverse
binds and bone tables stay unchanged, so the bind shape is identical. The custom emotes
(/paraderest among them) hold the flaps at rest (FillEmoteFlapTracks.py) but carry no
cloak tracks, so a cape keeps the previous pose there, as stock cloaks do; the few stock
clips without flap tracks (some turns and injured walks) hold the flaps' previous pose too.
The dwarf and half-orc roots number their flap and cloak joints apart from a_ba, so the
engine never moves them; on those bodies the legs carry the share the source gave a skirt
on average below the hips and the pelvis the rest (flaps_animated, still_share), and capes
are not listed.

Belt 226 and pelvis 198 stay as originally rigged at the test server's request; the
manifest's "restored" list holds their original blobs, and --check keeps them so.

The originals come from the Git blobs recorded in ChestSkirtBindings.json, so --apply
is repeatable. Replacing one of these models means recording its new source blob.
Re-run GeneratePartSkinModels.py, GenerateShoulderModels.py and GenerateNeckModels.py
with --apply afterwards: robe-body aliases copy these weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import struct
import subprocess

import CompileModels as mdl
from NeckSkinBindings import body_model
from RobePoseAudit import rotate

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools' / 'ChestSkirtBindings.json'
LEG_BONES = frozenset({'lthigh_g', 'rthigh_g', 'lshin_g', 'rshin_g', 'lfoot_g', 'rfoot_g'})
# The body's back belt flap, top to bottom. A coat hangs from the lower two joints.
FLAP_CHAIN = ('fb1_g01', 'fb2_g01', 'fb4_g01')
# The body's front tabard flap, top to bottom. A closed skirt's front hangs from the lower two.
FRONT_CHAIN = ('tf1_g', 'tf2_g', 'tf3_g01')
# The body's cloak bones, left, middle and right columns, top to bottom. Capes hang from them.
CLOAK_COLUMNS = (('cl1_g', 'cl2_g', 'cl3_g', 'cl4_g'), ('cm1_g', 'cm2_g', 'cm3_g', 'cm4_g'),
                 ('cr1_g', 'cr2_g', 'cr3_g', 'cr4_g'))
CLOAK_BONES = frozenset(bone for column in CLOAK_COLUMNS for bone in column)
# The clips that swing the flaps and cloak bones. The engine pairs their tracks with the wearer's joints by part ID.
FLAP_ANIMATIONS = ROOT / 'sw_cr_creature' / 'a_ba.mdl'
# Degrees from the back at which the back flap share reaches zero: from the back round to the
# front, as in the flap binding the test server liked, so the side panels trail with the back.
FALLOFF = 180.
# Degrees from the front at which the front flap share reaches zero (per style "front_falloff"). A
# closed skirt fades the back flap out before the front one starts (its style's "falloff"), so no
# vertex needs both flaps.
FRONT_FALLOFF = 60.
# Each style's share of the back flap ("back") and the front flap ("front") at full, and
# whether the skirt meets in front ("closed"), which gives the front a seam too; "falloff" and
# "front_falloff" override FALLOFF and FRONT_FALLOFF, and "still_seam" the seam where the flaps
# never move (degrees).
# Chest 269's tail is one flat panel, and belts 019, 112 and 114 are long skirts flared at the
# back; at full strength they stood out almost level behind runners, so they take 60% (client-reviewed).
# "cape" marks capes, which hang from the cloak bones instead.
STYLES = {
    'chest154': {'back': 1.},
    'chest269': {'back': .6},
    'chest028': {'back': 1.},
    'chest059': {'back': 1.},
    'chest310': {'back': 1.},
    'belt019': {'back': .6},
    'belt110': {'back': 1.},
    'belt112': {'back': .6},
    'belt114': {'back': .6},
    # A skirt closed all round. Its front hangs from the front flap 80 degrees round, so the striding
    # legs stay behind it, and its back flap fades out before that; without flaps its front seam
    # needs more width than an open coat's.
    'pelvis035': {'back': 1., 'front': 1., 'closed': True, 'falloff': 100., 'front_falloff': 80., 'still_seam': 35.},
    'neck291': {'cape': True},
    'shol260': {'cape': True},
    'shor260': {'cape': True},
}
# Where the flaps swing: degrees round from a seam (the back, and the front of a closed skirt)
# within which the lower skirt stays on the pelvis and the flaps, and beyond which a LEGS share
# of it follows its own leg. That share centres the skirt over the feet and brings a side
# panel forward with the leg in the standing idles; all of it turned the coat into a tent
# around striding legs (client-reviewed).
SEAM = 30.
SIDE = 90.
LEGS = .6
# Metres from the body's axis within which a vertex has no meaningful bearing and stays on the pelvis.
AXIS = .1
# Where the flaps never move: degrees either side of a seam over which a vertex passes from
# one leg to the other. Nothing swings the back out of the way, so each half follows its own
# leg soon after the seam.
STILL_SEAM = 25.
# Metres below the knee joint over which a side panel passes from the thigh to the shin.
KNEE_BAND = .15
# Where the flaps never move, the legs carry below the hips the share the source gave
# them on average there (still_share) and the pelvis the rest.


def skeleton(data: bytes) -> tuple[list[str], object]:
    name = mdl.external_skeleton_name(data)
    if name is None:
        raise ValueError('Expected an externally skinned body part')
    body = body_model((ROOT / 'sw_pt_root' / f'{name}.mdl').read_bytes())
    return [node[0] for node in body.nodes], body


def skins(data: bytes, bones: list[str]):
    """Yield each skin's vertex count, layout pointers, palette bone names and node-to-slot map."""
    uint = lambda offset: struct.unpack_from('<I', data, offset)[0]
    raw = 12 + uint(4)
    for name, _, mesh in mdl.binary_nodes(data):
        if not mesh or 'weights' not in mesh:
            continue
        offset = mesh['_mesh_offset']
        skin = offset + 512
        palette = struct.unpack_from('<17h', data, skin + 64)
        bone_map_offset, count = 12 + uint(skin + 20), uint(skin + 24)
        if count != len(bones):
            raise ValueError(f'{name}: external bone table does not match the wearer skeleton')
        yield {'name': name, 'count': struct.unpack_from('<H', data, offset + 448)[0], 'stride': uint(offset + 440),
               'positions': raw + uint(offset + 444), 'weights': raw + uint(skin + 12),
               'indices': raw + uint(skin + 16), 'palette': palette, 'palette_offset': skin + 64,
               'bone_map': struct.unpack_from(f'<{count}h', data, bone_map_offset), 'bone_map_offset': bone_map_offset,
               'bones': [bones[bone] if 0 <= bone < len(bones) else None for bone in palette],
               'inverse_rotations': 12 + uint(skin + 28), 'inverse_translations': 12 + uint(skin + 40)}


def influences(data: bytes, skin: dict, vertex: int) -> dict[str, float]:
    weights = struct.unpack_from('<4f', data, skin['weights'] + vertex * (skin['stride'] or 16))
    slots = struct.unpack_from('<4h', data, skin['indices'] + vertex * (skin['stride'] or 8))
    result = {}
    for weight, slot in zip(weights, slots):
        if not weight:
            continue
        if not math.isfinite(weight) or weight < 0 or not 0 <= slot < 17 or skin['bones'][slot] is None:
            raise ValueError(f"{skin['name']}: invalid skin weight")
        result[skin['bones'][slot]] = result.get(skin['bones'][slot], 0.) + weight
    if abs(sum(result.values()) - 1) > .0021:
        raise ValueError(f"{skin['name']}: weights do not sum to one")
    return result


def hip_weights(current: dict[str, float]) -> dict[str, float]:
    """Move every leg weight onto the pelvis."""
    released = sum(weight for bone, weight in current.items() if bone in LEG_BONES)
    if not released:
        return current
    result = {bone: weight for bone, weight in current.items() if bone not in LEG_BONES}
    result['pelvis_g'] = result.get('pelvis_g', 0.) + released
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def smoothstep(value: float) -> float:
    value = min(1., max(0., value))
    return value * value * (3 - 2 * value)


def style_key(path: str) -> str:
    match = re.search(r'_(chest|belt|pelvis|neck|shol|shor)(\d{3})\.mdl$', path.lower())
    if not match:
        raise ValueError(f'{path}: not a chest, belt, pelvis, neck or shoulder part')
    return match[1] + match[2]


def style(path: str) -> dict:
    """The flap shares and front shape of a part, from its file name."""
    return {'back': 1., 'front': 0., 'closed': False, 'falloff': FALLOFF, 'cape': False, **STYLES[style_key(path)]}


def heights(pose: dict, chain: tuple[str, str, str] = FLAP_CHAIN) -> tuple[float, float, float]:
    """The hip joints, where the skirt leaves the waist binding, and a flap's two lower joints."""
    hip, top, bottom = pose['lthigh_g'][0][2], pose[chain[1]][0][2], pose[chain[2]][0][2]
    if not hip > top > bottom:
        raise ValueError('Expected the hip joints above the lower flap joints')
    return hip, top, bottom


def bearing(position: tuple[float, float, float], pose: dict) -> float:
    """Degrees around the body from the back: positive toward the right side, +-180 at the front."""
    pelvis = pose['pelvis_g'][0]
    return math.degrees(math.atan2(position[0] - pelvis[0], pelvis[1] - position[1]))


def axis_share(position: tuple[float, float, float], pose: dict) -> float:
    """None on the body's axis, where a vertex has no meaningful bearing, rising to all AXIS from it."""
    pelvis = pose['pelvis_g'][0]
    return smoothstep(math.hypot(position[0] - pelvis[0], position[1] - pelvis[1]) / AXIS)


def leg_share(position: tuple[float, float, float], pose: dict, closed: bool = False) -> float:
    """Where the flaps swing, how much of a lower skirt vertex follows its own leg: none near a seam, LEGS at the sides."""
    angle = abs(bearing(position, pose))
    away = min(angle, 180 - angle) if closed else angle
    return LEGS * smoothstep((away - SEAM) / (SIDE - SEAM)) * axis_share(position, pose)


def left_share(position: tuple[float, float, float], pose: dict, closed: bool = False, seam: float = STILL_SEAM) -> float:
    """Where the flaps never move, how much of a vertex follows the left leg: all on the left, none on the right, half on a seam."""
    angle = bearing(position, pose)
    if closed and abs(angle) > 90:
        angle = math.copysign(180 - abs(angle), angle)
    return 1 - smoothstep((angle + seam) / (2 * seam))


def knee_share(position: tuple[float, float, float], pose: dict) -> float:
    """How much of a leg share follows the shin rather than the thigh: none above the knee, all KNEE_BAND below it."""
    return smoothstep((pose['lshin_g'][0][2] - position[2]) / KNEE_BAND)


def shin_share(position: tuple[float, float, float], pose: dict, closed: bool = False, seam: float = STILL_SEAM) -> float:
    """Where the flaps never move, how much of a side panel's leg share follows the shin: none above the knee or near a seam."""
    angle = abs(bearing(position, pose))
    away = min(angle, 180 - angle) if closed else angle
    return smoothstep((away - seam) / seam) * knee_share(position, pose)


def flap_share(position: tuple[float, float, float], pose: dict, falloff: float = FALLOFF) -> float:
    """The share of a skirt vertex carried by the back flap, from its bind position."""
    hip, top, _ = heights(pose)
    behind = abs(bearing(position, pose))
    return (1 - smoothstep(behind / falloff)) * smoothstep((hip - position[2]) / (hip - top)) * axis_share(position, pose)


def front_share(position: tuple[float, float, float], pose: dict, falloff: float = FRONT_FALLOFF) -> float:
    """The share of a skirt vertex carried by the front flap, from its bind position."""
    hip, top, _ = heights(pose, FRONT_CHAIN)
    ahead = 180 - abs(bearing(position, pose))
    return (1 - smoothstep(ahead / falloff)) * smoothstep((hip - position[2]) / (hip - top)) * axis_share(position, pose)


def flap_bones(height: float, pose: dict, chain: tuple[str, str, str] = FLAP_CHAIN) -> dict[str, float]:
    """Split a flap share between a flap's two lower joints by the vertex height."""
    _, top, bottom = heights(pose, chain)
    lower = min(1., max(0., (top - height) / (top - bottom)))
    return {bone: weight for bone, weight in ((chain[1], 1 - lower), (chain[2], lower)) if weight}


def animated_joints(body, joints) -> bool:
    """Whether the walk and run clips reach these joints of the wearer.

    The dwarf and half-orc roots number their flap and cloak joints apart from a_ba, so
    the engine never moves them; a skirt or cape hung from them would stay rigid anyway.
    """
    parts = lambda model: {node[0]: node[2] for node in model.nodes if node[0] in joints}
    expected = parts(body_model(FLAP_ANIMATIONS.read_bytes()))
    return len(expected) == len(joints) and parts(body) == expected and -1 not in expected.values()


def flaps_animated(body) -> bool:
    """Whether the walk and run clips reach the wearer's back and front flap joints."""
    return animated_joints(body, {*FLAP_CHAIN, *FRONT_CHAIN})


def cloak_animated(body) -> bool:
    """Whether the walk and run clips reach the wearer's cloak bones."""
    return animated_joints(body, CLOAK_BONES)


def waist_weights(current: dict[str, float], lower: float) -> dict[str, float]:
    """The source binding with its leg weight on the pelvis; below the hips the root folds into the pelvis too."""
    upper = hip_weights(current)
    if lower and 'rootdummy' in upper:
        # The root and the pelvis share a pivot; folding one into the other keeps the hip band within four bones.
        upper = {bone: weight for bone, weight in upper.items() if bone != 'rootdummy'}
        upper['pelvis_g'] = upper.get('pelvis_g', 0.) + current['rootdummy']
    return upper


def swing_weights(current: dict[str, float], position: tuple[float, float, float], pose: dict, shape: dict) -> dict[str, float]:
    """Where the flaps swing: the pelvis at the waist, then the pelvis and the flaps, with a share of its own leg at the sides."""
    total = sum(current.values())
    hip, top, _ = heights(pose)
    lower = smoothstep((hip - position[2]) / (hip - top))
    result = {bone: weight * (1 - lower) for bone, weight in waist_weights(current, lower).items()}
    legs = lower * total * leg_share(position, pose, shape['closed'])
    result['pelvis_g'] = result.get('pelvis_g', 0.) + lower * total - legs
    side = 'l' if bearing(position, pose) < 0 else 'r'
    shin = knee_share(position, pose)
    for bone, weight in ((f'{side}thigh_g', 1 - shin), (f'{side}shin_g', shin)):
        result[bone] = result.get(bone, 0.) + legs * weight
    flaps = [(flap_share(position, pose, shape['falloff']) * shape['back'], FLAP_CHAIN)]
    if shape['front']:
        flaps.append((front_share(position, pose, shape.get('front_falloff', FRONT_FALLOFF)) * shape['front'], FRONT_CHAIN))
    for share, chain in flaps:
        if not share:
            continue
        result = {bone: weight * (1 - share) for bone, weight in result.items()}
        for bone, weight in flap_bones(position[2], pose, chain).items():
            result[bone] = result.get(bone, 0.) + share * total * weight
    return result


def still_weights(current: dict[str, float], position: tuple[float, float, float], pose: dict, shape: dict) -> dict[str, float]:
    """Where the flaps never move: the pelvis at the waist, below the hips the legs' average source share and the pelvis."""
    total = sum(current.values())
    hip, top, _ = heights(pose)
    thighs = smoothstep((hip - position[2]) / (hip - top))
    seam = shape.get('still_seam', STILL_SEAM)
    left = left_share(position, pose, shape['closed'], seam)
    moved = thighs * total * shape['still']
    result = {bone: weight * (1 - thighs) for bone, weight in waist_weights(current, thighs).items()}
    result['pelvis_g'] = result.get('pelvis_g', 0.) + thighs * total * (1 - shape['still'])
    # Below the knee the side panels follow the shin, so they lie along the lower leg when it bends.
    shin = shin_share(position, pose, shape['closed'], seam)
    for side, share in (('l', left), ('r', 1 - left)):
        for bone, weight in ((f'{side}thigh_g', 1 - shin), (f'{side}shin_g', shin)):
            result[bone] = result.get(bone, 0.) + moved * share * weight
    return result


def column_x(column: tuple[str, ...], height: float, pose: dict) -> float:
    """Where a cloak column runs sideways at a height, between its bracketing joints."""
    levels = [pose[bone][0][2] for bone in column]
    for index in range(len(column) - 1):
        if levels[index] >= height >= levels[index + 1]:
            lower = (levels[index] - height) / (levels[index] - levels[index + 1])
            return pose[column[index]][0][0] * (1 - lower) + pose[column[index + 1]][0][0] * lower
    return pose[column[0 if height > levels[0] else -1]][0][0]


def cloak_weights(position: tuple[float, float, float], pose: dict) -> dict[str | None, float]:
    """A cape vertex's cloak bones: the columns either side of it and the joints above and below it.

    Above a column's second joint the key None takes the share that stays on the source's torso binding.
    """
    x, _, height = position
    across = [column_x(column, height, pose) for column in CLOAK_COLUMNS]
    if x <= across[0]:
        columns = [(0, 1.)]
    elif x >= across[-1]:
        columns = [(len(across) - 1, 1.)]
    else:
        index = next(index for index in range(len(across) - 1) if x <= across[index + 1])
        right = (x - across[index]) / (across[index + 1] - across[index])
        columns = [(index, 1 - right), (index + 1, right)]
    result = {}
    for index, share in columns:
        column = CLOAK_COLUMNS[index]
        levels = [pose[bone][0][2] for bone in column]
        if height >= levels[1]:
            lower = smoothstep((levels[0] - height) / (levels[0] - levels[1]))
            parts = [(None, 1 - lower), (column[1], lower)]
        elif height <= levels[-1]:
            parts = [(column[-1], 1.)]
        else:
            joint = max(level for level in range(1, len(column) - 1) if levels[level] >= height)
            lower = (levels[joint] - height) / (levels[joint] - levels[joint + 1])
            parts = [(column[joint], 1 - lower), (column[joint + 1], lower)]
        for bone, weight in parts:
            if share * weight:
                result[bone] = result.get(bone, 0.) + share * weight
    return result


def cape_weights(current: dict[str, float], position: tuple[float, float, float], pose: dict) -> dict[str, float]:
    """Hang a leg-weighted cape vertex from the cloak bones, easing back onto the source's torso binding at the top."""
    total = sum(current.values())
    torso = {bone: weight for bone, weight in current.items() if bone not in LEG_BONES}
    torso['torso_g'] = torso.get('torso_g', 0.) + sum(weight for bone, weight in current.items() if bone in LEG_BONES)
    cloak = cloak_weights(position, pose)
    kept = cloak.pop(None, 0.)
    result = {bone: weight * kept for bone, weight in torso.items()}
    for bone, weight in cloak.items():
        result[bone] = result.get(bone, 0.) + weight * total
    result = {bone: weight for bone, weight in result.items() if weight > 1e-7}
    while len(result) > 4:
        # Near the shoulders an arm weight can push a vertex past four bones; the smallest one goes to the torso.
        extra = min((bone for bone in result if bone != 'torso_g' and bone not in CLOAK_BONES), key=result.get)
        result['torso_g'] += result.pop(extra)
    return result


def skirt_weights(current: dict[str, float], position: tuple[float, float, float], pose: dict,
                  animated: bool = True, shape: dict | None = None) -> dict[str, float]:
    """Rebind a leg-weighted vertex by its bind position: a cape from the cloak bones, a skirt from the pelvis, flaps and legs."""
    if not LEG_BONES & set(current):
        return current
    shape = {'back': 1., 'front': 0., 'closed': False, 'falloff': FALLOFF, 'cape': False, 'still': 1., **(shape or {})}
    if shape['cape']:
        result = cape_weights(current, position, pose) if animated else current
    elif animated:
        result = swing_weights(current, position, pose, shape)
    else:
        result = still_weights(current, position, pose, shape)
    result = {bone: weight for bone, weight in result.items() if weight > 1e-7}
    if len(result) > 4:
        raise ValueError(f'Skirt vertex at {position} needs {len(result)} bones: {sorted(result)}')
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def rebind(data: bytes, shape: dict | None = None) -> tuple[bytes, int]:
    bones, body = skeleton(data)
    pose = body.pose()
    animated = cloak_animated(body) if (shape or {}).get('cape') else flaps_animated(body)
    rest = iter(rest_positions(data))
    result = bytearray(data)
    changed = 0
    for skin in skins(data, bones):
        bone_map = skin['bone_map']
        slots = {bone: slot for slot, bone in enumerate(skin['bones']) if bone is not None and bone_map[bones.index(bone)] == slot}
        free = [slot for slot in range(17) if slot not in bone_map]
        for vertex in range(skin['count']):
            current = influences(data, skin, vertex)
            target = skirt_weights(current, next(rest), pose, animated, shape)
            if target is current:
                continue
            for bone in target:
                if bone in slots:
                    continue
                if not free:
                    raise ValueError(f"{skin['name']}: skin palette has no free slot for {bone}")
                slot, node = free.pop(0), bones.index(bone)
                slots[bone] = slot
                struct.pack_into('<h', result, skin['palette_offset'] + slot * 2, node)
                struct.pack_into('<h', result, skin['bone_map_offset'] + node * 2, slot)
            rows = [*target.items(), *[(None, 0.)] * (4 - len(target))]
            struct.pack_into('<4f', result, skin['weights'] + vertex * (skin['stride'] or 16), *[weight for _, weight in rows])
            struct.pack_into('<4h', result, skin['indices'] + vertex * (skin['stride'] or 8),
                             *[slots[bone] if bone else 0 for bone, _ in rows])
            changed += 1
    return bytes(result), changed


def rest_positions(data: bytes, pose: dict | None = None) -> list[tuple[float, float, float]]:
    """Skin every vertex in the wearer's bind pose, or in another pose of the wearer's skeleton."""
    bones, body = skeleton(data)
    world = list((pose or body.pose()).values())
    result = []
    for skin in skins(data, bones):
        for vertex in range(skin['count']):
            point = struct.unpack_from('<3f', data, skin['positions'] + vertex * (skin['stride'] or 12))
            weights = struct.unpack_from('<4f', data, skin['weights'] + vertex * (skin['stride'] or 16))
            slots = struct.unpack_from('<4h', data, skin['indices'] + vertex * (skin['stride'] or 8))
            position = [0., 0., 0.]
            for weight, slot in zip(weights, slots):
                if not weight:
                    continue
                bone = skin['palette'][slot]
                stored = struct.unpack_from('<4f', data, skin['inverse_rotations'] + bone * 16)
                local = rotate((*stored[1:], stored[0]), point)
                local = tuple(a + b for a, b in zip(local, struct.unpack_from('<3f', data, skin['inverse_translations'] + bone * 12)))
                translation, rotation, scale = world[bone]
                moved = rotate(rotation, tuple(value * scale for value in local))
                for axis in range(3):
                    position[axis] += weight * (translation[axis] + moved[axis])
            result.append(tuple(position))
    return result


def source(blob: str) -> bytes:
    return subprocess.run(['git', '-C', str(ROOT), 'cat-file', 'blob', blob], check=True, capture_output=True).stdout


def still_share(data: bytes) -> float:
    """The average share of the source's skirt weight on the legs below the middle back flap joint, to two places."""
    bones, body = skeleton(data)
    top = heights(body.pose())[1]
    shares = []
    weights = [influences(data, skin, vertex) for skin in skins(data, bones) for vertex in range(skin['count'])]
    for current, position in zip(weights, rest_positions(data), strict=True):
        if LEG_BONES & set(current) and position[2] < top:
            shares.append(sum(weight for bone, weight in current.items() if bone in LEG_BONES) / sum(current.values()))
    return round(sum(shares) / len(shares), 2) if shares else 1.


def shape(path: str, data: bytes) -> dict:
    """A part's style plus the leg share its source gave the skirt, for bodies whose flaps never move."""
    return {**style(path), 'still': still_share(data)}


def build(entry: dict) -> bytes:
    original = source(entry['sourceBlob'])
    rebound, changed = rebind(original, shape(entry['path'], original))
    if not changed:
        raise ValueError(f"{entry['path']}: the source has no leg-weighted skirt")
    error = max(math.dist(a, b) for a, b in zip(rest_positions(original), rest_positions(rebound), strict=True))
    if error > 2e-5:
        raise ValueError(f"{entry['path']}: bind shape moved by {error:.9g} m")
    return rebound


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    stale = []
    for name, entry in manifest['models'].items():
        path = ROOT / entry['path']
        data = build(entry)
        digest = hashlib.sha256(data).hexdigest()
        if path.read_bytes() != data or entry['sha256'] != digest:
            stale.append(name)
            if args.apply:
                path.write_bytes(data)
                entry['sha256'] = digest
    # Parts the test server asked to keep as originally rigged (belt 226, pelvis 198).
    for relative, blob in manifest.get('restored', {}).items():
        data = source(blob)
        if (ROOT / relative).read_bytes() != data:
            stale.append(relative)
            if args.apply:
                (ROOT / relative).write_bytes(data)
    if args.check and stale:
        raise ValueError(f'Skirt bindings are stale: {stale}; run ChestSkirtBindings.py --apply')
    if args.apply and stale:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"{'Rebound' if args.apply else 'Validated'} {len(manifest['models'])} skirt and cape models"
          f" and {len(manifest.get('restored', {}))} restored originals"
          f"{f' ({len(stale)} updated)' if args.apply else ''}.")


if __name__ == '__main__':
    main()
