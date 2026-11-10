"""Build alternate gun-grasp poses without changing the authored hand assets.

Rigid hand variants share face topology but bake body size and handedness into
their vertices. Transfer finger bends through that correspondence; rotate
mechanical plates in each body's own space so their dimensions remain intact.
Cuffs, UVs, materials and node transforms remain authored. The renderer selects
the alternate only for a gun's grip hand.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

import numpy as np

import CompileModels as mdl
from GenerateRacialPartVariants import update_model_bounds

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_suffix('.json')


def source(blob):
    return subprocess.check_output(['git', '-C', str(ROOT), 'cat-file', 'blob', blob])


def geometry(data, index=None):
    nodes = [(name, mesh) for name, _, mesh in mdl.binary_nodes(data) if mesh]
    if index is None and len(nodes) != 1:
        raise ValueError('A multi-mesh hand needs an explicit finger mesh index')
    mesh = nodes[index or 0][1]
    if 'weights' in mesh:
        raise ValueError('Expected a rigid finger mesh')
    offset = mesh['_mesh_offset']
    # Reject legacy bump-animation streams; EE tangent/sign streams are updated below.
    if any(struct.unpack_from('<I', data, offset + relative)[0] != 0xffffffff
           for relative in (476, 480, 484, 492)):
        raise ValueError('Hand has unsupported legacy bump-animation buffers')
    return mesh


def bend(vertices, recipe):
    anchors = np.array(recipe['joints'])
    root_x, root_z, degrees = (np.interp(vertices[:, 1], anchors[:, 0], anchors[:, column])
                               for column in (1, 2, 3))
    weight = np.clip((root_z - vertices[:, 2]) / recipe['blendMetres'], 0, 1)
    weight = weight * weight * (3 - 2 * weight)
    weight[(vertices[:, 1] >= recipe['thumbStartsY']) |
           (vertices[:, 2] >= recipe['cuffStartsZ'])] = 0
    for region in recipe.get('exclude', []):
        weight[vertices[:, region['axis']] >= region['min']] = 0
    angle = np.deg2rad(degrees) * weight
    posed = vertices.copy()
    if proximal := recipe.get('proximal'):
        # The distal joint travels with its proximal phalanx. Keep the distal
        # selection weights in the authored pose, and bend about the moved joint.
        posed = bend(vertices, proximal)
        roots = bend(np.column_stack([root_x, vertices[:, 1], root_z]), proximal)
        root_x, root_z = roots[:, 0], roots[:, 2]
    dx, dz = posed[:, 0] - root_x, posed[:, 2] - root_z
    changed = weight > 0
    posed[changed, 0] = root_x[changed] + np.cos(angle[changed]) * dx[changed] + np.sin(angle[changed]) * dz[changed]
    posed[changed, 2] = root_z[changed] - np.sin(angle[changed]) * dx[changed] + np.cos(angle[changed]) * dz[changed]
    return posed


def component_labels(mesh):
    """Weld UV duplicates before finding the mechanical hand's separate parts."""
    unique, inverse = np.unique(np.array(mesh['verts']).round(6), axis=0, return_inverse=True)
    neighbors = [set() for _ in unique]
    for face in mesh['faces']:
        a, b, c = inverse[list(face[:3])]
        neighbors[a].update((b, c))
        neighbors[b].update((a, c))
        neighbors[c].update((a, b))
    labels = np.full(len(unique), -1, dtype=int)
    count = 0
    for start in range(len(unique)):
        if labels[start] >= 0:
            continue
        pending = [start]
        labels[start] = count
        while pending:
            for neighbor in neighbors[pending.pop()]:
                if labels[neighbor] < 0:
                    labels[neighbor] = count
                    pending.append(neighbor)
        count += 1
    return unique, labels, labels[inverse]


def rotate(points, pivot, axis, radians):
    relative = points - pivot
    return (pivot + relative * np.cos(radians) + np.cross(axis, relative) * np.sin(radians) +
            np.outer(relative @ axis, axis) * (1 - np.cos(radians)))


