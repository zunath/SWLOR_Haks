"""Remap external neck skins to the wearer's actual skeleton traversal.

NWN's GatherBones skips separately attached models, but visits every inline
child of a robe-bearing body root. Animation part IDs are not skin bone IDs.
Keep vertices, weights, bind transforms, and materials unchanged; relocate only
the external bone tables. Never recompile these skins without their donor body.
"""
from __future__ import annotations

import math
import struct

import CompileModels as mdl
from RobePoseAudit import Model


def body_model(data):
    if mdl.binary(data):
        return Model(data, False)
    # Stock KEY resources include ASCII skeletons. Reproduce the compiler's
    # depth-first order, not the source file's node declaration order.
    declarations = mdl.parse_nodes(data.decode('latin1').split('endmodelgeom', 1)[0])
    children = {}
    for _, name, props in declarations:
        children.setdefault(props.get('parent', ['null'])[0].lower(), []).append((name, props))
    result = Model.__new__(Model)
    result.nodes = []
    def visit(name, props, parent):
        controllers = {}
        for key, kind in [('position', 8), ('orientation', 20), ('scale', 36)]:
            if key in props:
                value = mdl.quaternion(props[key]) if kind == 20 else tuple(map(float, props[key]))
                controllers[kind] = ((0,), [value])
        index = len(result.nodes)
        result.nodes.append((name, parent, int(props.get('partnumber', [-1])[0]), controllers, 0))
        for child, fields in children.get(name, []):
            visit(child, fields, index)
    roots = children.get('null', [])
    if len(roots) != 1:
        raise ValueError('Body skeleton must have one root')
    visit(*roots[0], None)
    if len(result.nodes) != len(declarations):
        raise ValueError('Disconnected body skeleton')
    return result


def skins(data):
    model = Model(data, False)
    raw = 12 + model.uint(4)
    if raw + model.uint(8) != len(data):
        raise ValueError("Invalid model section lengths")
    result = []
    for name, _, _, _, node in model.nodes:
        flags = model.uint(node + 108)
        if not flags & 64:
            continue
        mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
        skin = mesh + 512
        count = struct.unpack_from('<H', data, mesh + 448)[0]
        stride = model.uint(mesh + 440)
        used = set()
        for vertex in range(count):
            weights = struct.unpack_from('<4f', data, raw + model.uint(skin + 12) + vertex * (stride or 16))
            indices = struct.unpack_from('<4h', data, raw + model.uint(skin + 16) + vertex * (stride or 8))
            # Legacy exports round each of up to four weights to three decimals.
            if any(not math.isfinite(w) or w < 0 or w > 1.00001 for w in weights) or abs(sum(weights) - 1) > 0.0021:
                raise ValueError(f'{name}: invalid weights at vertex {vertex}')
            for weight, slot in zip(weights, indices):
                if not weight:
                    continue
                if not 0 <= slot < 17:
                    raise ValueError(f'{name}: invalid bone slot {slot}')
                used.add(slot)
        bones = struct.unpack_from('<17h', data, skin + 64)
        mapping, length = model.uint(skin + 20), model.uint(skin + 24)
        for slot in used:
            bone = bones[slot]
            if not 0 <= bone < min(length, model.uint(skin + 32), model.uint(skin + 44)):
                raise ValueError(f'{name}: invalid external bone {bone}')
            if struct.unpack_from('<h', data, 12 + mapping + bone * 2)[0] != slot:
                raise ValueError(f'{name}: inconsistent bone map for slot {slot}')
        if length > len(model.nodes):
            result.append((skin, tuple((slot, bones[slot]) for slot in sorted(used))))
    return result


def mapping_for(data, source_body, target_body, external=None):
    external = skins(data) if external is None else external
    # Preserve duplicate helper identities by their occurrence in traversal.
    def identities(nodes):
        counts = {}
        result = []
        for index, (name, *_) in enumerate(nodes):
            name = '@root' if index == 0 else name
            occurrence = counts.get(name, 0)
            counts[name] = occurrence + 1
            result.append((name, occurrence))
        return result
    old = identities(source_body.nodes)
    new = {key: index for index, key in enumerate(identities(target_body.nodes))}
    return tuple(tuple((slot, bone, new[old[bone]]) for slot, bone in slots) for _, slots in external)


def needs_remap(mapping):
    return any(old != new for mesh in mapping for _, old, new in mesh)


def remap(data, name, mapping, external=None, bone_count=0):
    """Emit a compiled alias. The raw mesh section stays byte-for-byte identical."""
    external = skins(data) if external is None else external
    if len(external) != len(mapping) or not external:
        raise ValueError('Skin mapping inventory differs')
    model = Model(data, False)
    raw = 12 + model.uint(4)
    result = bytearray(data[:raw])
    if len(name) > 16:
        raise ValueError('Model resref is too long')
    result[20:84] = name.encode('ascii').ljust(64, b'\0')
    root = model.nodes[0][4]
    result[root + 32:root + 64] = name.encode('ascii').ljust(32, b'\0')

    def append(payload):
        while len(result) % 4:
            result.append(0)
        pointer = len(result) - 12
        result.extend(payload)
        return pointer

    for (skin, slots), mapped in zip(external, mapping):
        if tuple((slot, old) for slot, old, _ in mapped) != slots:
            raise ValueError('Skin slot inventory differs')
        # Keep the donor's external-skeleton extent even if every used bone moved
        # earlier. A short table can be mistaken for this attachment's local bones.
        count = max(bone_count, model.uint(skin + 24), max(new for _, _, new in mapped) + 1)
        if count > 32767:
            raise ValueError('Skeleton exceeds signed bone-index range')
        nodes = [-1] * count
        quaternions = bytearray(struct.pack('<4f', 1, 0, 0, 0) * count)
        translations = bytearray(count * 12)
        bone_table = [-1] * 17
        for slot, old, new in mapped:
            if nodes[new] != -1:
                raise ValueError('Two slots refer to one bone')
            nodes[new] = slot
            bone_table[slot] = new
            q = 12 + model.uint(skin + 28) + old * 16
            t = 12 + model.uint(skin + 40) + old * 12
            quaternions[new * 16:new * 16 + 16] = data[q:q + 16]
            translations[new * 12:new * 12 + 12] = data[t:t + 12]
        pointer = append(struct.pack(f'<{count}h', *nodes))
        struct.pack_into('<II', result, skin + 20, pointer, count)
        pointer = append(quaternions)
        struct.pack_into('<III', result, skin + 28, pointer, count, count)
        pointer = append(translations)
        struct.pack_into('<III', result, skin + 40, pointer, count, count)
        struct.pack_into('<17h', result, skin + 64, *bone_table)
    struct.pack_into('<I', result, 4, len(result) - 12)
    result.extend(data[raw:])
    return bytes(result)
