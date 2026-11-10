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


def geometry(data, index=None, skin_skeleton=None):
    nodes = [(name, mesh) for name, _, mesh in mdl.binary_nodes(data, skin_skeleton=skin_skeleton) if mesh]
    if index is None and len(nodes) != 1:
        raise ValueError('A multi-mesh hand needs an explicit finger mesh index')
    mesh = nodes[index or 0][1]
    if 'weights' in mesh and skin_skeleton is None:
        raise ValueError('A skin hand needs its explicit donor skeleton')
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


def patch(data, posed, changed, cuff, alias, mesh_index=None, skin_skeleton=None):
    mesh = geometry(data, mesh_index) if skin_skeleton is None else geometry(data, mesh_index, skin_skeleton)
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
    after = geometry(output, mesh_index) if skin_skeleton is None else geometry(output, mesh_index, skin_skeleton)
    for key in mesh.keys() - {'verts', 'normals', '_mesh_offset'}:
        if mesh[key] != after[key]:
            raise ValueError(f'Authored hand field changed: {key}')
    for key in ('verts', 'normals'):
        if not np.array_equal(np.array(mesh[key])[cuff], np.array(after[key])[cuff]):
            raise ValueError(f'Protected cuff {key} changed')
    originals = [values for _, _, values in mdl.binary_nodes(data, skin_skeleton=skin_skeleton) if values]
    selected = [values for _, _, values in mdl.binary_nodes(output, skin_skeleton=skin_skeleton) if values]
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
    if recipe.get('renamePartNodes', True):
        for index, (_, parent, _, _, node) in enumerate(model.nodes):
            if parent is not None:
                name = 'grip_' + alias[9] + '_' + str(entry['style']) + '_' + str(index)
                renamed[node + 32:node + 64] = name.encode('ascii').ljust(32, b'\0')
    output = update_model_bounds(bytes(renamed))
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Own-source recipe differs from its client-reviewed asset: ' + alias)
    return output, {'movedVertices': moved_count, 'protectedCuffVertices': protected_count, 'sha256': sha256}


def droid_field(vertices, recipe):
    """Evaluate the pinned joint field in the hand's own reflected source frame."""
    if recipe['fieldKind'] == 'rigidClaw':
        return vertices.copy()
    if recipe['fieldKind'] == 'organic':
        return droid_thumb_blend(vertices, organic_bend(vertices, recipe['bend']), recipe)
    bend_recipe = recipe['bend']
    anchors = np.array(bend_recipe['joints'])
    root_x, root_z, degrees = (np.interp(vertices[:, 1], anchors[:, 0], anchors[:, column])
                               for column in (1, 2, 3))
    weight = np.clip((root_z - vertices[:, 2]) / bend_recipe['blendMetres'], 0, 1)
    weight = weight * weight * (3 - 2 * weight)
    weight[vertices[:, 2] >= bend_recipe['cuffStartsZ']] = 0
    weight[(vertices[:, 0] < .005) & (vertices[:, 1] > .02) &
           (vertices[:, 2] > bend_recipe['thumbBaseMinZ'])] = 0
    posed = vertices.copy()
    if proximal := bend_recipe.get('proximal'):
        proximal_recipe = {**recipe, 'bend': proximal, 'thumbBlendDegrees': 0}
        posed = droid_field(vertices, proximal_recipe)
        roots = droid_field(np.column_stack([root_x, vertices[:, 1], root_z]), proximal_recipe)
        root_x, root_z = roots[:, 0], roots[:, 2]
    angle = np.deg2rad(degrees) * weight
    dx, dz = posed[:, 0] - root_x, posed[:, 2] - root_z
    selected = weight > 0
    posed[selected, 0] = root_x[selected] + np.cos(angle[selected]) * dx[selected] + np.sin(angle[selected]) * dz[selected]
    posed[selected, 2] = root_z[selected] - np.sin(angle[selected]) * dx[selected] + np.cos(angle[selected]) * dz[selected]
    return droid_thumb_blend(vertices, posed, recipe)