def pose(vertices, canonical, recipe, affine=None):
    if recipe.get('kind') != 'articulated':
        return bend(vertices, recipe), vertices[:, 2] >= recipe['cuffStartsZ']
    unique, labels, _ = component_labels(canonical)
    if len(np.unique(labels)) != recipe['componentCount']:
        raise ValueError('Mechanical hand component inventory changed')
    distances = np.linalg.norm(vertices[:, None] - unique[None, :], axis=2)
    nearest = distances.argmin(axis=1)
    if distances[np.arange(len(vertices)), nearest].max() > recipe['vertexToleranceMetres']:
        raise ValueError('Mechanical hand vertex correspondence changed')
    components = labels[nearest]
    protected = np.isin(components, recipe['fixedComponents'])
    # Body variants can be stretched unevenly. Rotate their actual plates,
    # rather than stretching a rotation baked in the canonical body.
    working = vertices if affine is None else vertices @ affine[:3] + affine[3]
    shaped = unique if affine is None else unique @ affine[:3] + affine[3]
    posed = working.copy()
    for joint in recipe['articulations']:
        if joint.get('prong'):
            points = unique[np.isin(labels, joint['rigid'])]
            local_pivot = points[points[:, 2] > joint['pivotMinZ']].mean(axis=0)
            radial = local_pivot.copy()
            radial[2] = 0
            longitudinal = np.array([0., 0., 1.])
            pivot = local_pivot
            if affine is not None:
                pivot = local_pivot @ affine[:3] + affine[3]
                radial = radial @ affine[:3]
                longitudinal = longitudinal @ affine[:3]
            axis = np.cross(radial, longitudinal)
            axis /= np.linalg.norm(axis)
        else:
            hinge = shaped[labels == joint['hinge']]
            pivot = hinge.mean(axis=0)
            axis = np.linalg.svd(hinge - pivot)[2][-1]
        center = shaped[np.isin(labels, joint['rigid'])].mean(axis=0)
        inward = -unique[np.isin(labels, joint['rigid'])].mean(axis=0)
        inward[2] = 0
        if affine is not None:
            inward = inward @ affine[:3]
        if np.dot(np.cross(axis, center - pivot), inward) < 0:
            axis = -axis
        angle = np.deg2rad(recipe['degrees'])
        selected = np.isin(components, joint['rigid'])
        posed[selected] = rotate(working[selected], pivot, axis, angle)
        if 'piston' not in joint:
            continue
        # The rod telescopes between its fixed palm anchor and moving finger anchor.
        rod = unique[labels == joint['piston']]
        rod_center = rod.mean(axis=0)
        rod_axis = np.linalg.svd(rod - rod_center)[2][0]
        projection = (rod - rod_center) @ rod_axis
        ends = [rod[projection < 0].mean(axis=0), rod[projection >= 0].mean(axis=0)]
        if np.linalg.norm(ends[0][:2]) > np.linalg.norm(ends[1][:2]):
            ends.reverse()
        start, end = ends
        if affine is not None:
            start, end = (point @ affine[:3] + affine[3] for point in (start, end))
        new_end = rotate(end[None, :], pivot, axis, angle)[0]
        old_axis, new_axis = end - start, new_end - start
        old_length, new_length = np.linalg.norm(old_axis), np.linalg.norm(new_axis)
        a, b = old_axis / old_length, new_axis / new_length
        cross = np.cross(a, b)
        turn_axis = cross / max(np.linalg.norm(cross), 1e-15)
        turn_angle = np.arccos(np.clip(a @ b, -1, 1))
        selected = components == joint['piston']
        relative = working[selected] - start
        axial = np.outer(relative @ a, a)
        posed[selected] = rotate(relative - axial + axial * (new_length / old_length),
                                 np.zeros(3), turn_axis, turn_angle) + start
    return posed, protected


