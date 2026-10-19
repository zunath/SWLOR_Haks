"""Give robe 236's generated skirts back their native coat-helper weights.

The skirts were once weighted to pelvis/thigh/shin so they followed the legs in
ability clips without coat tracks. With the coat helpers animated (see
NumberRobeGarmentParts.py), that binding only made the coat hug the hips and let
long pelvis parts poke through. The native robe's own skin tables are copied
back onto the unchanged RGB rig; geometry, the skeleton and every other skin
stay byte-identical. Like native robes, the skirt then holds its previous pose
in ability clips that omit the coat tracks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

import RobePoseAudit as poses

ROOT = Path(__file__).resolve().parents[1]
SOURCES = frozenset(f'p{gender}{race}0_robe236' for gender in 'fm' for race in 'deho')
PANELS = ('skirt_b', 'skirt_fl', 'skirt_fr')


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


def restore_native_skirts(source, wearer, names):
    """Copy the native skirt palettes, bone maps, inverse binds and weights onto the RGB rig."""
    native, target = poses.Model(source, False), poses.Model(wearer, False)
    native_indices = {name: i for i, (name, *_) in enumerate(native.nodes)}
    target_indices = {name: i for i, (name, *_) in enumerate(target.nodes)}
    result = bytearray(wearer)
    for panel in PANELS:
        old_offset = native.nodes[native_indices[panel]][4]
        new_offset = target.nodes[target_indices[names[panel]]][4]
        oskin, count, ow, oi, ostride = skin_layout(native, old_offset)
        skin, new_count, nw, ni, stride = skin_layout(target, new_offset)
        if count != new_count:
            raise ValueError(f'{panel}: native and converted skirt topology differ')
        palette = struct.unpack_from('<17h', source, oskin + 64)
        mapped = [target_indices[names[native.nodes[index][0]]] if index >= 0 else -1 for index in palette]
        struct.pack_into('<17h', result, skin + 64, *mapped)
        mapping, length = 12 + target.uint(skin + 20), target.uint(skin + 24)
        result[mapping:mapping + length * 2] = bytes([0xff, 0xff]) * length
        for slot, index in enumerate(mapped):
            if index < 0:
                continue
            struct.pack_into('<h', result, mapping + index * 2, slot)
            for field, size in ((28, 16), (40, 12)):
                start = 12 + native.uint(oskin + field) + palette[slot] * size
                end = 12 + target.uint(skin + field) + index * size
                result[end:end + size] = source[start:start + size]
        for vertex in range(count):
            for old, new, size in ((ow + vertex * (ostride or 16), nw + vertex * (stride or 16), 16),
                                   (oi + vertex * (ostride or 8), ni + vertex * (stride or 8), 8)):
                result[new:new + size] = source[old:old + size]
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
        after = restore_native_skirts((ROOT/'sw_pt_robe'/f'{row[1]}.mdl').read_bytes(), before, names)
        error = validate_rest_pose(before, after, names)
        if before != after:
            outputs[path] = (before, after)
        print(f'{name}: rest-shape error {error:.9g} m')
    if processed != SOURCES:
        raise ValueError('Robe rendering catalog does not contain all eight normal-body variants')
    if args.check and outputs:
        raise ValueError('Robe 236 skirts no longer match their native coat-helper bindings')
    if args.apply:
        if manifest_path.read_bytes() != manifest_bytes or any(path.read_bytes() != before for path,(before,_) in outputs.items()):
            raise ValueError('Robe assets changed during validation')
        for path,(_,after) in outputs.items():
            path.write_bytes(after)
            manifest['files'][path.relative_to(ROOT).as_posix()] = hashlib.sha256(after).hexdigest()
            manifest['model_builds'].pop(path.stem, None)
        for name in ('RobeSkirtBindings.py', 'GenerateRobeRgbModels.py'):
            path = ROOT/'tools'/name
            relative = path.relative_to(ROOT).as_posix()
            manifest['files'][relative] = file_digest(path)
            if relative in manifest.get('validation_tool_files', {}):
                manifest['validation_tool_files'][relative] = manifest['files'][relative]
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    print(f"{'Applied' if args.apply else 'Validated'} {len(outputs)} robe roots")


if __name__ == '__main__':
    main()