def droid_thumb_blend(vertices, posed, recipe):
    from scipy.spatial.transform import Rotation
    def smooth(value):
        value = np.clip(value, 0, 1)
        return value * value * (3 - 2 * value)
    if across := recipe.get('thumbAcrossPalm'):
        pivot = np.array(across['pivot'])
        yw, xw, zw = across['blendWidths']
        blend = smooth((vertices[:, 1] - .015) / yw) * smooth((.018 - vertices[:, 0]) / xw) * smooth((-.045 - vertices[:, 2]) / zw)
        posed = pivot + Rotation.from_rotvec(blend[:, None] * np.array([0, np.deg2rad(across['yDegrees']), 0])).apply(posed - pivot)
        return pivot + Rotation.from_rotvec(blend[:, None] * np.array([np.deg2rad(across['xDegrees']), 0, 0])).apply(posed - pivot)
    if thumb := recipe.get('thumbBlendDegrees', 0):
        pivot = np.array([-.005, .035, -.065])
        blend = smooth((vertices[:, 1] - .015) / .06) * smooth((.018 - vertices[:, 0]) / .04) * smooth((-.045 - vertices[:, 2]) / .04)
        return pivot + Rotation.from_rotvec(blend[:, None] * np.array([np.deg2rad(thumb), 0, 0])).apply(posed - pivot)
    return posed


def droid_pose(vertices, mesh, recipe):
    """Keep separate mechanical fingers rigid and preserve the authored cuff."""
    from scipy.spatial.transform import Rotation
    labels = component_labels(mesh)[2]
    if recipe['fieldKind'] == 'rigidClaw':
        palm = max(np.unique(labels), key=lambda part: vertices[labels == part, 2].max())
        palm_x = vertices[labels == palm, 0].mean()
        posed = vertices.copy()
        rigid = []
        for part in np.unique(labels):
            if part == palm:
                continue
            selected = labels == part
            points = vertices[selected]
            pivot = points[points[:, 2] > points[:, 2].max() - recipe['hingeBand']].mean(0)
            degrees = recipe['jointDegrees'] * (1 if points[:, 0].mean() > palm_x else -1)
            posed[selected] = (points - pivot) @ Rotation.from_euler('y', degrees, degrees=True).as_matrix().T + pivot
            rigid.append(int(part))
        return posed, labels == palm, rigid, np.zeros(len(vertices), bool)
    posed = droid_field(vertices, recipe)
    rigid = []
    if recipe.get('preserveRigidFingers'):
        for part in np.unique(labels):
            selected = labels == part
            points = vertices[selected]
            if points[:, 2].max() < -.055 and np.ptp(points[:, 2]) < .09 and len(points) >= 3:
                if not recipe.get('projectOnlyMoved', True) or np.max(np.linalg.norm(posed[selected] - points, axis=1)) > 1e-8:
                    center = points[:, [0, 2]].mean(0)
                    goal = posed[selected][:, [0, 2]].mean(0)
                    left, _, right = np.linalg.svd((points[:, [0, 2]] - center).T @ (posed[selected][:, [0, 2]] - goal))
                    fix = np.eye(2)
                    fix[-1, -1] = np.linalg.det(left @ right)
                    turn = left @ fix @ right
                    target = points.copy()
                    target[:, [0, 2]] = (points[:, [0, 2]] - center) @ turn + goal
                    posed[selected] = target
                rigid.append(int(part))
    thumb = np.zeros(len(vertices), bool)
    if opposition := recipe.get('componentThumbRotation'):
        for part in np.unique(labels):
            center = vertices[labels == part].mean(0)
            selected = (center[1] > .07 or (center[1] > .025 and center[2] > -.085)) if opposition['extendedRule'] else (center[1] > .035 and center[2] > -.12)
            if selected:
                thumb[labels == part] = True
        pivot = np.array(opposition['rootPivot'])
        posed[thumb] = (posed[thumb] - pivot) @ Rotation.from_euler('x', opposition['degrees'], degrees=True).as_matrix().T + pivot
    protected = (vertices[:, 2] >= recipe['cuffStartsZ']) & ~thumb
    return posed, protected, rigid, thumb


