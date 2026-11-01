"""Guard the fitted pelvis skirt's protected geometry, native metadata and skin seams."""
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

    def test_only_skirt_buffers_and_required_native_metadata_change(self):
        for name, source in self.sources.items():
            with self.subTest(model=name):
                current = self.outputs[name]
                self.assertEqual(len(source), len(current))
                allowed = np.zeros(len(source), dtype=bool)

                def allow(start, size):
                    allowed[start:start + size] = True

                allow(148, 28)
                bones, _ = skirts.skeleton(source)
                for skin in skirts.skins(source, bones):
                    mesh_offset = skin['palette_offset'] - 64 - 512
                    allow(mesh_offset + 20, 40)
                    allow(skin['palette_offset'], 34)
                    allow(skin['bone_map_offset'], len(bones) * 2)
                    for vertex in range(pelvis.SKIRT_VERTICES):
                        for pointer, size in (('positions', 12), ('weights', 16), ('indices', 8)):
                            allow(skin[pointer] + vertex * (skin['stride'] or size), size)
                for _, _, mesh in mdl.binary_nodes(source):
                    if not mesh or 'weights' not in mesh:
                        continue
                    offset = mesh['_mesh_offset']
                    stride = struct.unpack_from('<I', source, offset + 440)[0]
                    normals = 12 + struct.unpack_from('<I', source, 4)[0] + struct.unpack_from('<I', source, offset + 468)[0]
                    faces = 12 + struct.unpack_from('<I', source, offset + 8)[0]
                    for vertex in range(pelvis.SKIRT_VERTICES):
                        allow(normals + vertex * (stride or 12), 12)
                    for index, face in enumerate(mesh['faces']):
                        if min(face[:3]) < pelvis.SKIRT_VERTICES:
                            allow(faces + index * 32, 16)
                differences = np.frombuffer(source, dtype=np.uint8) != np.frombuffer(current, dtype=np.uint8)
                self.assertFalse(np.any(differences & ~allowed), 'Unrelated geometry, UVs, material or binds changed')
                before = next(mesh for _, _, mesh in mdl.binary_nodes(source) if mesh and 'weights' in mesh)
                after = next(mesh for _, _, mesh in mdl.binary_nodes(current) if mesh and 'weights' in mesh)
                for field in ('faces', 'tverts', 'tverts1', 'tverts2', 'tverts3', 'colors'):
                    self.assertEqual(before.get(field), after.get(field), field)
                for field in ('verts', 'weights', 'normals'):
                    self.assertEqual(before[field][pelvis.SKIRT_VERTICES:], after[field][pelvis.SKIRT_VERTICES:], field)
                for skin in skirts.skins(current, bones):
                    for vertex in range(skin['count']):
                        self.assertLessEqual(len(skirts.influences(current, skin, vertex)), 4)

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
                for index, face in enumerate(mesh['faces']):
                    if min(face[:3]) >= pelvis.SKIRT_VERTICES:
                        continue
                    triangle = points[face[:3]]
                    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
                    length = np.linalg.norm(normal)
                    if length < 1e-8:
                        continue
                    plane = np.array(struct.unpack_from('<4f', data, faces + index * 32))
                    self.assertGreater(np.dot(plane[:3], normal / length), .999)
                    self.assertLess(np.max(abs(triangle @ plane[:3] + plane[3])), 2e-6)

    def test_uv_seams_stay_closed_during_idle_walk_and_run(self):
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
            for clip_name in ('pause1', 'pause2', 'walk', 'run'):
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
                    with self.subTest(model=name, clip=clip_name, phase=phase):
                        for left, right in pairs:
                            np.testing.assert_allclose(posed[left], posed[right], atol=2e-6)

    def test_skirt_intersections_with_actual_thigh_hulls_are_reduced(self):
        hulls = json.loads(Path(__file__).with_name('PelvisSkirtThighHulls.json').read_text())
        game_data = Path(os.environ.get('NWN_GAME_DATA', r'C:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights\data'))
        self.assertTrue((game_data / 'nwn_base.key').is_file(), 'Set NWN_GAME_DATA to run pelvis pose checks')
        active = tint.find_active_models()
        library = body_tracks.library_for(active, game_data)
        phases = {'pause1': (0,), 'pause2': (0,), 'walk': (0, .25, .5, .75),
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
                for phase in times:
                    pose = body.pose(clip, clip[0] * phase)
                    world_hulls = []
                    for bone, normals, offsets in local_hulls:
                        transform = pelvis.transform(pose[bone])
                        world_normals = normals @ np.linalg.inv(transform[:, :3])
                        world_offsets = offsets - world_normals @ transform[:, 3]
                        world_hulls.append(np.column_stack((world_normals, world_offsets)))
                    for kind, data in (('source', self.sources[name]), ('fitted', current)):
                        triangles = np.array(skirts.rest_positions(data, pose))[faces]
                        area = 0.
                        for planes in world_hulls:
                            distances = np.einsum('tvi,pi->tvp', triangles, planes[:, :3]) + planes[None, None, :, 3]
                            intersecting = ~np.any(np.all(distances > 0, axis=1), axis=1)
                            area += sum(self._inside_hull_area(triangle, planes) for triangle in triangles[intersecting])
                        areas[kind].append(area)
            with self.subTest(model=name):
                baseline = np.mean(areas['source'])
                self.assertGreater(baseline, .005, 'The sample must exercise the original clipping defect')
                self.assertLess(np.mean(areas['fitted']), baseline * .15,
                                'The fitted skirt must remove at least 85% of intersecting thigh area')
                self.assertLess(max(areas['fitted']), max(areas['source']) * .5,
                                'A large isolated breakthrough must not hide behind the mean')

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
