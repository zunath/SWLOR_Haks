"""Guards for owned rifle motion keys, protected hooks and original native clips."""
import copy
import json
import unittest
from unittest.mock import Mock, patch
import GenerateRifleGripProfiles as profiles


class RifleGripProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(profiles.MANIFEST.read_text())

    def test_owned_profiles_preserve_defaults_stock_and_unowned_tracks(self):
        self.assertEqual(3, len(self.manifest['models']))
        profile_count = 0
        for record in self.manifest['models']:
            original, clips = profiles.sources(record)
            baseline_nodes = [(n, p, i, c) for n, p, i, c, _ in original.nodes]
            for name, model, source_clip in clips:
                with self.subTest(body=record['body'], clip=name):
                    if name.endswith(('_b', '_a', '_a_in', '_a_out')):
                        continue
                    self.assertEqual(baseline_nodes, [(n, p, i, c) for n, p, i, c, _ in model.nodes])
                    if name in profiles.hold.CLIPS:
                        self.assertIs(original, model)
                        continue
                    profile_count += 1
                    for clip in profiles.hold.CLIPS:
                        before = original.clips[clip][1]
                        after = model.clips[clip][1]
                        self.assertEqual([(n, p, i) for n, p, i, _, _ in before], [(n, p, i) for n, p, i, _, _ in after])
                        for (joint, _, _, old, _), (_, _, _, new, _) in zip(before, after):
                            normalize_hook = clip == source_clip and clip != 'xbowr' and joint == 'rhand'
                            exclude_rotation = clip == source_clip and (joint in profiles.ALLOWED_JOINTS or normalize_hook)
                            self.assertEqual({k: v for k, v in old.items() if not exclude_rotation or k != 20},
                                             {k: v for k, v in new.items() if not exclude_rotation or k != 20})
                            if joint == 'lhand' or (joint == 'rhand' and not normalize_hook):
                                self.assertEqual(old, new)
                            if normalize_hook:
                                self.assertEqual(old[20][0], new[20][0])
                                expected = profiles.np.frombuffer(profiles.idle_hook_quaternion(original), dtype='<f4')
                                self.assertTrue(profiles.np.array_equal(new[20][1], profiles.np.tile(expected, (len(old[20][1]), 1))))
        self.assertEqual(18, profile_count)

    def test_equipped_idle_preserves_complete_body_clip_and_only_retimes_fitted_arms(self):
        count = 0
        for record in self.manifest['models']:
            original, clips = profiles.sources(record)
            base = profiles.base_idle(record)
            for name, fitted, source_clip in clips:
                if not name.endswith('_b'):
                    continue
                count += 1
                self.assertEqual('pause1', source_clip)
                self.assertEqual(profiles.hold.animation_header(base.data, 'pause1'),
                                 profiles.hold.animation_header(fitted.data, 'pause1'))
                idle = next(model for clip, model, _ in clips if clip == name[:-1] + 'i')
                arms = {n: c[20] for n, _, _, c, _ in idle.clips['xbowr'][1]
                        if n in profiles.EQUIPPED_IDLE_JOINTS}
                before, after = base.clips['pause1'][1], fitted.clips['pause1'][1]
                self.assertEqual(len(before), len(after))
                for (joint, parent, part, old, _), (new_joint, new_parent, new_part, new, _) in zip(before, after):
                    self.assertEqual(original.nodes[0][0] if parent is None else joint, new_joint)
                    self.assertEqual((parent, part), (new_parent, new_part))
                    self.assertEqual({k: v for k, v in old.items()
                                      if joint not in profiles.EQUIPPED_IDLE_JOINTS or k != 20},
                                     {k: v for k, v in new.items()
                                      if joint not in profiles.EQUIPPED_IDLE_JOINTS or k != 20})
                    if joint in profiles.EQUIPPED_IDLE_JOINTS:
                        times, values = arms[joint]
                        expected = profiles.np.asarray([profiles.np.float32(t * base.clips['pause1'][0] /
                                                                           idle.clips['xbowr'][0]) for t in times])
                        self.assertTrue(profiles.np.array_equal(expected, new[20][0]))
                        self.assertEqual(values, new[20][1])
        self.assertEqual(6, count)

    def test_equipped_idle_rejects_wrong_body_or_unpinned_base(self):
        record = copy.deepcopy(self.manifest['models'][0])
        record['baseIdle']['model'] = 'a_ba' if record['body'][1] == 'f' else 'a_fa'
        with self.assertRaisesRegex(ValueError, 'matching native body base'):
            profiles.base_idle(record)
        record = copy.deepcopy(self.manifest['models'][0])
        record['baseIdle']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'baseline changed'):
            profiles.base_idle(record)

    def test_fitted_abilities_preserve_body_motion_translations_and_generic_phases(self):
        count = 0
        for record in self.manifest['models']:
            original, clips = profiles.sources(record)
            source = profiles.ability_source(record)
            allowed = profiles.EQUIPPED_IDLE_JOINTS | {'rhand'}
            for name, fitted, source_clip in clips:
                if source_clip not in ('sw_aimedshot', 'sw_aimedshot_in', 'sw_aimedshot_out'):
                    continue
                count += 1
                self.assertEqual(profiles.hold.animation_header(source.data, source_clip),
                                 profiles.hold.animation_header(fitted.data, source_clip))
                before, after = source.clips[source_clip][1], fitted.clips[source_clip][1]
                self.assertEqual(len(before), len(after))
                for (joint, parent, part, old, _), (new_joint, new_parent, new_part, new, _) in zip(before, after):
                    self.assertEqual(original.nodes[0][0] if parent is None else joint, new_joint)
                    self.assertEqual((parent, part), (new_parent, new_part))
                    change_rotation = source_clip == 'sw_aimedshot' and joint in allowed
                    self.assertEqual({k: v for k, v in old.items() if not change_rotation or k != 20},
                                     {k: v for k, v in new.items() if not change_rotation or k != 20})
        self.assertEqual(18, count)

    def test_ability_ownership_rejects_missing_profiles_and_unowned_joints(self):
        record = copy.deepcopy(self.manifest['models'][0])
        record['abilities'].pop()
        with self.assertRaisesRegex(ValueError, 'one qualified Aimed Shot'):
            profiles.validate_abilities(record)
        record = copy.deepcopy(self.manifest['models'][0])
        record['abilities'][0]['rotations']['rootdummy'] = next(iter(record['abilities'][0]['rotations'].values()))
        with self.assertRaisesRegex(ValueError, 'only six arm rotations'):
            profiles.validate_abilities(record)
        record = copy.deepcopy(self.manifest['models'][0])
        next(iter(record['abilities'][0]['rotations'].values()))['values'][0] = [0, 0, 0, 2]
        with self.assertRaisesRegex(ValueError, 'unit quaternions'):
            profiles.validate_abilities(record)

    def test_only_the_pinned_idle_hook_basis_is_supported(self):
        for value in (None, 'ready', [0., 0., 0., 1.]):
            record = copy.deepcopy(self.manifest['models'][0])
            record['profileHookBasis'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'pinned idle hook basis'):
                profiles.validate_motions(record, profiles.baseline(record))

    def test_varying_idle_hook_cannot_be_normalized_implicitly(self):
        record = self.manifest['models'][0]
        original = copy.deepcopy(profiles.baseline(record))
        idle = next(n[3] for n in original.clips['xbowr'][1] if n[0] == 'rhand')
        idle[20] = ((0., 1.), [(0., 0., 0., 1.), (0., 0., 1., 0.)])
        with self.assertRaisesRegex(ValueError, 'constant and unit'):
            profiles.idle_hook_quaternion(original)

    def test_weapon_hooks_cannot_be_authored_as_profile_rotations(self):
        record = copy.deepcopy(self.manifest['models'][0])
        curve = next(iter(record['motions'][0]['rotations'].values()))
        record['motions'][0]['rotations']['rhand'] = curve
        with self.assertRaisesRegex(ValueError, 'only the support arm and right wrist'):
            profiles.validate_motions(record, profiles.baseline(record))

    def test_every_profile_requires_the_three_correct_native_roles(self):
        record = copy.deepcopy(self.manifest['models'][0])
        record['motions'].pop(0)
        with self.assertRaisesRegex(ValueError, 'idle, ready and shot'):
            profiles.validate_motions(record, profiles.baseline(record))
        record = copy.deepcopy(self.manifest['models'][0])
        record['motions'][0]['sourceClip'] = 'xbowshot'
        with self.assertRaisesRegex(ValueError, 'role differs'):
            profiles.validate_motions(record, profiles.baseline(record))

    def test_nonfinite_nonunit_and_unordered_keys_are_rejected(self):
        for invalid, message in [('nonfinite', 'finite'), ('nonunit', 'unit'), ('time', 'increase')]:
            with self.subTest(invalid=invalid):
                record = copy.deepcopy(self.manifest['models'][0])
                curve = next(iter(record['motions'][0]['rotations'].values()))
                if invalid == 'nonfinite':
                    curve['values'][0][0] = float('nan')
                elif invalid == 'nonunit':
                    curve['values'][0] = [0., 0., 0., 2.]
                else:
                    curve['times'][1] = curve['times'][0]
                with self.assertRaisesRegex(ValueError, message):
                    profiles.validate_motions(record, profiles.baseline(record))


class RifleProfileOwnershipTests(unittest.TestCase):
    def test_base_generation_cannot_overwrite_specialized_banks(self):
        hold = profiles.hold
        protected = hold.profile_owned_bodies()
        self.assertTrue(protected)
        library = Mock()
        with patch.object(hold, 'BODIES', tuple(protected)):
            self.assertEqual({}, hold.generate(library))
        self.assertEqual([], library.mock_calls)

    def test_base_finalization_cannot_overwrite_specialized_banks(self):
        hold = profiles.hold
        library = Mock()
        with patch.object(hold, 'BODIES', tuple(hold.profile_owned_bodies())):
            hold.finalize(library)
        self.assertEqual([], library.mock_calls)


if __name__ == '__main__':
    unittest.main()
