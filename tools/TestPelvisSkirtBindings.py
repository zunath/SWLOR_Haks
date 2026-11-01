"""Guard release 035 skinning, canonical 034 identity and local front/left clearance."""
from pathlib import Path
import hashlib
import json
import os
import struct
import unittest

import numpy as np

import ChestSkirtBindings as skirts
import CompileModels as mdl
import GenerateTintMapAssets as tint
import PelvisSkirtBindings as pelvis
import RestoreRobeBodyTracks as body_tracks
from RobePoseAudit import Model


class PelvisSkirtBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = json.loads(pelvis.MANIFEST.read_text())['models']
        cls.sources = {name: skirts.source(entry['sourceBlob']) for name, entry in cls.entries.items()}
        cls.outputs = {name: pelvis.generate(entry) for name, entry in cls.entries.items()}

    def test_recipes_reproduce_the_reviewed_binary(self):
        self.assertEqual({'pfh0_pelvis034', 'pmh0_pelvis034'}, set(self.entries))
        for name, entry in self.entries.items():
            with self.subTest(model=name):
                self.assertEqual(entry['sha256'], hashlib.sha256(self.outputs[name]).hexdigest())
                self.assertEqual(self.outputs[name], pelvis.generate(entry))

    def test_release_provenance_is_pinned(self):
        self.assertEqual('f4bb7e0c7b9d174c945afedf637ea2389047dfa9', self.entries['pfh0_pelvis034']['sourceBlob'])
        self.assertEqual('072dcdb02372a18044a20a29db4530b45d818427', self.entries['pmh0_pelvis034']['sourceBlob'])

    def test_only_local_geometry_metadata_and_canonical_identity_change(self):
        for name, source in self.sources.items():
            with self.subTest(model=name):
                current = self.outputs[name]
                self.assertEqual(len(source), len(current))
                garment = pelvis.Garment(source)
                displacement = garment.displacement(self.entries[name]['parameters'])
                changed = np.any(displacement != 0, axis=1)
                allowed = np.zeros(len(source), dtype=bool)

                def allow(start, size):
                    allowed[start:start + size] = True

                original = (name[:-3] + '035').encode('ascii')
                self.assertEqual(3, source.count(original))
                cursor = 0
                for _ in range(3):
                    cursor = source.index(original, cursor)
                    allow(cursor, len(original))
                    self.assertEqual(name.encode('ascii'), current[cursor:cursor + len(original)])
                    cursor += len(original)
                allow(148, 28)
                skin = garment.skin
                offset = garment.mesh['_mesh_offset']
                allow(offset + 20, 40)
                for vertex in np.flatnonzero(changed):
                    allow(skin['positions'] + vertex * (skin['stride'] or 12), 12)
                stride = struct.unpack_from('<I', source, offset + 440)[0]
                normals = 12 + struct.unpack_from('<I', source, 4)[0] + struct.unpack_from('<I', source, offset + 468)[0]
                faces = 12 + struct.unpack_from('<I', source, offset + 8)[0]
                for vertex in range(pelvis.SKIRT_VERTICES):
                    if (garment.pos[vertex, 1] > .0312
                            or (garment.pos[vertex, 0] > .10 and garment.pos[vertex, 1] > -.02)):
                        allow(normals + vertex * (stride or 12), 12)
                for index, face in enumerate(garment.mesh['faces']):
                    if np.any(changed[face[:3]]):
                        allow(faces + index * 32, 16)
                differences = np.frombuffer(source, dtype=np.uint8) != np.frombuffer(current, dtype=np.uint8)
                self.assertFalse(np.any(differences & ~allowed),
                                 'Rig, UVs, protected geometry or unrelated binary data changed')
                after = next(mesh for _, _, mesh in mdl.binary_nodes(current) if mesh and 'weights' in mesh)
                for field in ('faces', 'tverts', 'tverts1', 'tverts2', 'tverts3', 'colors', 'weights'):
                    self.assertEqual(garment.mesh.get(field), after.get(field), field)
                # Explicitly guard every slot, including zero-weight indices, all bone tables and inverse binds.
                for pointer, size, count in (('weights', 16, skin['count']), ('indices', 8, skin['count']),
                                            ('palette_offset', 34, 1), ('bone_map_offset', 2 * len(garment.names), 1),
                                            ('inverse_rotations', 16 * len(garment.names), 1),
                                            ('inverse_translations', 12 * len(garment.names), 1)):
                    for vertex in range(count):
                        start = skin[pointer] + vertex * (skin['stride'] or size)
                        self.assertEqual(source[start:start + size], current[start:start + size], pointer)

    def test_clearance_is_local_and_keeps_rear_waist_hem_and_width(self):
        for name, source in self.sources.items():
            with self.subTest(model=name):
                garment = pelvis.Garment(source)
                expected = garment.displacement(self.entries[name]['parameters'])
                before = garment.pos
                after = np.array(skirts.rest_positions(self.outputs[name]))
                np.testing.assert_allclose(after - before, expected, atol=2e-7)
                np.testing.assert_allclose(after[:, 0].min(), before[:, 0].min(), atol=1e-8)
                np.testing.assert_allclose(after[:, 0].max(), before[:, 0].max(), atol=1e-8)
                protected = ~np.any(expected != 0, axis=1)
                np.testing.assert_array_equal(after[protected], before[protected])
                self.assertLessEqual(np.max(expected[:, 1]), .060000001)
                self.assertGreaterEqual(np.min(expected[:, 0]), -.025)
                self.assertLessEqual(np.max(expected[:, 0]), .025)
                rear = (before[:, 1] <= -.02) | ((before[:, 1] <= .0312) & (before[:, 0] <= .10))
                self.assertTrue(np.all(expected[rear] == 0))
                self.assertTrue(np.all(expected[(before[:, 2] <= .60) | (before[:, 2] >= 1.12)] == 0))
                for field in ('frontClearance', 'leftFrontClearance', 'leftSideClearance', 'rightFrontClearance', 'rightSideClearance'):
                    for value in (-.01, .2, float('nan'), float('inf')):
                        invalid = {**self.entries[name]['parameters'], field: value}
                        with self.assertRaises(ValueError, msg=f'{field}={value}'):
                            garment.displacement(invalid)

    def test_bounds_normals_and_face_planes_match_the_new_positions(self):
        for name, data in self.outputs.items():
            with self.subTest(model=name):
                mesh = next(mesh for _, _, mesh in mdl.binary_nodes(data) if mesh and 'weights' in mesh)
                offset = mesh['_mesh_offset']
                points = np.array(mesh['verts'])
                np.testing.assert_allclose(struct.unpack_from('<3f', data, offset + 20), points.min(0), atol=1e-6)
                np.testing.assert_allclose(struct.unpack_from('<3f', data, offset + 32), points.max(0), atol=1e-6)
                np.testing.assert_allclose(struct.unpack_from('<3f', data, offset + 48), points.mean(0), atol=1e-6)
                radius = struct.unpack_from('<f', data, offset + 44)[0]
                self.assertAlmostEqual(radius, np.linalg.norm(points - points.mean(0), axis=1).max(), places=6)
                np.testing.assert_allclose(np.linalg.norm(np.array(mesh['normals'])[:pelvis.SKIRT_VERTICES], axis=1), 1, atol=2e-6)
                faces = 12 + struct.unpack_from('<I', data, offset + 8)[0]
                garment = pelvis.Garment(self.sources[name])
                changed = np.any(garment.displacement(self.entries[name]['parameters']) != 0, axis=1)
                for index, face in enumerate(mesh['faces']):
                    if not np.any(changed[face[:3]]):
                        continue
                    triangle = points[face[:3]]
                    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
                    length = np.linalg.norm(normal)
                    if length < 1e-8:
                        continue
                    plane = np.array(struct.unpack_from('<4f', data, faces + index * 32))
                    self.assertGreater(np.dot(plane[:3], normal / length), .999)
                    self.assertLess(np.max(abs(triangle @ plane[:3] + plane[3])), 2e-6)

    def test_uv_seams_and_rear_motion_are_preserved_across_poses(self):
        game_data = Path(os.environ.get('NWN_GAME_DATA', r'C:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights\data'))
        self.assertTrue((game_data / 'nwn_base.key').is_file(), 'Set NWN_GAME_DATA to run pelvis pose checks')
        active = tint.find_active_models()
        library = body_tracks.library_for(active, game_data)
        for name, current in self.outputs.items():
            bones, _ = skirts.skeleton(current)
            body = Model(active[bones[0]].read_bytes(), False)
            source_points = np.array(skirts.rest_positions(self.sources[name]))[:pelvis.SKIRT_VERTICES]
            _, groups = np.unique(np.round(source_points, 5), axis=0, return_inverse=True)
            pairs = []
            for group in range(groups.max() + 1):
                vertices = np.where(groups == group)[0]
                pairs.extend((vertices[0], vertex) for vertex in vertices[1:])
            # Actual rear panels stay fixed; only the documented outer-right seam
            # may extend behind the original front plane.
            rear = (source_points[:, 1] <= -.02) | ((source_points[:, 1] <= .0312) & (source_points[:, 0] <= .10))
            for clip_name in ('pause1', 'pause2', 'pausebrd', 'pausetrd', 'custom4lp', 'custom52lp', 'walk', 'run'):
                owner = mdl.supermodel(active[bones[0]].read_bytes())
                while owner:
                    model = library.model(owner)
                    if clip_name in model.clips:
                        clip = model.clips[clip_name]
                        break
                    owner = model.parent
                else:
                    self.fail(f'{name} lacks {clip_name}')
                for phase in (0, .25, .5, .75):
                    posed = skirts.rest_positions(current, body.pose(clip, clip[0] * phase))
                    original = np.array(skirts.rest_positions(self.sources[name], body.pose(clip, clip[0] * phase)))
                    with self.subTest(model=name, clip=clip_name, phase=phase):
                        np.testing.assert_array_equal(np.array(posed)[:pelvis.SKIRT_VERTICES][rear], original[:pelvis.SKIRT_VERTICES][rear])
                        for left, right in pairs:
                            np.testing.assert_allclose(np.array(posed[left]) - posed[right], original[left] - original[right], atol=2e-6)

    def test_skirt_intersections_with_actual_thigh_hulls_are_reduced(self):
        hulls = json.loads(Path(__file__).with_name('PelvisSkirtThighHulls.json').read_text())
        game_data = Path(os.environ.get('NWN_GAME_DATA', r'C:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights\data'))
        self.assertTrue((game_data / 'nwn_base.key').is_file(), 'Set NWN_GAME_DATA to run pelvis pose checks')
        active = tint.find_active_models()
        library = body_tracks.library_for(active, game_data)
        phases = {'pause1': (0, .5), 'pause2': (0, .5), 'pausebrd': (0, .5), 'pausetrd': (0, .5),
                  'custom4lp': (0, .5), 'custom52lp': (0, .5), 'walk': (0, .25, .5, .75),
                  'run': (0, .125, .3125, .375, .5, .625, .8125, .875)}
        for name, current in self.outputs.items():
            root = name.split('_')[0]
            body = Model(active[root].read_bytes(), False)
            local_hulls = []
            for hull in hulls[root]:
                points = []
                for entry in hull['sources']:
                    data = (pelvis.ROOT / entry['path']).read_bytes()
                    self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest(),
                                     'Thigh geometry changed; rebuild its reference convex hull')
                    model = Model(data, False)
                    transforms = model.pose()
                    for mesh_name, _, mesh in mdl.binary_nodes(data):
                        if not mesh or not struct.unpack_from('<I', data, mesh['_mesh_offset'] + 108)[0]:
                            continue
                        transform = pelvis.transform(transforms[mesh_name])
                        points.extend(np.array(mesh['verts']) @ transform[:, :3].T + transform[:, 3])
                points = np.array(points)
                triangles = points[np.array(hull['triangles'])]
                normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
                normals /= np.linalg.norm(normals, axis=1)[:, None]
                inward = np.einsum('ij,ij->i', normals, points.mean(0) - triangles[:, 0]) > 0
                normals[inward] *= -1
                offsets = -np.einsum('ij,ij->i', normals, triangles[:, 0])
                self.assertLessEqual(np.max(points @ normals.T + offsets), 2e-6)
                local_hulls.append((hull['bone'], normals, offsets))
            faces = next(mesh['faces'] for _, _, mesh in mdl.binary_nodes(current) if mesh and 'weights' in mesh)
            faces = np.array([face[:3] for face in faces if max(face[:3]) < pelvis.SKIRT_VERTICES])
            areas = {'source': [], 'fitted': []}
            left_contacts = {'source': [], 'fitted': []}
            parade_contacts = {'source': [], 'fitted': []}
            tired_contacts = {'source': [], 'fitted': []}
            right_tired_contacts = {'source': [], 'fitted': []}
            front = np.mean(np.array(skirts.rest_positions(self.sources[name]))[faces, 1], axis=1) > .0312
            movement_front = {'source': [], 'fitted': []}
            for clip_name, times in phases.items():
                owner = mdl.supermodel(active[root].read_bytes())
                while owner:
                    model = library.model(owner)
                    if clip_name in model.clips:
                        clip = model.clips[clip_name]
                        break
                    owner = model.parent
                else:
                    self.fail(f'{name} lacks {clip_name}')
                reference_phases = times
                # The native right-side screenshot is captured nine seconds after
                # starting Tired. Include nearby capture/queue timing as well.
                if clip_name == 'pausetrd':
                    times = (*times, *((seconds % clip[0]) / clip[0] for seconds in (8.85, 9., 9.15, 9.3)))
                for phase in times:
                    pose = body.pose(clip, clip[0] * phase)
                    world_hulls = []
                    for bone, normals, offsets in local_hulls:
                        transform = pelvis.transform(pose[bone])
                        world_normals = normals @ np.linalg.inv(transform[:, :3])
                        world_offsets = offsets - world_normals @ transform[:, 3]
                        world_hulls.append((bone, np.column_stack((world_normals, world_offsets))))
                    for kind, data in (('source', self.sources[name]), ('fitted', current)):
                        triangles = np.array(skirts.rest_positions(data, pose))[faces]
                        area = 0.
                        for bone, planes in world_hulls:
                            distances = np.einsum('tvi,pi->tvp', triangles, planes[:, :3]) + planes[None, None, :, 3]
                            intersecting = ~np.any(np.all(distances > 0, axis=1), axis=1)
                            inside = sum(self._inside_hull_area(triangle, planes) for triangle in triangles[intersecting])
                            area += inside
                            if bone == 'lthigh_g' and clip_name == 'pause2':
                                left_contacts[kind].append(inside)
                            # Retain the original left reference poses. The added
                            # capture-time samples specifically exercise the right side.
                            if bone == 'lthigh_g' and clip_name == 'pausetrd' and phase in reference_phases:
                                tired_contacts[kind].append(inside)
                            if bone == 'rthigh_g' and clip_name == 'pausetrd':
                                right_tired_contacts[kind].append(inside)
                            if bone == 'lthigh_g' and clip_name == 'custom52lp':
                                parade_contacts[kind].append(inside)
                            if clip_name in ('walk', 'run'):
                                movement_front[kind].append(sum(self._inside_hull_area(triangle, planes) for triangle in triangles[intersecting & front]))
                        areas[kind].append(area)
            with self.subTest(model=name):
                self.assertGreater(np.mean(left_contacts['source']), .004, 'Exercise the reported left contact')
                self.assertLess(np.mean(left_contacts['fitted']), np.mean(left_contacts['source']) * .30,
                                'Remove at least 70% of the left-thigh Pause2 contact without reweighting')
                self.assertLess(np.mean(parade_contacts['fitted']), np.mean(parade_contacts['source']) * .60,
                                'Remove at least 40% of the left-thigh parade-rest contact')
                self.assertLess(np.mean(tired_contacts['fitted']), np.mean(tired_contacts['source']) * .05,
                                'Retain at least 95% left-thigh Tired improvement at the reference poses')
                self.assertLess(np.mean(right_tired_contacts['fitted']), np.mean(right_tired_contacts['source']) * .10,
                                'Remove at least 90% of the native-observed right-side Tired contact')
                self.assertLess(np.mean(movement_front['fitted']), np.mean(movement_front['source']) * .85,
                                'Moving front cloth must gain measurable clearance')
                self.assertTrue(np.all(np.array(areas['fitted']) <= np.array(areas['source']) + 1e-6),
                                'No sampled pose may gain thigh intersection area')

    @staticmethod
    def _inside_hull_area(triangle, planes):
        polygon = triangle
        for plane in planes:
            if len(polygon) < 3:
                return 0.
            distances = polygon @ plane[:3] + plane[3]
            if np.all(distances <= 1e-9):
                continue
            if np.all(distances > 1e-9):
                return 0.
            clipped = []
            for index in range(len(polygon)):
                start, end = polygon[index - 1], polygon[index]
                before, after = distances[index - 1], distances[index]
                if (before <= 0) != (after <= 0):
                    clipped.append(start + (end - start) * before / (before - after))
                if after <= 0:
                    clipped.append(end)
            polygon = np.array(clipped)
        return sum(np.linalg.norm(np.cross(polygon[index] - polygon[0], polygon[index + 1] - polygon[0])) * .5
                   for index in range(1, len(polygon) - 1))


if __name__ == '__main__':
    unittest.main()