def build_droid(recipe, entry):
    original = source(entry['sourceBlob'])
    _, parts = organic_geometry(original)
    basis = np.array(entry['jointReferenceToOwn'])
    axes, origin = basis[:3], basis[3]
    if not np.allclose(axes @ axes.T, np.eye(3), atol=1e-7):
        raise ValueError('Mechanical hand reference changes scale or shear')
    alias = Path(entry['output']).stem
    output, moved_count, protected_count = original, 0, 0
    minimum_jacobian = minimum_area = 1.
    for index, (mesh, matrix, vertices, root, rendered) in enumerate(parts):
        if not rendered:
            continue
        local = (root - origin) @ axes.T
        posed, protected, rigid, thumb = droid_pose(local, mesh, recipe)
        labels = component_labels(mesh)[2]
        faces = np.array(mesh['faces'])[:, :3]
        probes = local[faces[~np.isin(labels[faces[:, 0]], rigid) & ~thumb[faces[:, 0]]]].mean(1)
        if len(probes):
            epsilon = 1e-6
            jacobian = np.stack([(droid_field(probes + np.eye(3)[axis] * epsilon, recipe) -
                                  droid_field(probes - np.eye(3)[axis] * epsilon, recipe)) / (2 * epsilon)
                                 for axis in range(3)], axis=2)
            minimum_jacobian = min(minimum_jacobian, float(np.linalg.det(jacobian).min()))
        before = np.cross(local[faces[:, 1]] - local[faces[:, 0]], local[faces[:, 2]] - local[faces[:, 0]])
        after = np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]])
        valid = np.linalg.norm(before, axis=1) > 1e-12
        minimum_area = min(minimum_area, float((np.linalg.norm(after, axis=1)[valid] / np.linalg.norm(before, axis=1)[valid]).min()))
        flat = np.ptp(local[faces][:, :, 1], axis=1) < 1e-6
        if np.any(valid & flat & (before[:, 1] * after[:, 1] < -1e-15) & ~thumb[faces[:, 0]]):
            raise ValueError('Mechanical finger pose inverts a source face')
        target = posed @ axes + origin
        bound = (np.column_stack([target, np.ones(len(target))]) @ np.linalg.inv(matrix).T)[:, :3]
        bound[protected] = vertices[protected]
        changed = np.linalg.norm(bound - vertices, axis=1) > 1e-10
        output = patch(output, bound, changed, protected, alias, index)
        actual = np.array(geometry(output, index)['verts'])
        for part in rigid:
            selected = labels == part
            a, b = vertices[selected], actual[selected]
            error = np.max(np.abs(np.linalg.norm(a[:, None] - a[None, :], axis=2) - np.linalg.norm(b[:, None] - b[None, :], axis=2)))
            if error > 2e-7:
                raise ValueError('Mechanical finger component changed shape')
        protected_count += int(protected.sum())
        moved_count += int(changed.sum())
    if minimum_jacobian <= .02 or minimum_area <= .03 or protected_count != entry['protectedCuffVertices']:
        raise ValueError('Mechanical hand source pose failed its cuff or surface guard')
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Mechanical recipe differs from the client-reviewed hand: ' + alias)
    return output, {'movedVertices': moved_count, 'protectedCuffVertices': protected_count, 'sha256': sha256}


