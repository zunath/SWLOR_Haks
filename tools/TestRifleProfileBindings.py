"""Native wearer bindings and arm ownership for rifle profile generation."""
import copy
import json
import unittest
import GenerateRifleGripProfiles as profiles


class RifleProfileBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(profiles.MANIFEST.read_text())

    def test_legacy_cloak_ids_are_rebound_only_in_the_fitted_clip(self):
        for body in ('pmd0', 'pmo0'):
            with self.subTest(body=body):
                original = profiles.Model((profiles.ROOT / 'sw_cr_creature' / ('a_rfl_' + body + '.mdl')).read_bytes())
                _, reference = profiles.node_part_paths(original.nodes)
                paths, _ = profiles.node_part_paths(original.clips['xbowrdy'][1])
                self.assertTrue(any(reference[path][0] != node[2]
                                    for path, node in zip(paths, original.clips['xbowrdy'][1])))
                fixed = profiles.bind_clip_parts(original, 'xbowrdy', original.nodes)
                for path, before, after in zip(paths, original.clips['xbowrdy'][1], fixed.clips['xbowrdy'][1]):
                    self.assertEqual(reference[path][0], after[2])
                    self.assertEqual(before[:2], after[:2])
                    self.assertEqual(before[3], after[3])
                for clip in ('xbowr', 'xbowshot'):
                    self.assertEqual(original.clips[clip], fixed.clips[clip])

    def test_generic_ability_projects_optional_nodes_without_changing_body_controllers(self):
        record = next(r for r in self.manifest['models'] if r['body'][1] == 'f')
        source = profiles.ability_source(record)
        wearer = profiles.Model((profiles.ROOT / 'sw_cr_creature/a_rfl_pfd0.mdl').read_bytes())
        before_paths, _ = profiles.node_part_paths(source.clips['sw_aimedshot'][1])
        _, reference = profiles.node_part_paths(wearer.nodes)
        absent = set(before_paths) - reference.keys()
        self.assertTrue(absent)
        fixed = profiles.bind_clip_parts(source, 'sw_aimedshot', wearer.nodes)
        after_paths, _ = profiles.node_part_paths(fixed.clips['sw_aimedshot'][1])
        self.assertEqual(set(before_paths) - absent, set(after_paths))
        before = dict(zip(before_paths, source.clips['sw_aimedshot'][1]))
        for path, node in zip(after_paths, fixed.clips['sw_aimedshot'][1]):
            self.assertEqual(reference[path][0], node[2])
            self.assertEqual(before[path][3], node[3])
        for name in source.clips.keys() - {'sw_aimedshot'}:
            self.assertEqual(source.clips[name], fixed.clips[name])
        self.assertEqual(fixed.data, profiles.bind_clip_parts(fixed, 'sw_aimedshot', wearer.nodes).data)

    def test_existing_optional_joint_cannot_be_pruned_for_a_wrong_hierarchy(self):
        record = next(r for r in self.manifest['models'] if r['body'][1] == 'f')
        source = profiles.ability_source(record)
        wearer = profiles.Model((profiles.ROOT / 'sw_cr_creature/a_rfl_pfd0.mdl').read_bytes())
        paths, _ = profiles.node_part_paths(source.clips['sw_aimedshot'][1])
        _, reference = profiles.node_part_paths(wearer.nodes)
        node = next(n for path, n in zip(paths, source.clips['sw_aimedshot'][1])
                    if path not in reference and len(path) > 1)
        wrong = wearer.nodes + [(node[0], 0, max(n[2] for n in wearer.nodes) + 1, {}, node[4])]
        with self.assertRaisesRegex(ValueError, 'hierarchy differs'):
            profiles.bind_clip_parts(source, 'sw_aimedshot', wrong)

    def test_missing_required_wearer_joint_is_rejected(self):
        record = next(r for r in self.manifest['models'] if r['body'][1] == 'f')
        source = profiles.ability_source(record)
        wearer = profiles.Model((profiles.ROOT / 'sw_cr_creature/a_rfl_pfd0.mdl').read_bytes())
        reference = [('missing_bicep' if n == 'lbicep_g' else n, p, i, c, at)
                     for n, p, i, c, at in wearer.nodes]
        with self.assertRaisesRegex(ValueError, 'required native body joint'):
            profiles.bind_clip_parts(source, 'sw_aimedshot', reference)

    def test_six_arm_rotations_are_allowed_but_body_and_attachment_tracks_are_rejected(self):
        record = copy.deepcopy(self.manifest['models'][0])
        original = profiles.baseline(record)
        for motion in record['motions']:
            for joint, _, _, controllers, _ in original.clips[motion['sourceClip']][1]:
                if joint in ('rbicep_g', 'rforearm_g'):
                    motion['rotations'][joint] = {'times': list(controllers[20][0]),
                                                  'values': [list(q) for q in controllers[20][1]]}
        profiles.validate_motions(record, original)
        for joint in ('rootdummy', 'torso_g', 'pelvis_g', 'rhand', 'lhand'):
            invalid = copy.deepcopy(record)
            invalid['motions'][0]['rotations'][joint] = next(iter(invalid['motions'][0]['rotations'].values()))
            with self.subTest(joint=joint), self.assertRaisesRegex(ValueError, 'only six arm joints'):
                profiles.validate_motions(invalid, original)


if __name__ == '__main__':
    unittest.main()
