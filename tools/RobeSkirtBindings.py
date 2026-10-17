"""Keep robe 236's skirt attached during body-only ability animations.

Its legacy coat helpers have no tracks in authored ability clips. Bind the same
skin weights to pelvis/thigh/shin bones instead. Meshes, body layout, animation
IDs, materials and every other skin remain byte-identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

import CompileModels as mdl
import RobePoseAudit as poses

ROOT = Path(__file__).resolve().parents[1]
SOURCES = frozenset(f'p{gender}{race}0_robe236' for gender in 'fm' for race in 'deho')
PANELS = ('skirt_b', 'skirt_fl', 'skirt_fr')
TARGETS = {
    f'coat_{corner}{suffix}': ('pelvis_g' if suffix == '1' else
                             f'{side}thigh_g' if suffix == '1a' else f'{side}shin_g')
    for corner, side in (('bl', 'l'), ('br', 'r'), ('fl', 'l'), ('fr', 'r'))
    for suffix in ('1', '1a', '2')
}


def skin_layout(model, offset):
    flags = model.uint(offset + 108)
    if not flags & 64:
        raise ValueError('Expected a compiled skin')
    mesh = offset + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
    skin = mesh + 512
    raw = 12 + model.uint(4)
    return (skin, struct.unpack_from('<H', model.data, mesh + 448)[0],
            raw + model.uint(skin + 12), raw + model.uint(skin + 16), model.uint(mesh + 440))


def vertices(model, offset, clip=None, time=0):
    skin, count, weights, indices, stride = skin_layout(model, offset)
    mesh = skin - 512
    raw = 12 + model.uint(4)
    positions = raw + model.uint(mesh + 444)
    # Native raw vertices are interleaved; the position field starts each row.
    vertex_stride = stride or 12
    if vertex_stride < 12:
        raise ValueError('Invalid vertex stride')
    palette = struct.unpack_from('<17h', model.data, skin + 64)
    qptr, tptr = 12 + model.uint(skin + 28), 12 + model.uint(skin + 40)
    world = list(model.pose(clip, time, model.scale).values())
    for vertex in range(count):
        point = struct.unpack_from('<3f', model.data, positions + vertex * vertex_stride)
        ws = struct.unpack_from('<4f', model.data, weights + vertex * (stride or 16))
        slots = struct.unpack_from('<4h', model.data, indices + vertex * (stride or 8))
        position = [0., 0., 0.]
        for weight, slot in zip(ws, slots):
            if not weight:
                continue
            if not 0 <= slot < 17 or not 0 <= palette[slot] < len(model.nodes):
                raise ValueError('Invalid weighted bone')
            bone = palette[slot]
            stored = struct.unpack_from('<4f', model.data, qptr + bone * 16)
            local = poses.rotate((*stored[1:], stored[0]), point)
            local = tuple(a+b for a,b in zip(local, struct.unpack_from('<3f', model.data, tptr + bone * 12)))
            bp, bq, bs = world[bone]
            transformed = poses.rotate(bq, tuple(value*bs for value in local))
            for axis in range(3):
                position[axis] += weight * (bp[axis] + transformed[axis])
        yield tuple(position)


def bind_skirts(data, names):
    """Retarget only three skin palettes; the body and garment skeletons stay intact."""
    model = poses.Model(data, False)
    indices = {}
    for index, node in enumerate(model.nodes):
        indices.setdefault(node[0], []).append(index)
    required = set(TARGETS.values()) | {names[panel] for panel in PANELS}
    if any(len(indices.get(name, [])) != 1 for name in required):
        raise ValueError('Missing or ambiguous body bone/skirt mesh')
    remap = {names[bone]: target for bone, target in TARGETS.items()}
    allowed_bones = set(TARGETS.values()) | {names['torso_g']}
    world = model.pose()
    result = bytearray(data)
    for panel in PANELS:
        mesh_name = names[panel]
        offset = model.nodes[indices[mesh_name][0]][4]
        skin, count, weights, vertex_indices, stride = skin_layout(model, offset)
        palette = struct.unpack_from('<17h', data, skin + 64)
        old_slots, rows = set(), []
        for vertex in range(count):
            ws = struct.unpack_from('<4f', data, weights + vertex * (stride or 16))
            slots = struct.unpack_from('<4h', data, vertex_indices + vertex * (stride or 8))
            if any(not math.isfinite(w) or w < 0 for w in ws) or abs(sum(ws)-1) > .0021:
                raise ValueError(f'{mesh_name}: invalid weights')
            old_slots.update(slot for weight, slot in zip(ws, slots) if weight)
            rows.append((ws, slots))
        targets, slot_map, new_binds, rest = {}, {}, set(), {}
        for slot in sorted(old_slots):
            if not 0 <= slot < 17 or not 0 <= palette[slot] < len(model.nodes):
                raise ValueError(f'{mesh_name}: invalid skin palette')
            bone = model.nodes[palette[slot]][0]
            target = remap.get(bone, bone)
            if target not in allowed_bones or len(indices.get(target, [])) != 1:
                raise ValueError(f'{mesh_name}: unexpected weighted bone {bone}')
            targets.setdefault(target, len(targets))
            slot_map[slot] = targets[target]
            if target != bone:
                new_binds.add(target)
                old_index = palette[slot]
                qptr = 12 + model.uint(skin + 28)
                tptr = 12 + model.uint(skin + 40)
                stored = struct.unpack_from('<4f', data, qptr + old_index*16)
                old_translation = struct.unpack_from('<3f', data, tptr + old_index*12)
                bp, bq, bs = world[bone]
                if abs(bs-1) > 2e-6:
                    raise ValueError('Skirt binding requires unit bind scales')
                # Preserve the authored skinned rest shape. Some female models'
                # inverse binds intentionally differ from their mesh transform.
                composite = (tuple(a+b for a,b in zip(bp, poses.rotate(bq, old_translation))),
                             poses.multiply(bq, (*stored[1:], stored[0])))
                previous = rest.setdefault(target, composite)
                if math.dist(previous[0], composite[0]) > 2e-5 or not mdl.equivalent_quaternion(previous[1], composite[1]):
                    raise ValueError('Merged skirt bones have different authored rest shapes')
        mapping, length = 12 + model.uint(skin + 20), model.uint(skin + 24)
        qptr, qcount = 12 + model.uint(skin + 28), model.uint(skin + 32)
        tptr, tcount = 12 + model.uint(skin + 40), model.uint(skin + 44)
        if min(length, qcount, tcount) < len(model.nodes):
            raise ValueError(f'{mesh_name}: incomplete bind tables')
        result[mapping:mapping+length*2] = b'\xff\xff' * length
        struct.pack_into('<17h', result, skin + 64, *([-1]*17))
        for bone, slot in targets.items():
            index = indices[bone][0]
            struct.pack_into('<h', result, skin + 64 + slot*2, index)
            struct.pack_into('<h', result, mapping + index*2, slot)
            if bone in new_binds:
                bp, bq, bs = world[bone]
                mp, mq = rest[bone]
                if abs(bs-1) > 2e-6:
                    raise ValueError('Skirt binding requires unit bind scales')
                inverse = (-bq[0], -bq[1], -bq[2], bq[3])
                translation = poses.rotate(inverse, tuple(a-b for a,b in zip(mp,bp)))
                rotation = poses.multiply(inverse, mq)
                struct.pack_into('<4f', result, qptr + index*16, rotation[3], *rotation[:3])
                struct.pack_into('<3f', result, tptr + index*12, *translation)
        for vertex, (ws, slots) in enumerate(rows):
            combined = {}
            for weight, slot in zip(ws, slots):
                if weight:
                    new_slot = slot_map[slot]
                    combined[new_slot] = combined.get(new_slot, 0.) + weight
            pairs = sorted(combined.items())
            struct.pack_into('<4f', result, weights + vertex*(stride or 16),
                             *([weight for _,weight in pairs] + [0.]*(4-len(pairs))))
            struct.pack_into('<4h', result, vertex_indices + vertex*(stride or 8),
                             *([slot for slot,_ in pairs] + [-1]*(4-len(pairs))))
    return bytes(result)


def validate_rest_pose(before, after, names):
    original, target = poses.Model(before, False), poses.Model(after, False)
    old_nodes = {node[0]: node[4] for node in original.nodes}
    new_nodes = {node[0]: node[4] for node in target.nodes}
    maximum = 0.
    for panel in PANELS:
        name = names[panel]
        for old, new in zip(vertices(original, old_nodes[name]), vertices(target, new_nodes[name]), strict=True):
            maximum = max(maximum, math.dist(old, new))
    if maximum > 2e-5:
        raise ValueError(f'Skirt bind shape moved by {maximum:.9g} metres')
    return maximum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    from GenerateRobeRgbModels import file_digest
    manifest_path = ROOT/'tools/RobeRgbModels.json'
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    outputs, processed = {}, set()
    for line in (ROOT/'sw_2da/roberender.2da').read_text().splitlines()[3:]:
        row = line.split()
        if len(row) != 4 or row[1] not in SOURCES or row[3] != '0':
            continue
        if row[1] in processed:
            raise ValueError('Duplicate robe rendering definition')
        processed.add(row[1])
        name = row[1][:3] + row[2]
        path = ROOT/'sw_pt_root'/f'{name}.mdl'
        before = path.read_bytes()
        names = manifest['garment_node_names'][name]
        after = bind_skirts(before, names)
        error = validate_rest_pose(before, after, names)
        if before != after:
            outputs[path] = (before, after)
        print(f'{name}: rest-shape error {error:.9g} m')
    if processed != SOURCES:
        raise ValueError('Robe rendering catalog does not contain all eight normal-body variants')
    if args.check and outputs:
        raise ValueError('Robe 236 still references independent coat helpers')
    if args.apply:
        if manifest_path.read_bytes() != manifest_bytes or any(path.read_bytes() != before for path,(before,_) in outputs.items()):
            raise ValueError('Robe assets changed during validation')
        for path,(_,after) in outputs.items():
            path.write_bytes(after)
            manifest['files'][path.relative_to(ROOT).as_posix()] = hashlib.sha256(after).hexdigest()
            manifest['model_builds'].pop(path.stem, None)
        for name in ('RobeSkirtBindings.py', 'GenerateRobeRgbModels.py'):
            path = ROOT/'tools'/name
            manifest['files'][path.relative_to(ROOT).as_posix()] = file_digest(path)
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    print(f"{'Applied' if args.apply else 'Validated'} {len(outputs)} robe roots")


if __name__ == '__main__':
    main()