def mechanical_surface_gap(first, second):
    """Conservative triangle contact guard for adjoining mechanical thumb parts."""
    for edges, triangles in ((first, second), (second, first)):
        e1, e2 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        for face in edges:
            for i, j in ((0, 1), (1, 2), (2, 0)):
                origin, direction = face[i], face[j] - face[i]
                h = np.cross(direction, e2)
                determinant = np.einsum('ij,ij->i', e1, h)
                valid = np.abs(determinant) > 1e-12
                inverse = np.zeros(len(determinant))
                inverse[valid] = 1 / determinant[valid]
                delta = origin - triangles[:, 0]
                u = np.einsum('ij,ij->i', delta, h) * inverse
                q = np.cross(delta, e1)
                v = q @ direction * inverse
                t = np.einsum('ij,ij->i', e2, q) * inverse
                if (valid & (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7) & (t >= -1e-7) & (t <= 1 + 1e-7)).any():
                    return 0.
    minimum = float('inf')
    for points, triangles in ((first.reshape(-1, 3), second), (second.reshape(-1, 3), first)):
        a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        e0, e1 = b - a, c - a
        normal = np.cross(e0, e1)
        norm2 = (normal * normal).sum(1)
        aa, ab, bb = (e0 * e0).sum(1), (e0 * e1).sum(1), (e1 * e1).sum(1)
        denominator = aa * bb - ab * ab
        valid = denominator > 1e-24
        for point in points:
            delta = point - a
            da, db = (delta * e0).sum(1), (delta * e1).sum(1)
            u, v = np.zeros(len(a)), np.zeros(len(a))
            u[valid] = (bb[valid] * da[valid] - ab[valid] * db[valid]) / denominator[valid]
            v[valid] = (aa[valid] * db[valid] - ab[valid] * da[valid]) / denominator[valid]
            inside = valid & (u >= 0) & (v >= 0) & (u + v <= 1)
            if inside.any():
                minimum = min(minimum, float(np.sqrt(((delta[inside] * normal[inside]).sum(1) ** 2 / norm2[inside]).min())))
            for start, end in ((a, b), (b, c), (c, a)):
                edge = end - start
                length2 = (edge * edge).sum(1)
                weight = np.divide(((point - start) * edge).sum(1), length2, out=np.zeros(len(a)), where=length2 > 0)
                distance = np.linalg.norm(point - start - np.clip(weight, 0, 1)[:, None] * edge, axis=1)
                minimum = min(minimum, float(distance.min()))
    return minimum


def build_wrist_pose(recipe, entry):
    """Bend an own-source grasp at its wrist while retaining the authored cuff."""
    from scipy.spatial.transform import Rotation
    if recipe['underPose'].get('kind') != 'ownSourceOrganic':
        raise ValueError('Wrist articulation requires an owned organic grasp')
    if entry['sourceBlob'] != entry['underPose']['sourceBlob']:
        raise ValueError('Wrist articulation must use the same authored source')
    output, _ = build_organic(recipe['underPose'], entry['underPose'])
    _, parts = organic_geometry(output)
    affine = np.array(entry['sourceToCanonicalAffine'])
    inverse = np.linalg.inv(affine[:3])
    pivot = np.array(entry['wristPivot'])
    vector = np.array(entry['wristFullRotationVector'])
    fraction = entry['wristFraction']
    cut, blend = entry['wristCutCanonicalZ'], entry['wristBlendCanonicalMetres']
    if not np.isfinite(np.r_[affine.ravel(), pivot, vector, fraction, cut, blend]).all() or blend <= 0:
        raise ValueError('Invalid wrist articulation frame')

    def field(root):
        local = (root - affine[3]) @ inverse
        weight = np.clip((cut - local[:, 2]) / blend, 0, 1)
        weight = weight * weight * (3 - 2 * weight)
        return pivot + Rotation.from_rotvec(weight[:, None] * vector * fraction).apply(root - pivot)

    alias = Path(entry['output']).stem
    protected_count, moved_count = 0, 0
    for index, (mesh, matrix, vertices, root, rendered) in enumerate(parts):
        if not rendered:
            continue
        local = (root - affine[3]) @ inverse
        fixed = local[:, 2] >= cut
        posed = field(root)
        faces = np.array(mesh['faces'])[:, :3]
        probes, epsilon = root[faces].mean(1), 1e-6
        jacobian = np.stack([(field(probes + np.eye(3)[axis] * epsilon) -
                              field(probes - np.eye(3)[axis] * epsilon)) / (2 * epsilon)
                             for axis in range(3)], axis=2)
        old_area = np.linalg.norm(np.cross(root[faces[:, 1]] - root[faces[:, 0]], root[faces[:, 2]] - root[faces[:, 0]]), axis=1)
        new_area = np.linalg.norm(np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]]), axis=1)
        if np.linalg.det(jacobian).min() <= .02 or (new_area[old_area > 1e-12] / old_area[old_area > 1e-12]).min() <= .03:
            raise ValueError('Wrist articulation folds or collapses its source surface')
        bound = (np.column_stack([posed, np.ones(len(vertices))]) @ np.linalg.inv(matrix).T)[:, :3]
        bound[fixed] = vertices[fixed]
        changed = np.linalg.norm(bound - vertices, axis=1) > 1e-10
        output = patch(output, bound, changed, fixed, alias, index)
        protected_count += int(fixed.sum())
        moved_count += int(changed.sum())
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Wrist articulation differs from its pinned native payload: ' + alias)
    return output, {'sha256': sha256, 'protectedCuffVertices': protected_count, 'movedVertices': moved_count}


