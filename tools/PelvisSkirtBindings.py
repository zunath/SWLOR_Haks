"""Give the released pelvis 035 skirt local clearance as canonical style 034.

The 2026-10-31 release is pinned by Git blob and SHA-256. Its original skin
weights, indices, bone tables, inverse binds, UVs and topology are immutable.
Only front cloth positions and a small right side contact patch move; the rear panels,
waist, lower hem and overall width stay unchanged. Native normals, face planes and
bounds follow the changed geometry. The 034 identity retains the existing 034
material/dye mapping used by the existing 035-to-034 migration.

--output DIR creates fixtures; --apply installs; --check verifies reproduction.
Regenerate dependent part-skin aliases after applying. Client evidence is required.
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
SKIRT_VERTICES = 471


def smooth(value):
    value = np.clip(value, 0, 1)
    return value * value * (3 - 2 * value)


def transform(pose):
    position, rotation, scale = pose
    return np.column_stack((matrix(rotation) * scale, position))


class Garment:
    def __init__(self, data: bytes):
        self.data = data
        self.names, self.body = skirts.skeleton(data)
        self.bind = self.body.pose()
        self.skins = list(skirts.skins(data, self.names))
        if len(self.skins) != 1 or self.skins[0]['count'] not in (688, 690):
            raise ValueError('Unexpected released pelvis 035 topology')
        self.skin = self.skins[0]
        self.mesh = next(mesh for _, _, mesh in mdl.binary_nodes(data) if mesh and 'weights' in mesh)
        self.raw = np.array(self.mesh['verts'])
        self.pos = np.array(skirts.rest_positions(data))

    def bind_matrices(self):
        skin = self.skin
        matrices = []
        for index, name in enumerate(self.names):
            rotation = struct.unpack_from('<4f', self.data, skin['inverse_rotations'] + index * 16)
            translation = struct.unpack_from('<3f', self.data, skin['inverse_translations'] + index * 12)
            inverse = np.column_stack((matrix((*rotation[1:], rotation[0])), translation))
            world = transform(self.bind[name])
            matrices.append(world[:, :3] @ inverse[:, :3])
        weights = np.array([struct.unpack_from('<4f', self.data, skin['weights'] + vertex * (skin['stride'] or 16))
                            for vertex in range(skin['count'])])
        indices = np.array([struct.unpack_from('<4h', self.data, skin['indices'] + vertex * (skin['stride'] or 8))
                            for vertex in range(skin['count'])])
        bones = np.array(skin['palette'])[indices]
        return np.einsum('vb,vbij->vij', weights, np.array(matrices)[bones])

    def displacement(self, parameters):
        front = parameters['frontClearance']
        left_front = parameters['leftFrontClearance']
        left_side = parameters['leftSideClearance']
        right_front = parameters['rightFrontClearance']
        right_side = parameters['rightSideClearance']
        values = (front, left_front, left_side, right_front, right_side)
        if (any(not np.isfinite(value) or value < 0 for value in values)
                or front + max(left_front, right_front) > .060000001
                or max(left_side, right_side) > .025 or right_front > .015):
            raise ValueError('Clearance exceeds the reviewed local 60 mm front / 25 mm side envelope')
        x, y, z = self.pos.T
        moved = np.zeros_like(self.pos)
        # Front cloth clears the thighs; smooth falloffs protect the waist and lower hem.
        band = smooth((y - .0312) / .08) * smooth((1.12 - z) / .14) * smooth((z - .60) / .10)
        moved[:, 1] = band * (front + left_front * smooth((-x - .02) / .12))
        # Native running exposed a separate, smaller contact on the right front panel.
        moved[:, 1] += band * right_front * smooth((x - .02) / .12) * smooth((z - .68) / .12)
        moved[:, 0] = (-left_side * smooth((-x - .10) / .06) * smooth((y - .0312) / .04)
                       * smooth((1.12 - z) / .12) * smooth((z - .65) / .13))
        # Asymmetric native thigh fixtures identify the remaining Tired contact as
        # anatomical RIGHT (+X). Only that seam extends slightly behind the front plane.
        right = (smooth((x - .10) / .06) * smooth((y + .02) / .045)
                 * smooth((1.12 - z) / .12) * smooth((z - .65) / .13))
        moved[:, 0] += right_side * right
        moved[SKIRT_VERTICES:] = 0
        # Neither local seam correction may widen the released silhouette.
        moved[:, 0] = np.maximum(moved[:, 0], x.min() - x)
        moved[:, 0] = np.minimum(moved[:, 0], x.max() - x)
        return moved

    def generate(self, parameters, target):
        displacement = self.displacement(parameters)
        changed = np.any(displacement != 0, axis=1)
        raw = self.raw.copy()
        raw[changed] += np.linalg.solve(self.bind_matrices()[changed], displacement[changed, :, None])[:, :, 0]
        result = bytearray(self.data)
        for vertex in np.flatnonzero(changed):
            struct.pack_into('<3f', result, self.skin['positions'] + vertex * (self.skin['stride'] or 12), *raw[vertex])
        # Use stored float positions for native metadata, not higher-precision intermediates.
        raw = np.array([struct.unpack_from('<3f', result, self.skin['positions'] + vertex * (self.skin['stride'] or 12))
                        for vertex in range(self.skin['count'])])
        faces = np.array([face[:3] for face in self.mesh['faces']])
        touched = np.any(changed[faces], axis=1)
        triangles = raw[faces]
        face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normals = np.zeros_like(raw)
        for corner in range(3):
            np.add.at(normals, faces[:, corner], face_normals)
        old_normals = np.array(self.mesh['normals'])
        groups = {}
        for vertex, (point, normal) in enumerate(zip(self.raw, old_normals)):
            groups.setdefault(tuple(np.round(np.r_[point, normal], 5)), []).append(vertex)
        affected = np.zeros(len(raw), dtype=bool)
        affected[faces[touched].ravel()] = True
        for group in groups.values():
            normals[group] = normals[group].sum(axis=0)
            if np.any(affected[group]):
                affected[group] = True
        # Protect rear-panel normals and internal pelvis/waist vertices; include the
        # small right-side contact extension as well as the front cloth.
        contact_region = ((self.pos[:, 1] > .0312)
                          | ((self.pos[:, 0] > .10) & (self.pos[:, 1] > -.02)))
        affected &= contact_region & (np.arange(len(raw)) < SKIRT_VERTICES)
        lengths = np.linalg.norm(normals, axis=1)
        normals = np.divide(normals, lengths[:, None], out=old_normals.copy(), where=lengths[:, None] > 1e-10)
        mesh_offset = self.mesh['_mesh_offset']
        stride = struct.unpack_from('<I', self.data, mesh_offset + 440)[0]
        normal_base = 12 + struct.unpack_from('<I', self.data, 4)[0] + struct.unpack_from('<I', self.data, mesh_offset + 468)[0]
        for vertex in np.flatnonzero(affected):
            struct.pack_into('<3f', result, normal_base + vertex * (stride or 12), *normals[vertex])
        face_base = 12 + struct.unpack_from('<I', self.data, mesh_offset + 8)[0]
        for index in np.flatnonzero(touched):
            normal = face_normals[index]
            length = np.linalg.norm(normal)
            if length > 1e-12:
                normal = normal / length
            struct.pack_into('<4f', result, face_base + index * 32, *normal, -float(normal @ triangles[index, 0]))
        center = raw.mean(0)
        struct.pack_into('<3f', result, mesh_offset + 20, *raw.min(0))
        struct.pack_into('<3f', result, mesh_offset + 32, *raw.max(0))
        struct.pack_into('<f', result, mesh_offset + 44, float(np.linalg.norm(raw - center, axis=1).max()))
        struct.pack_into('<3f', result, mesh_offset + 48, *center)
        result = update_model_bounds(bytes(result))
        original = target[:-3] + '035'
        if not target.endswith('034') or result.count(original.encode('ascii')) != 3:
            raise ValueError('Unexpected model/root/material identity fields')
        return result.replace(original.encode('ascii'), target.encode('ascii'))


def generate(entry: dict) -> bytes:
    source = skirts.source(entry['sourceBlob'])
    if hashlib.sha256(source).hexdigest() != entry['sourceSha256']:
        raise ValueError('Pinned released pelvis 035 source hash mismatch')
    return Garment(source).generate(entry['parameters'], Path(entry['path']).stem)


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
