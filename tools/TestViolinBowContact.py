"""Check actual native part-ID binding and bow-hair contact between authored keys."""
import math
import os
from pathlib import Path
import unittest

import numpy as np

import GenerateViolinBowContact as violin
import RobePoseAudit as poses


class ViolinBowContactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        game = Path(os.environ.get('NWN_GAME_DATA',
            'C:/Program Files (x86)/Steam/steamapps/common/Neverwinter Nights/data'))
        if not game.is_dir():
            raise unittest.SkipTest('NWN_GAME_DATA with stock KEY/BIF is required for the female dwarf carry')
        cls.library = violin.Library(game)
        cls.poses = poses.Library(cls.library.data)

    def test_contact_landmarks_are_on_the_installed_string_mesh(self):
        for sex, number in (('m', '042'), ('f', '041')):
            data = self.library.data('it_torch_' + number)
            world = poses.Model(data).pose()
            distances = []
            for name, _, mesh in violin.rifle.mdl.binary_nodes(data):
                if not mesh or not name.startswith('string'):
                    continue
                position, rotation, scale = world[name]
                vertices = [np.array(position) + np.array(poses.rotate(rotation, tuple(x * scale for x in v)))
                            for v in mesh['verts']]
                for face in mesh['faces']:
                    for i, j in zip(face[:3], face[1:3] + face[:1]):
                        start, edge = vertices[i], vertices[j] - vertices[i]
                        along = np.clip(np.dot(violin.CONTACT[sex] - start, edge) / np.dot(edge, edge), 0, 1)
                        distances.append(np.linalg.norm(violin.CONTACT[sex] - start - along * edge))
            self.assertLess(min(distances), .001, sex)

    def test_banks_preserve_every_other_native_controller(self):
        for body in violin.BODIES:
            with self.subTest(body=body):
                changes = violin.rotation_changes(self.library, body)
                self.assertTrue(set(violin.MAIN) <= set(changes) <= set(violin.CLIPS))
                self.assertTrue(all(set(nodes) == set(violin.ARM) for nodes in changes.values()))
                self.assertEqual('a_rfl_' + body, self.poses.model(body).parent)
                self.assertEqual(violin.bank_name(body), self.poses.model('a_rfl_' + body).parent)

    def test_hair_stays_on_strings_through_both_stroke_directions(self):
        # Test the installed clips on the wearer's actual part IDs, including the
        # native torch layer; a name-only draft fit missed the reported defect.
        for sex in 'mf':
            for family in 'hedoz':
                body = 'p' + sex + family + '0'
                model = self.poses.model(body)
                inherited = self.poses.clips(body)
                torch = inherited['torchl']
                for name in violin.MAIN:
                    with self.subTest(body=body, clip=name):
                        clip = inherited[name]
                        nodes = {node[2]: node for node in clip[1]}
                        nodes.update({node[2]: node for node in torch[1] if node[3]})
                        composed = (clip[0], list(nodes.values()))
                        fractions = []
                        for index in range(math.ceil(clip[0] * 120) + 1):
                            time = min(index / 120, clip[0])
                            world = model.pose(composed, time, model.scale)
                            def point(joint, value):
                                position, rotation, scale = world[joint]
                                return np.array(position) + np.array(poses.rotate(rotation, tuple(x * scale for x in value)))
                            contact = point('lhand', violin.CONTACT[sex])
                            frog, tip = point('rhand', violin.HAIR_START), point('rhand', violin.HAIR_END)
                            axis = violin.unit(tip - frog)
                            along = float((contact - frog) @ axis)
                            gap = np.linalg.norm(contact - frog - axis * along)
                            fraction = along / np.linalg.norm(tip - frog)
                            string = np.array(poses.rotate(world['lhand'][1], tuple(violin.STRING_AXIS[sex])))
                            self.assertLess(gap, .003, (body, name, time, 'hair/string gap'))
                            self.assertGreater(fraction, .15)
                            self.assertLess(fraction, .90)
                            self.assertLess(abs(float(axis @ string)), .03)
                            fractions.append(fraction)
                        self.assertGreater(max(fractions) - min(fractions), .30)

    def test_wrist_bend_stays_within_the_fitted_range(self):
        for sex in 'mf':
            for family in 'hedoz':
                body = 'p' + sex + family + '0'
                rest = next(node[3][20][1][0] for node in self.poses.model(body).nodes if node[0] == 'rhand_g')
                for name in violin.MAIN:
                    clip = self.poses.clips(body)[name]
                    track = next(node[3][20] for node in clip[1] if node[0] == 'rhand_g')
                    for index in range(math.ceil(clip[0] * 120) + 1):
                        rotation = poses.sample(track, min(index / 120, clip[0]), True)
                        angle = math.degrees(2 * math.acos(min(1, abs(float(np.dot(rest, rotation))))))
                        self.assertLess(angle, 60.5, (body, name, index / 120))


if __name__ == '__main__':
    unittest.main()
