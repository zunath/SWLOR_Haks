"""Hang the coat skirts of chest parts 154 and 269 from the thighs and the back belt flap.

Both chest parts carry an ankle-length coat skirt. Each half of the skirt was weighted
to its own thigh and shin (chest 269: a third torso, two thirds legs), so walking and
running pulled the halves apart: the back of the coat tore open and the legs showed
through it. Moving those weights onto the pelvis (October 2026) stopped the tearing
but left the coat frozen while the legs strode through it. Hanging most of the coat
from the body's stock back belt flap (fb1_g01, fb2_g01, fb4_g01), which the walk, run,
sit, kneel, knockdown and ability clips swing like coat tails, made it swing again. But
the standing idles sway the hips to one side and lean the flap back, so the coat hung
off-centre, tucked behind one leg and angled back instead of falling beside the legs.
A chest part only reaches the normal body's bones, so robe 236's coat helpers are out
of reach.

Each leg-weighted vertex is now weighted by its bind height:
- Above the hip joints it keeps the pelvis binding (leg weight moved onto the pelvis),
  so the waist cannot tear.
- From the hip joints down to the middle flap joint it eases onto the thigh on its own
  side of the body. Near the midline the two thighs share a vertex (SEAM), so the
  halves move together where they meet. The legs stay under the body in every idle,
  so the coat falls beside them as the original did.
- Below the middle flap joint the back of the coat also hangs from the two lower flap
  joints: fully at the back, easing toward the sides and front (FALLOFF), scaled per
  style (FLAP_STRENGTH). That share swings the coat out behind striding legs.
Below the hips the weights depend only on the bind position, so neighbouring vertices
move together even where the source weights were uneven. Every vertex keeps at most four bones and its source weight sum; the geometry, inverse
binds and bone tables stay unchanged, so the bind shape is identical. Clips that carry
no flap tracks (a few stock emotes and turns) hold the flap's previous pose, as stock
tabards do. The dwarf and half-orc roots number their flap joints apart from a_ba, so
the engine never swings their flap; on those bodies the coat below the hips hangs two
thirds from the thighs and a third from the pelvis (flaps_animated, STILL_THIGHS).

The originals come from the Git blobs recorded in ChestSkirtBindings.json, so --apply
is repeatable. Replacing one of these models means recording its new source blob.
Re-run GeneratePartSkinModels.py --apply afterwards: robe-body aliases copy these weights.
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
# The body's back belt flap, top to bottom. The coat hangs from the lower two joints.
FLAP_CHAIN = ('fb1_g01', 'fb2_g01', 'fb4_g01')
# The clips that swing the flap. The engine pairs their tracks with the wearer's joints by part ID.
FLAP_ANIMATIONS = ROOT / 'sw_cr_creature' / 'a_ba.mdl'
# Degrees from the back at which the flap share would reach zero.
FALLOFF = 150.
# How strongly each style follows the flap. Chest 269's tail is one flat panel; at full
# strength it trailed almost level behind runners, so it takes 60% (client-reviewed).
FLAP_STRENGTH = {'154': 1., '269': .6}
# Metres either side of the body's midline over which a vertex passes from one thigh to
# the other: wide enough that the halves move together where they meet.
SEAM = .25
# Below the hips, the share of a vertex the thighs carry on bodies whose flap never moves;
# the pelvis carries the rest. Where the flap swings, the thighs and the flap carry it all.
STILL_THIGHS = .67


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


def flap_strength(path: str) -> float:
    return FLAP_STRENGTH[re.search(r'_chest(\d{3})\.mdl$', path)[1]]


def heights(pose: dict) -> tuple[float, float, float]:
    """The hip joints, where the coat starts to follow the thighs, and the two lower flap joints."""
    hip, top, bottom = pose['lthigh_g'][0][2], pose[FLAP_CHAIN[1]][0][2], pose[FLAP_CHAIN[2]][0][2]
    if not hip > top > bottom:
        raise ValueError('Expected the hip joints above the lower flap joints')
    return hip, top, bottom


def left_share(position: tuple[float, float, float], pose: dict) -> float:
    """How much of a vertex follows the left thigh: all on the left, none on the right, half on the midline."""
    return 1 - smoothstep((position[0] - pose['pelvis_g'][0][0] + SEAM) / (2 * SEAM))


def flap_share(position: tuple[float, float, float], pose: dict) -> float:
    """The share of a skirt vertex carried by the flap, from its bind position."""
    pelvis = pose['pelvis_g'][0]
    _, top, bottom = heights(pose)
    behind = math.degrees(math.atan2(abs(position[0] - pelvis[0]), pelvis[1] - position[1]))
    return (1 - smoothstep(behind / FALLOFF)) * smoothstep((top - position[2]) / (top - bottom))


def flap_bones(height: float, pose: dict) -> dict[str, float]:
    """Split a flap share between the two lower flap joints by the vertex height."""
    _, top, bottom = heights(pose)
    lower = min(1., max(0., (top - height) / (top - bottom)))
    return {bone: weight for bone, weight in ((FLAP_CHAIN[1], 1 - lower), (FLAP_CHAIN[2], lower)) if weight}


def flaps_animated(body) -> bool:
    """Whether the walk and run clips reach the wearer's flap joints.

    The dwarf and half-orc roots number their flap joints apart from a_ba, so the
    engine never moves their flaps; a coat hung from them would stay rigid anyway.
    """
    parts = lambda model: {node[0]: node[2] for node in model.nodes if node[0] in FLAP_CHAIN}
    expected = parts(body_model(FLAP_ANIMATIONS.read_bytes()))
    return len(expected) == len(FLAP_CHAIN) and parts(body) == expected and -1 not in expected.values()


def skirt_weights(current: dict[str, float], position: tuple[float, float, float], pose: dict,
                  animated: bool = True, strength: float = 1.) -> dict[str, float]:
    """Hang a leg-weighted vertex from the pelvis at the waist, its own thigh below the hips and, at the back, the flap."""
    if not LEG_BONES & set(current):
        return current
    # Keep the source's weight sum: some exports are a little off one, and the bind shape scales with it.
    total = sum(current.values())
    hip, top, _ = heights(pose)
    thighs = smoothstep((hip - position[2]) / (hip - top))
    upper = hip_weights(current)
    if thighs and 'rootdummy' in upper:
        # The root and the pelvis share a pivot; folding one into the other keeps the hip band within four bones.
        upper = {bone: weight for bone, weight in upper.items() if bone != 'rootdummy'}
        upper['pelvis_g'] = upper.get('pelvis_g', 0.) + current['rootdummy']
    left = left_share(position, pose)
    # Below the hips the source weights give way to a blend that depends only on the bind position.
    moved = thighs * total * (1. if animated else STILL_THIGHS)
    result = {bone: weight * (1 - thighs) for bone, weight in upper.items()}
    if not animated:
        result['pelvis_g'] = result.get('pelvis_g', 0.) + thighs * total * (1 - STILL_THIGHS)
    result['lthigh_g'] = result.get('lthigh_g', 0.) + moved * left
    result['rthigh_g'] = result.get('rthigh_g', 0.) + moved * (1 - left)
    share = flap_share(position, pose) * strength if animated else 0.
    result = {bone: weight * (1 - share) for bone, weight in result.items()}
    for bone, weight in flap_bones(position[2], pose).items():
        result[bone] = result.get(bone, 0.) + share * total * weight
    result = {bone: weight for bone, weight in result.items() if weight > 1e-7}
    if len(result) > 4:
        raise ValueError(f'Skirt vertex at {position} needs {len(result)} bones: {sorted(result)}')
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def rebind(data: bytes, strength: float = 1.) -> tuple[bytes, int]:
    bones, body = skeleton(data)
    pose = body.pose()
    animated = flaps_animated(body)
    rest = iter(rest_positions(data))
    result = bytearray(data)
    changed = 0
    for skin in skins(data, bones):
        bone_map = skin['bone_map']
        slots = {bone: slot for slot, bone in enumerate(skin['bones']) if bone is not None and bone_map[bones.index(bone)] == slot}
        free = [slot for slot in range(17) if slot not in bone_map]
        for vertex in range(skin['count']):
            current = influences(data, skin, vertex)
            target = skirt_weights(current, next(rest), pose, animated, strength)
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


def build(entry: dict) -> bytes:
    original = source(entry['sourceBlob'])
    rebound, changed = rebind(original, flap_strength(entry['path']))
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
    if args.check and stale:
        raise ValueError(f'Chest skirt bindings are stale: {stale}; run ChestSkirtBindings.py --apply')
    if args.apply and stale:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"{'Rebound' if args.apply else 'Validated'} {len(manifest['models'])} chest skirt models"
          f"{f' ({len(stale)} updated)' if args.apply else ''}.")


if __name__ == '__main__':
    main()