def build_droid_correction(recipe, entry):
    from scipy.spatial.transform import Rotation
    original = source(entry['sourceBlob'])
    output, _ = build_droid(recipe['underPose'], entry['underPose'])
    _, parts = organic_geometry(original)
    _, posed_parts = organic_geometry(output)
    basis = np.array(entry['jointReferenceToOwn'])
    axes, origin = basis[:3], basis[3]
    alias = Path(entry['output']).stem
    for index, ((mesh, matrix, vertices, root, rendered), (prior, _, prior_vertices, prior_root, _)) in enumerate(zip(parts, posed_parts)):
        if not rendered:
            continue
        local = (root - origin) @ axes.T
        faces = np.array(mesh['faces'])[:, :3]
        if recipe['correction'] == 'thumbField':
            posed = droid_field(local, recipe)
            protected = local[:, 2] >= -.045
            probes = local[faces].mean(1)
            epsilon = 1e-6
            jacobian = np.stack([(droid_field(probes + np.eye(3)[axis] * epsilon, recipe) -
                                  droid_field(probes - np.eye(3)[axis] * epsilon, recipe)) / (2 * epsilon)
                                 for axis in range(3)], axis=2)
            before = np.linalg.norm(np.cross(local[faces[:, 1]] - local[faces[:, 0]], local[faces[:, 2]] - local[faces[:, 0]]), axis=1)
            after = np.linalg.norm(np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]]), axis=1)
            if np.linalg.det(jacobian).min() <= .02 or (after[before > 1e-12] / before[before > 1e-12]).min() <= .03:
                raise ValueError('Thumb articulation folds or collapses the source surface')
            target = posed @ axes + origin
            bound = (np.column_stack([target, np.ones(len(target))]) @ np.linalg.inv(matrix).T)[:, :3]
            bound[protected] = vertices[protected]
            output = patch(output, bound, np.linalg.norm(bound - vertices, axis=1) > 1e-10, protected, alias, index)
        elif local[:, 1].mean() > .06 and local[:, 2].mean() > -.08:
            labels = component_labels(mesh)[2]
            fixed_part = min(np.unique(labels), key=lambda part: local[labels == part, 1].mean())
            moving = labels != fixed_part
            fixed = ~moving
            points = local[fixed]
            pivot = points[points[:, 1] > points[:, 1].max() - .003].mean(0)
            posed = (prior_root - origin) @ axes.T
            posed[moving] = (posed[moving] - pivot) @ Rotation.from_euler('x', recipe['jointDegrees'], degrees=True).as_matrix().T + pivot
            if mechanical_surface_gap(posed[faces[fixed[faces].all(1)]], posed[faces[moving[faces].all(1)]]) >= .0025:
                raise ValueError('Mechanical thumb articulation opens its joint')
            target = posed @ axes + origin
            bound = (np.column_stack([target, np.ones(len(target))]) @ np.linalg.inv(matrix).T)[:, :3]
            bound[fixed] = prior_vertices[fixed]
            output = patch(output, bound, moving, fixed, alias, index)
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Thumb correction differs from the client-reviewed hand: ' + alias)
    return output, {'sha256': sha256}


