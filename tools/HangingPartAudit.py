"""Find neck and shoulder parts that hang below the hips without skinning that can move there.

A cape, tail or sash on a neck or shoulder part hangs past the hips. Where that geometry is a
rigid mesh, or a skin weighted only to the torso, arms, neck or head, it swings with the upper
body like a board instead of moving with the hips and legs (neck 289's cape tail followed the
left upper arm; the stock cloak bones held the 291/260 capes off the back as one straight
board). A hanging vertex is one more than HANG metres below the wearer's hip joints in the bind
pose; it counts as stiff unless its skin reaches a lower-body joint (LOWER_BODY).

Some hanging parts are rigid by design (a weapon carried on the back), so the audit cannot tell
which ones should move. HangingPartAudit.json lists every part found today, with how far its
stiff geometry hangs below the hips; TestHangingPartAudit.py fails when a part not listed there
hangs stiffly, or when a listed part no longer does (remove it then). Review a new entry before
adding it: skin the part to the lower body (ChestSkirtBindings.py binds capes) or confirm it is
meant to be rigid.

Scope: the playable normal-phenotype bodies (human, elf, half-orc and dwarf families, both
genders) and their authored styles; generated robe aliases (IDs from 1000) copy their source.

python -B tools/HangingPartAudit.py           prints the parts found
python -B tools/HangingPartAudit.py --update  rewrites the inventory (after review)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct

import CompileModels as mdl
import GenerateTintMapAssets as tint
from NeckSkinBindings import body_model
from RobePoseAudit import Model, rotate

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = Path(__file__).with_suffix('.json')
# Metres below the hip joints from which hanging geometry must be able to move with the lower body.
HANG = .15
PART = re.compile(r'p([mf])([hedo])0_(neck|shol|shor)(\d{3})')
# The joint each part's model hangs from.
ATTACH = {'neck': 'neck_g', 'shol': 'lshoulder_g', 'shor': 'rshoulder_g'}
LEG_BONES = frozenset({'lthigh_g', 'rthigh_g', 'lshin_g', 'rshin_g', 'lfoot_g', 'rfoot_g'})
LOWER_BODY = LEG_BONES | {'pelvis_g', 'rootdummy', 'belt_g1', 'belt_g', 'fb1_g01', 'fb2_g01', 'fb4_g01',
                          'tf1_g', 'tf1l_g', 'tf1r_g', 'tf2_g', 'tf3_g01',
                          *(f'c{column}{row}_g' for column in 'lmr' for row in range(1, 5))}


def skin_bones(data: bytes, model: Model, mesh: int, bones: list[str]) -> list[set[str]]:
    """The joints each skin vertex is weighted to, read without validating legacy weight sums."""
    raw = 12 + model.uint(4)
    skin = mesh + 512
    count = struct.unpack_from('<H', data, mesh + 448)[0]
    stride = model.uint(mesh + 440)
    palette = struct.unpack_from('<17h', data, skin + 64)
    result = []
    for vertex in range(count):
        weights = struct.unpack_from('<4f', data, raw + model.uint(skin + 12) + vertex * (stride or 16))
        slots = struct.unpack_from('<4h', data, raw + model.uint(skin + 16) + vertex * (stride or 8))
        result.append({bones[palette[slot]] for weight, slot in zip(weights, slots)
                       if weight > 0 and 0 <= slot < 17 and 0 <= palette[slot] < len(bones)})
    return result


def rest_points(data: bytes, model: Model, mesh: int, pose: dict, bones: list[str]) -> list[tuple[float, float, float]]:
    """Skin vertices in the wearer's bind pose, through each bone's inverse bind."""
    raw = 12 + model.uint(4)
    skin = mesh + 512
    count = struct.unpack_from('<H', data, mesh + 448)[0]
    stride = model.uint(mesh + 440)
    palette = struct.unpack_from('<17h', data, skin + 64)
    world = [pose[name] for name in bones]
    result = []
    for vertex in range(count):
        point = struct.unpack_from('<3f', data, raw + model.uint(mesh + 444) + vertex * (stride or 12))
        weights = struct.unpack_from('<4f', data, raw + model.uint(skin + 12) + vertex * (stride or 16))
        slots = struct.unpack_from('<4h', data, raw + model.uint(skin + 16) + vertex * (stride or 8))
        position, total = [0., 0., 0.], 0.
        for weight, slot in zip(weights, slots):
            if weight <= 0 or not 0 <= slot < 17 or not 0 <= palette[slot] < len(bones):
                continue
            bone = palette[slot]
            stored = struct.unpack_from('<4f', data, 12 + model.uint(skin + 28) + bone * 16)
            local = rotate((*stored[1:], stored[0]), point)
            local = tuple(a + b for a, b in zip(local, struct.unpack_from('<3f', data, 12 + model.uint(skin + 40) + bone * 12)))
            translation, rotation, scale = world[bone]
            moved = rotate(rotation, tuple(value * scale for value in local))
            for axis in range(3):
                position[axis] += weight * (translation[axis] + moved[axis])
            total += weight
        result.append(tuple(value / total for value in position) if total else point)
    return result


def stiff_depth(data: bytes, body: Model, pose: dict, attach: str) -> float:
    """How far (m) the part's stiff geometry hangs below the hip joints; 0 if none does."""
    hip = pose['lthigh_g'][0][2]
    model = Model(data, False)
    local = model.pose()
    bones = [node[0] for node in body.nodes]
    joint = pose[attach]
    deepest = 0.
    for name, _, _, _, node in model.nodes:
        flags = model.uint(node + 108)
        if not flags & 0x20:
            continue
        mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
        if flags & 0x40:
            if model.uint(mesh + 512 + 24) != len(bones):
                continue  # Not skinned to this wearer's skeleton; GeneratePartSkinModels reports those.
            points = rest_points(data, model, mesh, pose, bones)
            influences = skin_bones(data, model, mesh, bones)
            stiff = [point for point, joints in zip(points, influences) if not joints & LOWER_BODY]
        else:
            raw = 12 + model.uint(4)
            count = struct.unpack_from('<H', data, mesh + 448)[0]
            stride = model.uint(mesh + 440)
            position, rotation, scale = local[name]
            stiff = []
            for vertex in range(count):
                point = struct.unpack_from('<3f', data, raw + model.uint(mesh + 444) + vertex * (stride or 12))
                point = tuple(a + b for a, b in zip(position, rotate(rotation, tuple(value * scale for value in point))))
                stiff.append(tuple(a + b for a, b in zip(joint[0], rotate(joint[1], point))))
        deepest = max([deepest, *(hip - point[2] for point in stiff)])
    return deepest


def audit() -> dict[str, float]:
    """Every authored playable neck and shoulder part whose stiff geometry hangs more than HANG below the hips."""
    active = tint.find_active_models()
    bodies = {}
    result = {}
    for name, path in sorted(active.items()):
        match = PART.fullmatch(name)
        if not match:
            continue
        root = f'p{match[1]}{match[2]}0'
        if root not in bodies:
            body = body_model(active[root].read_bytes())
            bodies[root] = body, body.pose()
        data = path.read_bytes()
        if not mdl.binary(data):
            raise ValueError(f'{name}: expected a compiled model')
        depth = stiff_depth(data, *bodies[root], ATTACH[match[3]])
        if depth > HANG:
            result[name] = round(depth, 2)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--update', action='store_true', help='rewrite the reviewed inventory')
    args = parser.parse_args()
    found = audit()
    for name, depth in found.items():
        print(f'{name}: stiff geometry {depth:.2f} m below the hips')
    if args.update:
        INVENTORY.write_text(json.dumps({'hang': HANG, 'parts': found}, indent=2) + '\n', encoding='utf8', newline='\n')
    print(f'{len(found)} hanging neck and shoulder parts.')


if __name__ == '__main__':
    main()
