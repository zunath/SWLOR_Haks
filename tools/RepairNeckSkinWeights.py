"""Recover the corrupt female dwarf neck 159 from exact human vertex matches."""
import argparse
import hashlib
from pathlib import Path
import struct

import numpy as np
import GenerateTintMapAssets as tint
from NeckSkinBindings import body_model, skins
from RobePoseAudit import Model, inverse_transform, multiply, rotate


def repair(original, reference, dwarf_body, human_body):
    if hashlib.sha256(original).hexdigest() != '566fae56b5a0abda09400b62116c7aa8a492f906168905312c2204dda8dfaa6e':
        raise ValueError('This repair only accepts the audited corrupt female dwarf neck 159')
    def geometry(data):
        model = Model(data, False)
        node = next(node for node in model.nodes if node[0] == 'mesh_0010.rip.118')
        mesh = node[4] + 112
        raw = 12 + model.uint(4)
        count = struct.unpack_from('<H', data, mesh + 448)[0]
        if model.uint(mesh + 440):
            raise ValueError('Expected deinterleaved vertices')
        vertices = np.array([struct.unpack_from('<3f', data, raw + model.uint(mesh + 444) + i * 12) for i in range(count)])
        return model, raw, mesh + 512, count, vertices
    target, raw, skin, count, vertices = geometry(original)
    donor, donor_raw, donor_skin, _, reference_vertices = geometry(reference)
    # These are the authored axis ratios: every target vertex must match, and
    # every coincident reference vertex must agree on the recovered influences.
    distance = ((vertices[:, None] - reference_vertices[None, :] * [1.1, 1.04, .98]) ** 2).sum(2)
    weights, indices = bytearray(), bytearray()
    for vertex in range(count):
        candidates = np.flatnonzero(distance[vertex] < 1e-12)
        values = set()
        for i in candidates:
            w = donor_raw + donor.uint(donor_skin + 12) + int(i) * 16
            b = donor_raw + donor.uint(donor_skin + 16) + int(i) * 8
            values.add((reference[w:w + 16], reference[b:b + 8]))
        if len(values) != 1:
            raise ValueError(f'Vertex {vertex} has missing or ambiguous reference weights')
        w, b = values.pop()
        weights.extend(w)
        indices.extend(b)
    pose = dwarf_body.pose()
    names = {node[0]: i for i, node in enumerate(dwarf_body.nodes)}
    source_bones = struct.unpack_from('<17h', reference, donor_skin + 64)
    used = sorted({index for i in range(count)
                   for index, weight in zip(struct.unpack_from('<4h', indices, i * 8),
                                            struct.unpack_from('<4f', weights, i * 16)) if weight})
    size = len(dwarf_body.nodes)
    mapping, bones = [-1] * size, [-1] * 17
    rotations = bytearray(struct.pack('<4f', 1, 0, 0, 0) * size)
    translations = bytearray(size * 12)
    mp, mq, _ = pose['neck_g']
    for slot in used:
        name = human_body.nodes[source_bones[slot]][0]
        index = names[name]
        mapping[index], bones[slot] = slot, index
        bp, bq, _ = pose[name]
        _, inverse = inverse_transform(bp, bq)
        q = multiply(inverse, mq)
        t = rotate(inverse, tuple(a - b for a, b in zip(mp, bp)))
        struct.pack_into('<4f', rotations, index * 16, q[3], *q[:3])
        struct.pack_into('<3f', translations, index * 12, *t)
    result = bytearray(original[:raw])
    def append(payload):
        while len(result) % 4:
            result.append(0)
        pointer = len(result) - 12
        result.extend(payload)
        return pointer
    pointer = append(struct.pack(f'<{size}h', *mapping))
    struct.pack_into('<II', result, skin + 20, pointer, size)
    pointer = append(rotations)
    struct.pack_into('<III', result, skin + 28, pointer, size, size)
    pointer = append(translations)
    struct.pack_into('<III', result, skin + 40, pointer, size, size)
    struct.pack_into('<17h', result, skin + 64, *bones)
    struct.pack_into('<I', result, 4, len(result) - 12)
    result.extend(original[raw:])
    new_raw = 12 + struct.unpack_from('<I', result, 4)[0]
    struct.pack_into('<I', result, skin + 12, len(result) - new_raw)
    result.extend(weights)
    struct.pack_into('<I', result, skin + 16, len(result) - new_raw)
    result.extend(indices)
    struct.pack_into('<I', result, 8, len(result) - new_raw)
    skins(result)
    if result[new_raw:new_raw + len(original) - raw] != original[raw:]:
        raise ValueError('Existing raw geometry changed')
    return bytes(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, required=True)
    args = parser.parse_args()
    root = tint.REPOSITORY_ROOT
    path = root / 'sw_pt_neck/pfd0_neck159.mdl'
    original = path.read_bytes()
    try:
        skins(original)
    except ValueError:
        stock = tint.read_stock_key_models(args.game_data)
        reference = (root / 'sw_pt_neck/pfh0_neck159.mdl').read_bytes()
        path.write_bytes(repair(original, reference,
            body_model(tint.extract_stock_bif_resource(*stock['pfd0'])),
            body_model((root / 'sw_pt_root/pfh0.mdl').read_bytes())))
        print('Recovered all 1,240 weights; original geometry and materials preserved.')
    else:
        print('Neck 159 skin weights already validate.')