def build_joint_pose(recipe, entry):
    original = source(entry['sourceBlob'])
    _, parts = organic_geometry(original)
    index = entry['meshIndex']
    mesh, matrix, vertices, root, _ = parts[index]
    basis = np.array(entry['poseReferenceToOwn'])
    axes, origin = basis[:3], basis[3]
    if not np.allclose(axes @ axes.T, np.eye(3), atol=1e-7):
        raise ValueError('Joint reference changes the source hand proportions')
    roles = np.array(entry['componentRoles'])
    if len(roles) != len(vertices):
        raise ValueError('Mechanical component role inventory changed')
    posed = root.copy()
    for joint in recipe['joints']:
        selected = roles == joint['component']
        pivot = np.array(joint['pivot']) @ axes + origin
        direction = np.array(joint['axis']) @ axes * np.linalg.det(axes)
        posed[selected] = rotate(root[selected], pivot, direction, np.deg2rad(joint['degrees']))
    bound = (np.column_stack([posed, np.ones(len(posed))]) @ np.linalg.inv(matrix).T)[:, :3]
    protected = np.linalg.norm(posed - root, axis=1) < 1e-7
    bound[protected] = vertices[protected]
    # The original operation keeps sub-micron hinge points fixed. Preserve those
    # source bits rather than accumulating a new pose from the previous output.
    output = patch(original, bound, ~protected, protected, Path(entry['output']).stem, index)
    actual = np.array(geometry(output, index)['verts'])
    for role in np.unique(roles):
        a, b = vertices[roles == role], actual[roles == role]
        if np.max(np.abs(np.linalg.norm(a[:, None] - a[None, :], axis=2) - np.linalg.norm(b[:, None] - b[None, :], axis=2))) > 2e-7:
            raise ValueError('Mechanical joint changed its original rigid segment')
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Joint recipe differs from its client-reviewed hand')
    return output, {'sha256': sha256, 'protectedCuffVertices': int(protected.sum())}


def build_rigid_pose(recipe, entry):
    original = source(entry['sourceBlob'])
    model, meshes = organic_geometry(original)
    output = original
    for part in entry['parts']:
        index = part['meshIndex']
        mesh, matrix, vertices, _, _ = meshes[index]
        labels = np.array(part.get('componentRoles', component_labels(mesh)[2]))
        posed = vertices.copy()
        for movement in part['movements']:
            rotation = np.array(movement['rotation'])
            world = matrix[:3, :3] @ rotation @ np.linalg.inv(matrix[:3, :3])
            if not np.allclose(world @ world.T, np.eye(3), atol=1e-7) or np.linalg.det(world) < 0:
                raise ValueError('Imported mechanical part changes shape or reflection')
            selected = labels == movement['component']
            posed[selected] = vertices[selected] @ rotation.T + np.array(movement['translation'])
        fixed = np.array(part.get('fixedVertices', []), dtype=int)
        posed[fixed] = vertices[fixed]
        protected = np.zeros(len(vertices), bool)
        protected[part['protectedVertices']] = True
        if not protected[part['cuffVertices']].all():
            raise ValueError('Mechanical pose changes its authored cuff rim')
        posed[protected] = vertices[protected]
        output = patch(output, posed, np.linalg.norm(posed - vertices, axis=1) > 1e-10, protected,
                       Path(entry['output']).stem, index)
        actual = np.array(geometry(output, index)['verts'])
        for label in np.unique(labels):
            a, b = vertices[labels == label], actual[labels == label]
            if np.max(np.abs(np.linalg.norm(a[:, None] - a[None, :], axis=2) - np.linalg.norm(b[:, None] - b[None, :], axis=2))) > 2e-7:
                raise ValueError('Imported mechanical segment changed shape')
    renamed = bytearray(output)
    for index, name in entry.get('nodeNames', {}).items():
        node = model.nodes[int(index)]
        if node[1] is None or not name.isascii() or len(name) > 31:
            raise ValueError('Invalid hand-local mesh node name')
        renamed[node[4] + 32:node[4] + 64] = name.encode('ascii').ljust(32, b'\0')
    output = bytes(renamed)
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Rigid articulation differs from its client-reviewed hand')
    return output, {'sha256': sha256}


