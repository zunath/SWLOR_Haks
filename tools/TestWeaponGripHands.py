"""Guards for gun-grasp geometry, protected cuffs and native tangent frames."""
import json
import struct
import unittest

import numpy as np

import GenerateWeaponGripHands as grip


class WeaponGripHandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = []
        for recipe in json.loads(grip.MANIFEST.read_text())['recipes']:
            if recipe.get('kind', '').startswith('ownSource'):
                continue
            canonical = grip.geometry(grip.source(recipe['canonicalBlob']), recipe.get('meshIndex'))
            for entry in recipe['models']:
                source = grip.source(entry['sourceBlob'])
                result = (grip.ROOT / entry['output']).read_bytes()
                before, after = (grip.geometry(data, entry.get('meshIndex', recipe.get('meshIndex'))) for data in (source, result))
                affine, _ = grip.correspondence(canonical, before, recipe['affineToleranceMetres'])
                local = (np.array(before['verts']) - affine[3]) @ np.linalg.inv(affine[:3])
                _, cuff = grip.pose(local, canonical, recipe)
                cls.cases.append((entry, source, result, before, after, cuff))

    def test_original_assets_and_cuffs_remain_exact(self):
        for entry, source, _, before, after, cuff in self.cases:
            with self.subTest(hand=entry['source']):
                self.assertEqual((grip.ROOT / entry['source']).read_bytes(), source)
                # Count physical positions: legacy hands may weld the same
                # geometry into fewer vertices than the UV-split reference.
                positions = np.array(before['verts']).round(6)
                protected = len(np.unique(positions[cuff], axis=0))
                self.assertGreaterEqual(protected, 4)
                self.assertGreater(protected / len(np.unique(positions, axis=0)), .05)
                for key in ('verts', 'normals'):
                    np.testing.assert_array_equal(np.array(before[key])[cuff], np.array(after[key])[cuff])
                for key in before.keys() - {'verts', 'normals', '_mesh_offset'}:
                    self.assertEqual(before[key], after[key], key)

    def test_fingers_close_without_collapsed_faces_or_nonfinite_geometry(self):
        for entry, _, _, before, after, _ in self.cases:
            with self.subTest(hand=entry['output']):
                vertices = np.array(after['verts'])
                moved = np.linalg.norm(vertices - np.array(before['verts']), axis=1) > 1e-7
                original = np.array(before['verts']).round(6)
                changed = len(np.unique(original[moved], axis=0))
                self.assertGreater(changed / len(np.unique(original, axis=0)), .2)
                self.assertTrue(np.isfinite(vertices).all())
                triangles = vertices[np.array([face[:3] for face in after['faces']])]
                areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
                self.assertGreater(float(areas.min()), 1e-10)

    def test_updated_tangent_frames_are_orthonormal_and_cuffs_retain_their_frames(self):
        checked = 0
        for entry, source, result, before, after, cuff in self.cases:
            offset = after['_mesh_offset']
            pointer = struct.unpack_from('<I', result, offset + 488)[0]
            if pointer == 0xffffffff:
                continue
            checked += 1
            with self.subTest(hand=entry['output']):
                raw = 12 + struct.unpack_from('<I', result, 4)[0]
                stride = struct.unpack_from('<I', result, offset + 440)[0]
                sign_pointer = struct.unpack_from('<I', result, offset + 496)[0]
                tangents = np.array([struct.unpack_from('<3f', result, raw + pointer + i * (stride or 12)) for i in range(len(after['verts']))])
                signs = np.array([struct.unpack_from('<f', result, raw + sign_pointer + i * (stride or 4))[0] for i in range(len(after['verts']))])
                normals = np.array(after['normals'])
                np.testing.assert_allclose(np.linalg.norm(tangents, axis=1), 1, atol=1e-6)
                np.testing.assert_allclose(np.sum(normals * tangents, axis=1), 0, atol=1e-6)
                self.assertTrue(np.isin(signs, [-1, 1]).all())
                for index in np.flatnonzero(cuff):
                    for stream, width in [(488, 12), (496, 4)]:
                        start = raw + struct.unpack_from('<I', result, offset + stream)[0] + index * (stride or width)
                        self.assertEqual(source[start:start + width], result[start:start + width])
        self.assertGreater(checked, 0)

    def test_mechanical_plates_keep_their_rigid_shapes(self):
        checked = 0
        for recipe in json.loads(grip.MANIFEST.read_text())['recipes']:
            if recipe.get('kind') != 'articulated':
                continue
            canonical = grip.geometry(grip.source(recipe['canonicalBlob']), recipe.get('meshIndex'))
            unique, labels, _ = grip.component_labels(canonical)
            plates = [part for joint in recipe['articulations'] for part in joint['rigid']]
            for entry in recipe['models']:
                mesh_index = entry.get('meshIndex', recipe.get('meshIndex'))
                before = grip.geometry(grip.source(entry['sourceBlob']), mesh_index)
                after = grip.geometry((grip.ROOT / entry['output']).read_bytes(), mesh_index)
                affine, _ = grip.correspondence(canonical, before, recipe['affineToleranceMetres'])
                original, posed = (np.array(mesh['verts']) for mesh in (before, after))
                local = (original - affine[3]) @ np.linalg.inv(affine[:3])
                parts = labels[np.linalg.norm(local[:, None] - unique[None, :], axis=2).argmin(axis=1)]
                for part in plates:
                    with self.subTest(hand=entry['output'], plate=part):
                        a, b = original[parts == part], posed[parts == part]
                        before_lengths = np.linalg.norm(a[:, None] - a[None, :], axis=2)
                        after_lengths = np.linalg.norm(b[:, None] - b[None, :], axis=2)
                        np.testing.assert_allclose(after_lengths, before_lengths, atol=2e-5)
                checked += 1
        self.assertGreater(checked, 0)


class OwnSourceOrganicGripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = []
        for recipe in json.loads(grip.MANIFEST.read_text())['recipes']:
            if recipe.get('kind') != 'ownSourceOrganic':
                continue
            for entry in recipe['models']:
                original = grip.source(entry['sourceBlob'])
                selected = (grip.ROOT / entry['output']).read_bytes()
                cls.cases.append((recipe, entry, original, selected))

    def test_recipes_reproduce_the_client_reviewed_assets_from_pinned_sources(self):
        self.assertEqual(len(self.cases), 240)
        for recipe, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                self.assertEqual((grip.ROOT / entry['source']).read_bytes(), original)
                self.assertEqual(grip.build(recipe, entry)[0], selected)

    def test_cuffs_topology_uvs_materials_and_bind_transforms_stay_authored(self):
        for recipe, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                before_model, before = grip.organic_geometry(original)
                after_model, after = grip.organic_geometry(selected)
                self.assertEqual(len(before), len(after))
                self.assertEqual([(n[1], n[2], n[3]) for n in before_model.nodes],
                                 [(n[1], n[2], n[3]) for n in after_model.nodes])
                affine = np.array(entry['sourceToCanonicalAffine'])
                protected_count, moved_count = 0, 0
                for (source, _, vertices, root, rendered), (now, _, _, _, _) in zip(before, after):
                    for key in source.keys() - {'verts', 'normals', '_mesh_offset'}:
                        self.assertEqual(source[key], now[key], key)
                    local = (root - affine[3]) @ np.linalg.inv(affine[:3])
                    fixed = local[:, 2] >= recipe['bend']['cuffStartsZ'] if rendered else np.ones(len(vertices), bool)
                    for key in ('verts', 'normals'):
                        np.testing.assert_array_equal(np.array(source[key])[fixed], np.array(now[key])[fixed])
                    protected_count += int(fixed.sum())
                    moved_count += int((np.linalg.norm(np.array(now['verts']) - vertices, axis=1) > 1e-7).sum())
                self.assertGreaterEqual(protected_count, 4)
                self.assertGreater(moved_count, 0)

    def test_surface_health_and_unique_nodes_survive_native_serialization(self):
        for _, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                model, after = grip.organic_geometry(selected)
                _, before = grip.organic_geometry(original)
                names = [n[0] for n in model.nodes]
                self.assertEqual(len(names), len(set(names)))
                for (source, _, vertices, _, _), (now, _, _, _, _) in zip(before, after):
                    posed = np.array(now['verts'])
                    self.assertTrue(np.isfinite(posed).all())
                    faces = np.array(source['faces'])[:, :3]
                    old_area = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
                    new_area = np.linalg.norm(np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]]), axis=1)
                    self.assertGreater(float((new_area[old_area > 1e-12] / old_area[old_area > 1e-12]).min()), .03)


class OwnSourceWristGripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [(recipe, entry, grip.source(entry['sourceBlob']), (grip.ROOT / entry['output']).read_bytes())
                     for recipe in json.loads(grip.MANIFEST.read_text())['recipes']
                     if recipe.get('kind') == 'ownSourceWrist' for entry in recipe['models']]

    def test_wrist_recipes_reproduce_all_pinned_native_payloads(self):
        self.assertEqual(3, len(self.cases))
        for recipe, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                self.assertEqual((grip.ROOT / entry['source']).read_bytes(), original)
                rebuilt, proof = grip.build(recipe, entry)
                self.assertEqual(selected, rebuilt)
                self.assertGreaterEqual(proof['protectedCuffVertices'], 4)
                self.assertGreater(proof['movedVertices'], 0)

    def test_authored_cuffs_uvs_materials_topology_and_native_bindings_stay_exact(self):
        for _, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                before_model, before = grip.organic_geometry(original)
                after_model, after = grip.organic_geometry(selected)
                self.assertEqual([n[1:4] for n in before_model.nodes], [n[1:4] for n in after_model.nodes])
                self.assertEqual(len(before), len(after))
                affine = np.array(entry['sourceToCanonicalAffine'])
                protected_count = 0
                for (source, _, vertices, root, rendered), (now, _, _, _, _) in zip(before, after):
                    for key in source.keys() - {'verts', 'normals', '_mesh_offset'}:
                        self.assertEqual(source[key], now[key], key)
                    local = (root - affine[3]) @ np.linalg.inv(affine[:3])
                    cuff = local[:, 2] >= entry['wristCutCanonicalZ'] if rendered else np.ones(len(vertices), bool)
                    for key in ('verts', 'normals'):
                        np.testing.assert_array_equal(np.array(source[key])[cuff], np.array(now[key])[cuff])
                    protected_count += int(cuff.sum())
                self.assertGreaterEqual(protected_count, 4)

    def test_serialized_wrist_surfaces_remain_finite_and_keep_unique_node_names(self):
        for _, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                model, after = grip.organic_geometry(selected)
                _, before = grip.organic_geometry(original)
                names = [n[0] for n in model.nodes]
                self.assertEqual(len(names), len(set(names)))
                for (source, _, vertices, _, _), (now, _, _, _, _) in zip(before, after):
                    posed = np.array(now['verts'])
                    self.assertTrue(np.isfinite(posed).all())
                    faces = np.array(source['faces'])[:, :3]
                    old_area = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
                    new_area = np.linalg.norm(np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]]), axis=1)
                    self.assertGreater(float((new_area[old_area > 1e-12] / old_area[old_area > 1e-12]).min()), .03)


class OwnSourceDroidGripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = []
        for recipe in json.loads(grip.MANIFEST.read_text())['recipes']:
            if recipe.get('kind') != 'ownSourceDroid':
                continue
            for entry in recipe['models']:
                cls.cases.append((recipe, entry, grip.source(entry['sourceBlob']),
                                  (grip.ROOT / entry['output']).read_bytes()))

    def test_native_payloads_reproduce_from_pinned_own_source_geometry(self):
        self.assertEqual(164, len(self.cases))
        for recipe, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                self.assertEqual(original, (grip.ROOT / entry['source']).read_bytes())
                self.assertEqual(selected, grip.build(recipe, entry)[0])

    def test_authored_cuffs_buffers_and_rigid_mechanical_parts_survive(self):
        for recipe, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                old_model, before = grip.organic_geometry(original)
                model, after = grip.organic_geometry(selected)
                self.assertEqual([(n[1], n[2], n[3]) for n in old_model.nodes],
                                 [(n[1], n[2], n[3]) for n in model.nodes])
                self.assertEqual(len(before), len(after))
                basis = np.array(entry['jointReferenceToOwn'])
                protected_count = 0
                for (source, _, vertices, root, rendered), (now, _, posed, _, _) in zip(before, after):
                    for key in source.keys() - {'verts', 'normals', '_mesh_offset'}:
                        self.assertEqual(source[key], now[key], key)
                    if not rendered:
                        np.testing.assert_array_equal(vertices, posed)
                        continue
                    local = (root - basis[3]) @ basis[:3].T
                    _, protected, rigid, _ = grip.droid_pose(local, source, recipe)
                    for key in ('verts', 'normals'):
                        np.testing.assert_array_equal(np.array(source[key])[protected], np.array(now[key])[protected])
                    protected_count += int(protected.sum())
                    labels = grip.component_labels(source)[2]
                    for part in rigid:
                        a, b = vertices[labels == part], posed[labels == part]
                        np.testing.assert_allclose(np.linalg.norm(a[:, None] - a[None, :], axis=2),
                                                   np.linalg.norm(b[:, None] - b[None, :], axis=2), atol=2e-7)
                    faces = np.array(source['faces'])[:, :3]
                    a = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
                    b = np.linalg.norm(np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]]), axis=1)
                    self.assertGreater(float((b[a > 1e-12] / a[a > 1e-12]).min()), .03)
                    self.assertTrue(np.isfinite(posed).all())
                self.assertEqual(entry['protectedCuffVertices'], protected_count)


class ImportedMechanicalGripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = []
        for recipe in json.loads(grip.MANIFEST.read_text())['recipes']:
            if recipe.get('kind') not in ('ownSourceDroidCorrection', 'ownSourceJointPose', 'ownSourceRigid'):
                continue
            for entry in recipe['models']:
                cls.cases.append((recipe, entry, grip.source(entry['sourceBlob']),
                                  (grip.ROOT / entry['output']).read_bytes()))

    def test_every_selected_mechanical_payload_rebuilds_from_its_original_source(self):
        counts = {kind: sum(recipe['kind'] == kind for recipe, _, _, _ in self.cases)
                  for kind in ('ownSourceDroidCorrection', 'ownSourceJointPose', 'ownSourceRigid')}
        self.assertEqual({'ownSourceDroidCorrection': 14, 'ownSourceJointPose': 18, 'ownSourceRigid': 10}, counts)
        for recipe, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                self.assertEqual(original, (grip.ROOT / entry['source']).read_bytes())
                self.assertEqual(selected, grip.build(recipe, entry)[0])

    def test_cuff_rims_topology_materials_uvs_and_native_joint_controllers_stay_authored(self):
        for recipe, entry, original, selected in self.cases:
            with self.subTest(hand=entry['output']):
                before_model, before = grip.organic_geometry(original)
                after_model, after = grip.organic_geometry(selected)
                self.assertEqual([(n[1], n[2], n[3]) for n in before_model.nodes],
                                 [(n[1], n[2], n[3]) for n in after_model.nodes])
                self.assertEqual(len(before), len(after))
                highest = max(root[:, 2].max() for _, _, _, root, rendered in before if rendered)
                cuff_count = moved_count = 0
                for (source, _, vertices, root, rendered), (now, _, posed, _, _) in zip(before, after):
                    for key in source.keys() - {'verts', 'normals', '_mesh_offset'}:
                        self.assertEqual(source[key], now[key], key)
                    cuff = root[:, 2] >= highest - .005 if rendered else np.ones(len(vertices), bool)
                    for key in ('verts', 'normals'):
                        np.testing.assert_array_equal(np.array(source[key])[cuff], np.array(now[key])[cuff])
                    cuff_count += int(cuff.sum())
                    moved_count += int((np.linalg.norm(posed - vertices, axis=1) > 1e-7).sum())
                    self.assertTrue(np.isfinite(posed).all())
                    faces = np.array(source['faces'])[:, :3]
                    old_area = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
                    new_area = np.linalg.norm(np.cross(posed[faces[:, 1]] - posed[faces[:, 0]], posed[faces[:, 2]] - posed[faces[:, 0]]), axis=1)
                    self.assertGreater(float((new_area[old_area > 1e-12] / old_area[old_area > 1e-12]).min()), .03)
                self.assertGreater(cuff_count, 0)
                self.assertGreater(moved_count, 0)
                if entry.get('nodeNames'):
                    names = [node[0] for node in after_model.nodes]
                    self.assertEqual(len(names), len(set(names)))
                    for index, (old, new) in enumerate(zip(before_model.nodes, after_model.nodes)):
                        if old[1] is not None and str(index) not in entry['nodeNames']:
                            self.assertEqual(old[0], new[0])

    def test_each_generated_hand_has_exactly_one_owner(self):
        outputs = [entry['output'] for recipe in json.loads(grip.MANIFEST.read_text())['recipes']
                   for entry in recipe['models']]
        self.assertEqual(1663, len(outputs))
        self.assertEqual(len(outputs), len(set(outputs)))
        import subprocess
        tracked = set(subprocess.check_output(['git', '-C', str(grip.ROOT), 'ls-files'], text=True).splitlines())
        for recipe in json.loads(grip.MANIFEST.read_text())['recipes']:
            for entry in recipe['models']:
                self.assertTrue(entry['source'] in tracked, 'Pinned path must preserve Git filename case: ' + entry['source'])
                self.assertTrue(entry['output'] in tracked, 'Generated hand is not tracked: ' + entry['output'])
                if entry.get('skeleton'):
                    self.assertTrue(entry['skeleton'] in tracked, 'Pinned skeleton is not tracked: ' + entry['skeleton'])


class AuthoredHandGripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [(q, e, grip.source(e['sourceBlob']), (grip.ROOT / e['output']).read_bytes())
                     for q in json.loads(grip.MANIFEST.read_text())['recipes']
                     if q.get('kind') == 'ownSourceAuthored' for e in q['models']]

    def test_own_source_sculpts_reproduce_reviewed_hands_and_preserve_authored_buffers(self):
        self.assertEqual(1067, len(self.cases))
        for recipe, entry, original, output in self.cases:
            with self.subTest(hand=entry['output']):
                self.assertEqual(original, (grip.ROOT / entry['source']).read_bytes())
                self.assertEqual(output, grip.build(recipe, entry)[0])
                before, after = grip.organic_geometry(original)[1], grip.organic_geometry(output)[1]
                for index, ((mesh, _, vertices, _, _), (now, _, _, _, _)) in enumerate(zip(before, after)):
                    for key in mesh.keys() - {'verts', 'normals', '_mesh_offset'}:
                        self.assertEqual(mesh[key], now[key], key)
                    part = next((p for p in entry['parts'] if p['meshIndex'] == index), None)
                    if part is None:
                        self.assertEqual(mesh, now)
                        continue
                    for key in ('verts', 'normals'):
                        np.testing.assert_array_equal(np.array(mesh[key])[part['protectedVertices']],
                                                      np.array(now[key])[part['protectedVertices']])


class SkinHandGripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [(q, e, grip.source(e['sourceBlob']), (grip.ROOT / e['output']).read_bytes())
                     for q in json.loads(grip.MANIFEST.read_text())['recipes']
                     if q.get('kind') == 'ownSourceSkin' for e in q['models']]

    def test_native_skin_payloads_reproduce_from_pinned_own_sources(self):
        self.assertEqual(32, len(self.cases))
        for recipe, entry, original, output in self.cases:
            with self.subTest(hand=entry['output']):
                self.assertEqual(original, (grip.ROOT / entry['source']).read_bytes())
                self.assertEqual(output, grip.build(recipe, entry)[0])

    def test_cuff_weights_palette_bone_slots_inverse_binds_and_authored_buffers_survive(self):
        import struct
        from RobePoseAudit import Model
        for recipe, entry, original, output in self.cases:
            with self.subTest(hand=entry['output']):
                donor = grip.source(entry['skeletonBlob'])
                before = grip.geometry(original, entry['meshIndex'], donor)
                after = grip.geometry(output, entry['meshIndex'], donor)
                skin, _, _, weights, slots = grip.skin_buffers(original, before)
                _, _, _, wanted, new_slots = grip.skin_buffers(output, after)
                self.assertEqual(original[skin:skin + 100], output[skin:skin + 100])
                uint = lambda at: struct.unpack_from('<I', original, at)[0]
                for pointer, count, size in [(20, 24, 2), (28, 32, 16), (40, 44, 12)]:
                    start = 12 + uint(skin + pointer)
                    end = start + uint(skin + count) * size
                    self.assertEqual(original[start:end], output[start:end])
                np.testing.assert_array_equal(slots, new_slots)
                for key in before.keys() - {'verts', 'normals', 'weights', '_mesh_offset'}:
                    self.assertEqual(before[key], after[key], key)
                protected = entry['protectedVertices']
                self.assertGreater(len(protected), 0)
                np.testing.assert_array_equal(weights[protected], wanted[protected])
                for key in ('verts', 'normals'):
                    np.testing.assert_array_equal(np.array(before[key])[protected], np.array(after[key])[protected])
                self.assertEqual([n[1:4] for n in Model(original, False).nodes],
                                 [n[1:4] for n in Model(output, False).nodes])
                self.assertTrue(np.isfinite(np.array(after['verts'])).all())
                self.assertTrue(np.isfinite(np.array(after['normals'])).all())
                self.assertTrue(np.isfinite(wanted).all())
                self.assertTrue((wanted >= 0).all())
                if '30700' in entry['output']:
                    self.assertEqual([], entry['positions'])
                    np.testing.assert_array_equal(before['verts'], after['verts'])

    def test_editing_a_protected_cuff_is_rejected(self):
        import copy
        recipe, entry, original, _ = self.cases[0]
        entry = copy.deepcopy(entry)
        donor = grip.source(entry['skeletonBlob'])
        mesh = grip.geometry(original, entry['meshIndex'], donor)
        vertex = entry['protectedVertices'][0]
        position = np.array(mesh['verts'][vertex]) + [0.01, 0, 0]
        entry['positions'].append([vertex, *position.tolist()])
        with self.assertRaisesRegex(ValueError, 'protected cuff'):
            grip.build(recipe, entry)


if __name__ == '__main__':
    unittest.main()
