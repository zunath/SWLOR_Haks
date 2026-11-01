"""Reproduce the normal human pelvis 034 skirt's fitted rig and local clearance.

Only this knee-length skirt is handled here. The first four indexed components
are outer/inner skirt and tabard surfaces; the internal pelvis and waistband
remain byte-identical. The manifest pins original Git blobs so --apply never compounds a previous fit.

--output DIR builds fixture resources without touching tracked assets.
--apply installs the selected recipes; --check verifies exact reproducibility.
Rerun dependent part-skin aliases after applying. Native captures remain required.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
import numpy as np
import ChestSkirtBindings as skirts
import CompileModels as mdl
from GenerateRacialPartVariants import matrix, update_model_bounds
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_suffix('.json')
BONES = ['pelvis_g', 'lthigh_g', 'rthigh_g', 'tf2_g', 'fb2_g01']
SKIRT_VERTICES = 471

def smooth(value):
    value = np.clip(value, 0, 1)
    return value * value * (3 - 2 * value)

def transform(pose):
    position, rotation, scale = pose
    return np.column_stack((matrix(rotation) * scale, position))

class Garment:

    def __init__(self, data: bytes, extra_clearance: dict[str, float] | None=None):
        self.data = data
        self.names, self.body = skirts.skeleton(data)
        self.bind = self.body.pose()
        self.pos = np.array(skirts.rest_positions(data))
        self.skins = list(skirts.skins(data, self.names))
        if len(self.skins) != 1 or self.skins[0]['count'] not in (688, 690):
            raise ValueError('Unexpected pelvis 034 topology')
        self.raw = np.concatenate([np.array([struct.unpack_from('<3f', data, skin['positions'] + vertex * (skin['stride'] or 12)) for vertex in range(skin['count'])]) for skin in self.skins])
        self.origw = np.zeros((len(self.pos), len(BONES)))
        rows = [skirts.influences(data, skin, vertex) for skin in self.skins for vertex in range(skin['count'])]
        for vertex, row in enumerate(rows):
            for bone, weight in row.items():
                target = bone if bone in BONES else 'lthigh_g' if bone == 'lshin_g' else 'rthigh_g' if bone == 'rshin_g' else 'pelvis_g'
                self.origw[vertex, BONES.index(target)] += weight
        self.sums = self.origw.sum(1)
        self.origw /= self.sums[:, None]
        skin = self.skins[0]
        bind_transforms = []
        for bone in BONES:
            index = self.names.index(bone)
            rotation = struct.unpack_from('<4f', data, skin['inverse_rotations'] + index * 16)
            translation = struct.unpack_from('<3f', data, skin['inverse_translations'] + index * 12)
            inverse = np.column_stack((matrix((*rotation[1:], rotation[0])), translation))
            world = transform(self.bind[bone])
            bind_transforms.append(np.column_stack((world[:, :3] @ inverse[:, :3], world[:, :3] @ inverse[:, 3] + world[:, 3])))
        self.bindt = np.array(bind_transforms)
        self.extra = np.zeros(len(self.pos))
        for vertex, clearance in (extra_clearance or {}).items():
            if not 0 <= int(vertex) < SKIRT_VERTICES or not 0 <= clearance <= 0.04:
                raise ValueError('Clearance outside the fitted skirt envelope')
            self.extra[int(vertex)] = clearance

    def weights_and_raw(self, x):
        side, seam, span, frontflap, backflap, frontgap, backgap, sidegap = x
        p = self.pos
        origin = np.array(self.bind['pelvis_g'][0])
        hip = self.bind['lthigh_g'][0][2]
        delta = p - origin
        r = np.linalg.norm(delta[:, :2], axis=1)
        across = np.divide(delta[:, 0], r, out=np.zeros(len(p)), where=r > 1e-05)
        lateral = smooth(abs(across) / span)
        own = smooth((across / span + 1) / 2)
        lower = smooth((hip + 0.08 - p[:, 2]) / 0.23)
        lower[471:] = 0.0
        legs = seam + (side - seam) * lateral
        target = np.zeros_like(self.origw)
        target[:, 0] = 1 - legs
        target[:, 1] = legs * (1 - own)
        target[:, 2] = legs * own
        ff = (1 - lateral) ** 2 * np.where(delta[:, 1] >= 0, frontflap, backflap)
        target[:, :3] *= 1 - ff[:, None]
        target[:, 3] = np.where(delta[:, 1] >= 0, ff, 0)
        target[:, 4] = np.where(delta[:, 1] < 0, ff, 0)
        w = self.origw * (1 - lower[:, None]) + target * lower[:, None]
        gap = (np.where(delta[:, 1] >= 0, frontgap, backgap) * (1 - lateral) + sidegap * lateral) * lower
        moved = p.copy()
        moved[:, :2] += np.divide(delta[:, :2], r[:, None], out=np.zeros_like(delta[:, :2]), where=r[:, None] > 1e-05) * gap[:, None]
        if np.any(self.extra):
            direction = np.zeros_like(moved)
            delta = moved[:SKIRT_VERTICES, :2] - origin[:2]
            direction[:SKIRT_VERTICES, :2] = delta / np.linalg.norm(delta, axis=1)[:, None]
            panel = np.r_[np.arange(187, 267), np.arange(419, 471)]
            direction[panel] = [0, 1, 0]
            direction *= lower[:, None]
            moved += direction * self.extra[:, None]
        # Compensate authored inverse binds when changing the weight field.
        bt = np.einsum('vb,bij->vij', w, self.bindt)
        raw = np.linalg.solve(bt[:, :, :3], (moved - bt[:, :, 3])[:, :, None])[:, :, 0]
        raw[471:] = self.raw[471:]
        return (w, raw, moved)

    def generate(self, x):
        w, raw, rest = self.weights_and_raw(x)
        result = bytearray(self.data)
        offset = 0
        for skin in self.skins:
            mapping = skin['bone_map']
            slots = {bone: i for i, bone in enumerate(skin['bones']) if bone and mapping[self.names.index(bone)] == i}
            free = [s for s in range(17) if s not in mapping]
            for bone in BONES:
                if bone not in slots:
                    slot = free.pop(0)
                    slots[bone] = slot
                    index = self.names.index(bone)
                    struct.pack_into('<h', result, skin['palette_offset'] + slot * 2, index)
                    struct.pack_into('<h', result, skin['bone_map_offset'] + index * 2, slot)
            for v in range(skin['count']):
                if offset + v >= 471:
                    continue
                row = [(BONES[b], float(value * self.sums[offset + v])) for b, value in enumerate(w[offset + v]) if value > 1e-07]
                assert len(row) <= 4, row
                row = sorted(row, key=lambda kv: -kv[1]) + [(None, 0.0)] * (4 - len(row))
                struct.pack_into('<4f', result, skin['weights'] + v * (skin['stride'] or 16), *[z[1] for z in row])
                struct.pack_into('<4h', result, skin['indices'] + v * (skin['stride'] or 8), *[slots[z[0]] if z[0] else 0 for z in row])
                struct.pack_into('<3f', result, skin['positions'] + v * (skin['stride'] or 12), *raw[offset + v])
            offset += skin['count']
        for _, _, mesh in mdl.binary_nodes(self.data):
            if not mesh or 'weights' not in mesh:
                continue
            count = len(mesh['verts'])
            faces = np.array([f[:3] for f in mesh['faces']])
            tri = raw[faces]
            norm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            vn = np.zeros((count, 3))
            for i in range(3):
                np.add.at(vn, faces[:, i], norm)
            oldn = np.array(mesh['normals'])
            groups = {}
            for i, (pt, n) in enumerate(zip(np.array(mesh['verts']), oldn)):
                groups.setdefault(tuple(np.round(np.r_[pt, n], 5)), []).append(i)
            for group in groups.values():
                vn[group] = vn[group].sum(axis=0)
            lengths = np.linalg.norm(vn, axis=1)
            vn = np.divide(vn, lengths[:, None], out=oldn.copy(), where=lengths[:, None] > 1e-10)
            base = 12 + struct.unpack_from('<I', self.data, 4)[0] + struct.unpack_from('<I', self.data, mesh['_mesh_offset'] + 468)[0]
            stride = struct.unpack_from('<I', self.data, mesh['_mesh_offset'] + 440)[0]
            for i, n in enumerate(vn[:471]):
                struct.pack_into('<3f', result, base + i * (stride or 12), *n)
        for _, _, mesh in mdl.binary_nodes(self.data):
            if not mesh or 'weights' not in mesh:
                continue
            mo = mesh['_mesh_offset']
            low = raw.min(0)
            high = raw.max(0)
            center = raw.mean(0)
            struct.pack_into('<3f', result, mo + 20, *low)
            struct.pack_into('<3f', result, mo + 32, *high)
            struct.pack_into('<f', result, mo + 44, float(np.linalg.norm(raw - center, axis=1).max()))
            struct.pack_into('<3f', result, mo + 48, *center)
            faceptr = 12 + struct.unpack_from('<I', self.data, mo + 8)[0]
            for i, face in enumerate(mesh['faces']):
                if min(face[:3]) >= 471:
                    continue
                a, b, c = raw[face[:3]]
                normal = np.cross(b - a, c - a)
                length = np.linalg.norm(normal)
                if length > 1e-12:
                    normal /= length
                # Native face planes use n dot p + distance = 0.
                struct.pack_into('<4f', result, faceptr + i * 32, *normal, -float(normal @ a))
        return update_model_bounds(bytes(result))

def generate(entry: dict) -> bytes:
    source = skirts.source(entry['sourceBlob'])
    if hashlib.sha256(source).hexdigest() != entry['sourceSha256']:
        raise ValueError('Pinned pelvis source hash mismatch')
    params = entry['parameters']
    order = ('sideThigh', 'seamThigh', 'seamSpan', 'frontFlap', 'backFlap', 'frontClearance', 'backClearance', 'sideClearance')
    values = [params[key] for key in order]
    return Garment(source, entry.get('extraClearance')).generate(values)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--output', type=Path)
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    failures = []
    for name, entry in manifest['models'].items():
        generated = generate(entry)
        target = args.output / (name + '.mdl') if args.output else ROOT / entry['path']
        if args.check:
            if not target.exists() or target.read_bytes() != generated:
                failures.append(name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(generated)
    if failures:
        raise SystemExit('Pelvis skirt recipes differ: ' + ', '.join(failures))
    print(f"Verified {len(manifest['models'])} pelvis skirt recipes" if args.check else f"Generated {len(manifest['models'])} pelvis skirt recipes")
if __name__ == '__main__':
    main()