def build_authored_pose(recipe, entry):
    """Apply a pinned own-source sculpt, regenerating native normals and bounds."""
    original = source(entry['sourceBlob'])
    _, meshes = organic_geometry(original)
    output = original
    for part in entry['parts']:
        index = part['meshIndex']
        mesh, _, vertices, _, _ = meshes[index]
        posed = vertices.copy()
        seen = set()
        for vertex, x, y, z in part['positions']:
            if not isinstance(vertex, int) or vertex < 0 or vertex >= len(vertices) or vertex in seen:
                raise ValueError('Invalid authored hand vertex edit')
            seen.add(vertex)
            posed[vertex] = [x, y, z]
        if not np.isfinite(posed).all():
            raise ValueError('Authored hand pose contains non-finite coordinates')
        protected = np.zeros(len(vertices), bool)
        protected[part['protectedVertices']] = True
        changed = np.any(posed != vertices, axis=1)
        if changed[protected].any() or not protected[part['cuffVertices']].all():
            raise ValueError('Authored hand pose moves its protected cuff')
        faces = np.array(mesh['faces'])[:, :3]
        before = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
        after = np.linalg.norm(np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]]), axis=1)
        if (after[before > 1e-12] / before[before > 1e-12]).min() <= .03:
            raise ValueError('Authored hand pose collapses an original face')
        output = patch(output, posed, changed, protected, Path(entry['output']).stem, index)
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Authored source pose differs from its client-reviewed hand')
    return output, {'sha256': sha256}


def skin_buffers(data, mesh):
    """Read raw four-slot streams without dropping zero-weight bone slots."""
    offset = mesh['_mesh_offset']
    skin = offset + 512
    uint = lambda at: struct.unpack_from('<I', data, at)[0]
    raw = 12 + uint(4)
    stride = uint(offset + 440)
    count = len(mesh['verts'])
    weights_at = raw + uint(skin + 12)
    weights = np.array([struct.unpack_from('<4f', data, weights_at + i * (stride or 16))
                        for i in range(count)])
    slots = np.array([struct.unpack_from('<4h', data, raw + uint(skin + 16) + i * (stride or 8))
                      for i in range(count)])
    return skin, weights_at, stride or 16, weights, slots