def correspondence(canonical, target, tolerance):
    a, b = (np.array(mesh['verts'])[np.array([face[:3] for face in mesh['faces']])].mean(1)
            for mesh in (canonical, target))
    if a.shape != b.shape:
        raise ValueError('Hand variant changed face topology')
    affine = np.linalg.lstsq(np.column_stack([a, np.ones(len(a))]), b, rcond=None)[0]
    error = np.linalg.norm(np.column_stack([a, np.ones(len(a))]) @ affine - b, axis=1).max()
    if error > tolerance or abs(np.linalg.det(affine[:3])) < 1e-6:
        raise ValueError(f'Hand variant is not the same affine shape: {error:g} m')
    return affine, float(error)


def patch(data, posed, changed, cuff, alias, mesh_index=None):
    mesh = geometry(data, mesh_index)
    vertices, normals = (np.array(mesh[key]) for key in ('verts', 'normals'))
    faces = np.array([face[:3] for face in mesh['faces']])
    output = bytearray(data)
    offset = mesh['_mesh_offset']
    raw = 12 + struct.unpack_from('<I', data, 4)[0]
    stride = struct.unpack_from('<I', data, offset + 440)[0] or 12
    position = raw + struct.unpack_from('<I', data, offset + 444)[0]
    for index in np.flatnonzero(changed):
        struct.pack_into('<3f', output, position + index * stride, *posed[index])
    stored = np.array([struct.unpack_from('<3f', output, position + i * stride) for i in range(len(vertices))])
    triangles = stored[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    touched = np.any(changed[faces], axis=1)
    if np.any(np.linalg.norm(cross[touched], axis=1) < 1e-10):
        raise ValueError('Collapsed finger face')
    sums = np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(sums, faces[:, corner], cross)
    groups = {}
    for index, (vertex, normal) in enumerate(zip(vertices, normals)):
        groups.setdefault(tuple(np.round(np.r_[vertex, normal], 6)), []).append(index)
    affected = np.zeros(len(vertices), bool)
    affected[faces[touched].ravel()] = True
    for group in groups.values():
        sums[group] = sums[group].sum(0)
        if affected[group].any():
            affected[group] = True
    affected &= ~cuff
    normal = raw + struct.unpack_from('<I', data, offset + 468)[0]
    for index in np.flatnonzero(affected):
        length = np.linalg.norm(sums[index])
        if length > 1e-12:
            struct.pack_into('<3f', output, normal + index * stride, *(sums[index] / length))
    tangent_pointer, sign_pointer = (struct.unpack_from('<I', data, offset + relative)[0] for relative in (488, 496))
    if (tangent_pointer == 0xffffffff) != (sign_pointer == 0xffffffff):
        raise ValueError('Incomplete EE tangent frame')
    if tangent_pointer != 0xffffffff:
        uvs = np.array(mesh['tverts'])[faces]
        edge1, edge2 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        uv1, uv2 = uvs[:, 1] - uvs[:, 0], uvs[:, 2] - uvs[:, 0]
        determinant = uv1[:, 0] * uv2[:, 1] - uv1[:, 1] * uv2[:, 0]
        valid = abs(determinant) > 1e-12
        tangents, bitangents = np.zeros_like(vertices), np.zeros_like(vertices)
        t = (edge1[valid] * uv2[valid, 1, None] - edge2[valid] * uv1[valid, 1, None]) / determinant[valid, None]
        b = (edge2[valid] * uv1[valid, 0, None] - edge1[valid] * uv2[valid, 0, None]) / determinant[valid, None]
        for corner in range(3):
            np.add.at(tangents, faces[valid, corner], t)
            np.add.at(bitangents, faces[valid, corner], b)
        for index in np.flatnonzero(affected):
            n = np.array(struct.unpack_from('<3f', output, normal + index * stride))
            t = tangents[index] - n * (n @ tangents[index])
            if np.linalg.norm(t) < 1e-10:
                old = np.array(struct.unpack_from('<3f', data, raw + tangent_pointer + index * stride))
                t = old - n * (n @ old)
            t /= np.linalg.norm(t)
            sign = -1. if np.cross(n, t) @ bitangents[index] < 0 else 1.
            struct.pack_into('<3f', output, raw + tangent_pointer + index * stride, *t)
            sign_stride = struct.unpack_from('<I', data, offset + 440)[0] or 4
            struct.pack_into('<f', output, raw + sign_pointer + index * sign_stride, sign)
    face_base = 12 + struct.unpack_from('<I', data, offset + 8)[0]
    for index in np.flatnonzero(touched):
        n = cross[index] / np.linalg.norm(cross[index])
        struct.pack_into('<4f', output, face_base + index * 32, *n, -float(n @ triangles[index, 0]))
    center = stored.mean(0)
    struct.pack_into('<3f', output, offset + 20, *stored.min(0))
    struct.pack_into('<3f', output, offset + 32, *stored.max(0))
    struct.pack_into('<f', output, offset + 44, float(np.linalg.norm(stored - center, axis=1).max()))
    struct.pack_into('<3f', output, offset + 48, *center)
    output[20:84] = alias.encode('ascii').ljust(64, b'\0')
    root = 12 + struct.unpack_from('<I', data, 84)[0]
    output[root + 32:root + 64] = alias.encode('ascii').ljust(32, b'\0')
    output = update_model_bounds(bytes(output))
    after = geometry(output, mesh_index)
    for key in mesh.keys() - {'verts', 'normals', '_mesh_offset'}:
        if mesh[key] != after[key]:
            raise ValueError(f'Authored hand field changed: {key}')
    for key in ('verts', 'normals'):
        if not np.array_equal(np.array(mesh[key])[cuff], np.array(after[key])[cuff]):
            raise ValueError(f'Protected cuff {key} changed')
    originals = [values for _, _, values in mdl.binary_nodes(data) if values]
    selected = [values for _, _, values in mdl.binary_nodes(output) if values]
    if len(originals) != len(selected):
        raise ValueError('Hand mesh inventory changed')
    for index, (before, now) in enumerate(zip(originals, selected)):
        if index == (mesh_index or 0):
            continue
        for key in before.keys() - {'_mesh_offset'}:
            if before[key] != now[key]:
                raise ValueError(f'Unselected hand mesh changed: {index}/{key}')
    return output


def organic_bend(vertices, recipe):
    """Close the own-source fingers while leaving the thumb web and cuff fixed."""
    anchors = np.array(recipe['joints'])
    root_x, root_z, degrees = (np.interp(vertices[:, 1], anchors[:, 0], anchors[:, column])
                               for column in (1, 2, 3))
    weight = np.clip((root_z - vertices[:, 2]) / recipe['blendMetres'], 0, 1)
    weight = weight * weight * (3 - 2 * weight)
    weight[(vertices[:, 1] >= recipe['thumbStartsY']) |
           (vertices[:, 2] >= recipe['cuffStartsZ'])] = 0
    weight[(vertices[:, 0] < .005) & (vertices[:, 1] > .02) &
           (vertices[:, 2] > recipe['thumbBaseMinZ'])] = 0
    angle = np.deg2rad(degrees) * weight
    posed = vertices.copy()
    if proximal := recipe.get('proximal'):
        posed = organic_bend(vertices, proximal)
        roots = organic_bend(np.column_stack([root_x, vertices[:, 1], root_z]), proximal)
        root_x, root_z = roots[:, 0], roots[:, 2]
    dx, dz = posed[:, 0] - root_x, posed[:, 2] - root_z
    selected = weight > 0
    posed[selected, 0] = root_x[selected] + np.cos(angle[selected]) * dx[selected] + np.sin(angle[selected]) * dz[selected]
    posed[selected, 2] = root_z[selected] - np.sin(angle[selected]) * dx[selected] + np.cos(angle[selected]) * dz[selected]
    return posed


def organic_geometry(data):
    """Read every rigid mesh in its real model frame, including duplicate names."""
    from scipy.spatial.transform import Rotation
    from RobePoseAudit import Model
    model = Model(data, False)
    worlds = list(model.pose().values())
    parts = []
    for index, ((_, _, _, _, node), (_, _, mesh)) in enumerate(zip(model.nodes, mdl.binary_nodes(data))):
        if mesh is None:
            continue
        if 'weights' in mesh:
            raise ValueError('Own-source closure expects a rigid hand')
        position, orientation, scale = worlds[index]
        matrix = np.eye(4)
        matrix[:3, :3] = Rotation.from_quat(orientation).as_matrix() * scale
        matrix[:3, 3] = position
        vertices = np.array(mesh['verts'])
        root = (np.column_stack([vertices, np.ones(len(vertices))]) @ matrix.T)[:, :3]
        parts.append((mesh, matrix, vertices, root, bool(model.uint(mesh['_mesh_offset'] + 108))))
    return model, parts


def build_organic(recipe, entry):
    original = source(entry['sourceBlob'])
    model, parts = organic_geometry(original)
    affine = np.array(entry['sourceToCanonicalAffine'])
    inverse = np.linalg.inv(affine[:3])
    alias = Path(entry['output']).stem
    output, protected_count, moved_count = original, 0, 0
    for mesh_index, (mesh, matrix, vertices, root, rendered) in enumerate(parts):
        if not rendered:
            continue
        local = (root - affine[3]) @ inverse
        desired = root + (organic_bend(local, recipe['bend']) - local) @ affine[:3]
        fixed = local[:, 2] >= recipe['bend']['cuffStartsZ']
        faces = np.array(mesh['faces'])[:, :3]
        probes = local[faces].mean(1)
        epsilon = 1e-6
        jacobian = np.stack([(organic_bend(probes + np.eye(3)[axis] * epsilon, recipe['bend']) -
                              organic_bend(probes - np.eye(3)[axis] * epsilon, recipe['bend'])) / (2 * epsilon)
                             for axis in range(3)], axis=2)
        minimum_jacobian = float(np.linalg.det(jacobian).min())
        if entry.get('rigidComponents') and len(vertices) < 150:
            labels = component_labels(mesh)[2]
            for label in np.unique(labels):
                mask = labels == label
                before, wanted = root[mask], desired[mask]
                if np.max(np.abs(before - wanted)) < 1e-10:
                    continue
                if fixed[mask].any():
                    raise ValueError('A mechanical finger articulation moves its cuff')
                before_center, wanted_center = before.mean(0), wanted.mean(0)
                left, _, right = np.linalg.svd((before - before_center).T @ (wanted - wanted_center))
                turn = left @ right
                if np.linalg.det(turn) < 0:
                    left[:, -1] *= -1
                    turn = left @ right
                desired[mask] = (before - before_center) @ turn + wanted_center
            minimum_jacobian = 1.
        old_area = np.linalg.norm(np.cross(root[faces[:, 1]] - root[faces[:, 0]], root[faces[:, 2]] - root[faces[:, 0]]), axis=1)
        new_area = np.linalg.norm(np.cross(desired[faces[:, 1]] - desired[faces[:, 0]], desired[faces[:, 2]] - desired[faces[:, 0]]), axis=1)
        minimum_area_ratio = float((new_area[old_area > 1e-12] / old_area[old_area > 1e-12]).min())
        if minimum_jacobian <= .02 or minimum_area_ratio <= .03:
            raise ValueError('Own-source finger closure folds or collapses its surface')
        bound = (np.column_stack([desired, np.ones(len(vertices))]) @ np.linalg.inv(matrix).T)[:, :3]
        bound[fixed] = vertices[fixed]
        changed = np.linalg.norm(bound - vertices, axis=1) > 1e-10
        output = patch(output, bound, changed, fixed, alias, mesh_index)
        protected_count += int(fixed.sum())
        moved_count += int(changed.sum())
    renamed = bytearray(output)
    for index, (_, parent, _, _, node) in enumerate(model.nodes):
        if parent is not None:
            name = 'grip_' + alias[9] + '_' + str(entry['style']) + '_' + str(index)
            renamed[node + 32:node + 64] = name.encode('ascii').ljust(32, b'\0')
    output = update_model_bounds(bytes(renamed))
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Own-source recipe differs from its client-reviewed asset: ' + alias)
    return output, {'movedVertices': moved_count, 'protectedCuffVertices': protected_count, 'sha256': sha256}


def build(recipe, entry):
    if recipe.get('kind') == 'ownSourceOrganic':
        return build_organic(recipe, entry)
    canonical = geometry(source(recipe['canonicalBlob']), recipe.get('meshIndex'))
    original = source(entry['sourceBlob'])
    mesh_index = entry.get('meshIndex', recipe.get('meshIndex'))
    target = geometry(original, mesh_index)
    affine, error = correspondence(canonical, target, recipe['affineToleranceMetres'])
    vertices = np.array(target['verts'])
    local = (vertices - affine[3]) @ np.linalg.inv(affine[:3])
    if recipe.get('kind') == 'articulated':
        posed, cuff = pose(local, canonical, recipe, affine)
        # Keep protected positions bit-exact, including inverse-affine roundoff.
        posed[cuff] = vertices[cuff]
    else:
        moved, cuff = pose(local, canonical, recipe)
        posed = vertices + (moved - local) @ affine[:3]
    changed = np.linalg.norm(posed - vertices, axis=1) > 1e-10
    result = patch(original, posed, changed, cuff, Path(entry['output']).stem, mesh_index)
    return result, {'movedVertices': int(changed.sum()), 'protectedCuffVertices': int(cuff.sum()),
                    'affineErrorMetres': error, 'sha256': hashlib.sha256(result).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    parser.add_argument('--assets-only', action='store_true', help='Verify or rebuild owned meshes without rewriting selection tables')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    recipes = manifest['recipes']
    rows = []
    for recipe in recipes:
        for entry in recipe['models']:
            output, _ = build(recipe, entry)
            path = ROOT / entry['output']
            if path.with_suffix('.lod').exists():
                raise ValueError('Hand pose alias is shadowed by a LOD redirect')
            if args.apply:
                path.write_bytes(output)
            elif not path.is_file() or path.read_bytes() != output:
                raise ValueError(f'Stale gun-grasp hand: {path}')
            if (ROOT / entry['source']).read_bytes() != source(entry['sourceBlob']):
                raise ValueError('Authored hand source changed; review its pose recipe')
            rows.append((Path(entry['source']).stem.lower(), path.stem.lower()))
    if args.assets_only:
        print(f'Gun-grasp asset recipes: {len(rows)} variants pass.')
        return
    selections = manifest.get('selections', [[a, b, '****', '****'] for a, b in sorted(rows)])
    table = '2DA V2.0\n\nSOURCE RENDER WEAPON MODEL\n' + ''.join(
        str(index) + ' ' + ' '.join(row) + '\n' for index, row in enumerate(selections))
    path = ROOT / 'sw_2da/griprender.2da'
    if args.apply:
        path.write_text(table, encoding='ascii', newline='\n')
    elif path.read_text() != table:
        raise ValueError('Stale griprender.2da')
    # Alias meshes retain the source material identity and dye-layer assignments.
    tint_path = ROOT / 'sw_2da/tintmap.2da'
    lines = tint_path.read_text().splitlines()
    existing = [line.split()[1:] for line in lines[3:] if line.strip()]
    aliases = {render for _, render in rows}
    authored = [row for row in existing if row[0].lower() not in aliases]
    expected = []
    for source_name, render in sorted(rows):
        bindings = [row for row in authored if row[0].lower() == source_name]
        if not bindings:
            raise ValueError(f'No source dye bindings for {source_name}')
        expected.extend([[render, *row[1:]] for row in bindings])
    actual = [row for row in existing if row[0].lower() in aliases]
    if args.apply:
        tint_path.write_text('\n'.join(lines[:3]) + '\n' + ''.join(
            f'{i} ' + ' '.join(row) + '\n' for i, row in enumerate([*authored, *expected])),
            encoding='ascii', newline='\n')
    elif actual != expected:
        raise ValueError('Stale gun-grasp dye bindings')
    print(f'Gun-grasp hands: {len(rows)} variants pass; authored hands and cuffs unchanged.')


if __name__ == '__main__':
    main()
