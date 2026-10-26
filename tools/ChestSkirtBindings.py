"""Hang the coat skirts of chest parts 154 and 269 from the body's back belt flap.

Both chest parts carry an ankle-length coat skirt. Each half of the skirt was weighted
to its own thigh and shin (chest 269: a third torso, two thirds legs), so walking and
running pulled the halves apart: the back of the coat tore open and the legs showed
through it. Moving those weights onto the pelvis (October 2026) stopped the tearing
but left the coat frozen while the legs strode through it. A chest part only reaches
the normal body's bones, so robe 236's coat helpers are out of reach, but the body
skeleton has a stock belt flap chain at the back (fb1_g01, fb2_g01, fb4_g01) that the
walk, run, sit, kneel, knockdown and ability clips swing like coat tails.

Every leg-weighted vertex first moves its leg weight onto the pelvis. Then a share of
the whole vertex moves onto the flap chain, picked by the flap joints' bind heights.
The share depends only on the vertex's bind position, so neighbouring vertices move
together and the coat cannot tear: it is zero above the top flap joint, full from the
middle joint down, and largest at the back, easing toward the front (FALLOFF). The
other bones, the geometry, the inverse binds and the bone tables stay unchanged, so the
bind shape is identical. Over the walk and run clips the coat now covers the legs from
behind, stretches far less than the original leg weights and drapes over seats.
Clips that carry no flap tracks (a few stock emotes and turns) hold the flap's
previous pose, as stock tabards do.

The originals come from the Git blobs recorded in ChestSkirtBindings.json, so --apply
is repeatable. Replacing one of these models means recording its new source blob.
Re-run GeneratePartSkinModels.py --apply afterwards: robe-body aliases copy these weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess

import CompileModels as mdl
from NeckSkinBindings import body_model
from RobePoseAudit import rotate

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools' / 'ChestSkirtBindings.json'
LEG_BONES = frozenset({'lthigh_g', 'rthigh_g', 'lshin_g', 'rshin_g', 'lfoot_g', 'rfoot_g'})
# The body's back belt flap, top to bottom.
FLAP_CHAIN = ('fb1_g01', 'fb2_g01', 'fb4_g01')
# Degrees from the back at which the flap share would reach zero. The share eases
# from full at the back to about two thirds at the sides and a sixth at the front.
FALLOFF = 240.


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


def flap_share(position: tuple[float, float, float], pose: dict) -> float:
    """The share of a skirt vertex carried by the flap chain, from its bind position."""
    pelvis = pose['pelvis_g'][0]
    top, middle = (pose[bone][0][2] for bone in FLAP_CHAIN[:2])
    behind = math.degrees(math.atan2(abs(position[0] - pelvis[0]), pelvis[1] - position[1]))
    return (1 - smoothstep(behind / FALLOFF)) * smoothstep((top - position[2]) / (top - middle))


def flap_bones(height: float, pose: dict) -> dict[str, float]:
    """Split a flap share between the two chain joints that bracket the vertex height."""
    heights = [pose[bone][0][2] for bone in FLAP_CHAIN]
    if height >= heights[0]:
        return {FLAP_CHAIN[0]: 1.}
    for index in range(len(FLAP_CHAIN) - 1):
        if height >= heights[index + 1]:
            lower = (heights[index] - height) / (heights[index] - heights[index + 1])
            return {FLAP_CHAIN[index]: 1 - lower, FLAP_CHAIN[index + 1]: lower}
    return {FLAP_CHAIN[-1]: 1.}


def flap_weights(current: dict[str, float], position: tuple[float, float, float], pose: dict) -> dict[str, float]:
    """Hang a leg-weighted vertex from the pelvis and the back belt flap."""
    if not LEG_BONES & set(current):
        return current
    share = flap_share(position, pose)
    result = {bone: weight * (1 - share) for bone, weight in hip_weights(current).items()}
    for bone, weight in flap_bones(position[2], pose).items():
        result[bone] = result.get(bone, 0.) + share * weight
    kept = sorted(((bone, weight) for bone, weight in result.items() if weight > 1e-6), key=lambda item: (-item[1], item[0]))[:4]
    # Keep the source's weight sum: some exports are a little off one, and the bind shape scales with it.
    scale = sum(current.values()) / sum(weight for _, weight in kept)
    return {bone: weight * scale for bone, weight in kept}


def rebind(data: bytes) -> tuple[bytes, int]:
    bones, body = skeleton(data)
    pose = body.pose()
    rest = iter(rest_positions(data))
    result = bytearray(data)
    changed = 0
    for skin in skins(data, bones):
        bone_map = skin['bone_map']
        slots = {bone: slot for slot, bone in enumerate(skin['bones']) if bone is not None and bone_map[bones.index(bone)] == slot}
        free = [slot for slot in range(17) if slot not in bone_map]
        for vertex in range(skin['count']):
            current = influences(data, skin, vertex)
            target = flap_weights(current, next(rest), pose)
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
    rebound, changed = rebind(original)
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
        raise ValueError(f'Chest skirts are not hip-weighted: {stale}; run ChestSkirtBindings.py --apply')
    if args.apply and stale:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"{'Rebound' if args.apply else 'Validated'} {len(manifest['models'])} chest skirt models"
          f"{f' ({len(stale)} updated)' if args.apply else ''}.")


if __name__ == '__main__':
    main()
