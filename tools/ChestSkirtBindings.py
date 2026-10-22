"""Hang the coat skirts of chest parts 154 and 269 from the hips instead of from the legs.

Both chest parts carry an ankle-length coat skirt. Each half of the skirt was weighted
to its own thigh and shin (chest 269: a third torso, two thirds legs), so walking and
running pulled the halves apart: the back of the coat tore open and the legs showed
through it. Robe 236 had the same flaw and now hangs its skirts from robe coat helpers,
but a chest part only reaches the normal body's bones, and no blend of those
reproduces the helpers' motion.

Every leg weight moves to the pelvis, as on rigid stock skirts; the other bones, the
geometry, the inverse binds and the bone tables stay unchanged, so the bind shape is
identical. Measured over the stock walk and run clips, the worst skirt stretch drops
from up to 11x to under 1.4x and less leg shows behind the coat. Keeping any leg share
let the female chest 154 skirt tear again. Like rigid stock skirts, the coat no longer
follows the legs when sitting or kneeling and passes through the seat or the floor.

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


def skeleton(data: bytes) -> tuple[list[str], object]:
    name = mdl.external_skeleton_name(data)
    if name is None:
        raise ValueError('Expected an externally skinned body part')
    body = body_model((ROOT / 'sw_pt_root' / f'{name}.mdl').read_bytes())
    return [node[0] for node in body.nodes], body


def skins(data: bytes, bones: list[str]):
    """Yield each skin's vertex count, layout pointers and palette bone names."""
    uint = lambda offset: struct.unpack_from('<I', data, offset)[0]
    raw = 12 + uint(4)
    for name, _, mesh in mdl.binary_nodes(data):
        if not mesh or 'weights' not in mesh:
            continue
        offset = mesh['_mesh_offset']
        skin = offset + 512
        palette = struct.unpack_from('<17h', data, skin + 64)
        yield {'name': name, 'count': struct.unpack_from('<H', data, offset + 448)[0], 'stride': uint(offset + 440),
               'positions': raw + uint(offset + 444), 'weights': raw + uint(skin + 12),
               'indices': raw + uint(skin + 16), 'palette': palette,
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


def rebind(data: bytes) -> tuple[bytes, int]:
    bones, _ = skeleton(data)
    result = bytearray(data)
    changed = 0
    for skin in skins(data, bones):
        slots = {bone: slot for slot, bone in enumerate(skin['bones']) if bone is not None}
        for vertex in range(skin['count']):
            current = influences(data, skin, vertex)
            target = hip_weights(current)
            if target is current:
                continue
            if 'pelvis_g' not in slots:
                raise ValueError(f"{skin['name']}: skin palette lacks pelvis_g")
            rows = [*target.items(), *[(None, 0.)] * (4 - len(target))]
            struct.pack_into('<4f', result, skin['weights'] + vertex * (skin['stride'] or 16), *[weight for _, weight in rows])
            struct.pack_into('<4h', result, skin['indices'] + vertex * (skin['stride'] or 8),
                             *[slots[bone] if bone else 0 for bone, _ in rows])
            changed += 1
    return bytes(result), changed


def rest_positions(data: bytes) -> list[tuple[float, float, float]]:
    """Skin every vertex in the wearer's bind pose."""
    bones, body = skeleton(data)
    world = list(body.pose().values())
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