def build_skin_pose(recipe, entry):
    """Build an authored finger skin with its original donor, slots and binds."""
    from NeckSkinBindings import body_model, skins
    from scipy.spatial.transform import Rotation
    original = source(entry['sourceBlob'])
    if hashlib.sha256(original).hexdigest() != entry['sourceSha256']:
        raise ValueError('Skin hand source fingerprint differs')
    skeleton = source(entry['skeletonBlob'])
    skeleton_path = ROOT / entry['skeleton']
    current = skeleton_path.read_bytes() if skeleton_path.is_file() else subprocess.check_output(
        ['git', '-C', str(ROOT), 'show', 'HEAD:' + entry['skeleton']])
    # Supermodel/animation-bank changes are allowed; native bone order, paths and
    # default bind controllers must still match the pinned donor.
    donor = body_model(skeleton)
    if [n[:4] for n in donor.nodes] != [n[:4] for n in body_model(current).nodes]:
        raise ValueError('Skin hand donor bone order or bind transforms changed')
    index = entry['meshIndex']
    mesh = geometry(original, index, skeleton)
    if 'weights' not in mesh:
        raise ValueError('Authored finger skin recipe requires a skin mesh')
    vertices = np.array(mesh['verts'])
    posed = vertices.copy()
    protected = np.zeros(len(vertices), bool)
    protected[entry['protectedVertices']] = True
    names = [n[0] for n in donor.nodes]
    skin, weight_at, stride, weights, slots = skin_buffers(original, mesh)
    wanted = weights.copy()
    for field, target, width in [('positions', posed, 3), ('weights', wanted, 4)]:
        seen = set()
        for edit in entry[field]:
            vertex, *values = edit
            if (not isinstance(vertex, int) or not 0 <= vertex < len(vertices)
                    or vertex in seen or len(values) != width):
                raise ValueError('Invalid authored skin vertex edit')
            seen.add(vertex)
            target[vertex] = values
    if not np.isfinite(posed).all() or not np.isfinite(wanted).all():
        raise ValueError('Authored skin contains non-finite values')
    if (wanted < 0).any() or (wanted > 1.00001).any():
        raise ValueError('Authored skin contains invalid weights')
    sums, old_sums = wanted.sum(1), weights.sum(1)
    if (sums < np.minimum(old_sums, 1) - 2e-7).any() or (sums > np.maximum(old_sums, 1) + 2e-7).any():
        raise ValueError('Skin finger weights drift outside their original normalization range')
    changed = np.any(posed != vertices, axis=1)
    changed_weights = np.any(wanted != weights, axis=1)
    if changed[protected].any() or changed_weights[protected].any():
        raise ValueError('Authored skin changes its protected cuff')
    # Independently derive the top 5 mm cuff rim from the original hand bind.
    bone = names.index(Path(entry['source']).stem[9].lower() + 'hand_g')
    uint = lambda at: struct.unpack_from('<I', original, at)[0]
    w, x, y, z = struct.unpack_from('<4f', original, 12 + uint(skin + 28) + bone * 16)
    inverse = np.eye(4)
    inverse[:3, :3] = Rotation.from_quat([x, y, z, w]).as_matrix()
    inverse[:3, 3] = struct.unpack_from('<3f', original, 12 + uint(skin + 40) + bone * 12)
    local = (np.c_[vertices, np.ones(len(vertices))] @ inverse.T)[:, :3]
    cuff = local[:, 2] >= local[:, 2].max() - .005
    if not protected[cuff].all() or not protected[entry['cuffVertices']].all():
        raise ValueError('Authored skin recipe does not protect the source cuff rim')
    output = bytearray(patch(original, posed, changed, protected, Path(entry['output']).stem, index, skeleton))
    for vertex in np.flatnonzero(changed_weights):
        struct.pack_into('<4f', output, weight_at + int(vertex) * stride, *wanted[vertex])
    # A sub-float32 bind solve can refresh a face even when its stored vertices
    # stay identical. Recompute only its plane from the final stored geometry.
    faces = np.array(mesh['faces'])[:, :3]
    stored = np.array(geometry(bytes(output), index, skeleton)['verts'])
    face_at = 12 + uint(mesh['_mesh_offset'] + 8)
    seen = set()
    for face in entry['refreshedFaces']:
        if not isinstance(face, int) or not 0 <= face < len(faces) or face in seen:
            raise ValueError('Invalid authored skin face refresh')
        seen.add(face)
        triangle = stored[faces[face]]
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        length = np.linalg.norm(normal)
        if length < 1e-10:
            raise ValueError('Authored skin refresh has a collapsed face')
        normal /= length
        struct.pack_into('<4f', output, face_at + face * 32, *normal, -float(normal @ triangle[0]))
    # Validate native external-bone consistency after changing the weight stream.
    skins(bytes(output))
    now = geometry(bytes(output), index, skeleton)
    for key in mesh.keys() - {'verts', 'normals', 'weights', '_mesh_offset'}:
        if mesh[key] != now[key]:
            raise ValueError(f'Authored skin field changed: {key}')
    for key in ('verts', 'normals'):
        if not np.array_equal(np.array(mesh[key])[protected], np.array(now[key])[protected]):
            raise ValueError(f'Protected skin cuff {key} changed')
    if not np.array_equal(skin_buffers(output, now)[4], slots):
        raise ValueError('Authored skin changes its bone slots')
    sha256 = hashlib.sha256(output).hexdigest()
    if sha256 != entry['outputSha256']:
        raise ValueError('Authored skin differs from its client-reviewed native payload')
    return bytes(output), {'sha256': sha256}


def build(recipe, entry):
    if recipe.get('kind') == 'ownSourceWrist':
        return build_wrist_pose(recipe, entry)
    if recipe.get('kind') == 'ownSourceSkin':
        return build_skin_pose(recipe, entry)
    if recipe.get('kind') == 'ownSourceAuthored':
        return build_authored_pose(recipe, entry)
    if recipe.get('kind') == 'ownSourceJointPose':
        return build_joint_pose(recipe, entry)
    if recipe.get('kind') == 'ownSourceRigid':
        return build_rigid_pose(recipe, entry)
    if recipe.get('kind') == 'ownSourceDroidCorrection':
        return build_droid_correction(recipe, entry)
    if recipe.get('kind') == 'ownSourceDroid':
        return build_droid(recipe, entry)
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
    if entry.get('outputSha256') and hashlib.sha256(result).hexdigest() != entry['outputSha256']:
        raise ValueError('Hand recipe differs from its client-reviewed payload')
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
