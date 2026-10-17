"""Phenotype-2 robe 236 variants inherit coat motion with matching native IDs."""
from functools import lru_cache
import struct
import unittest

import CompileModels as mdl
import RestoreRobeClothParents as repair
import RobePoseAudit as poses


@lru_cache(maxsize=None)
def parent_model(name):
    sources = list(repair.ROOT.glob('sw_*/' + name + '.mdl'))
    if len(sources) != 1:
        raise ValueError(f'Expected one animation parent {name}, found {sources}')
    return poses.Model(sources[0].read_bytes())


class RobeClothParentTests(unittest.TestCase):
    def test_repair_changes_only_parent_and_animation_ids_and_is_idempotent(self):
        for path in sorted((repair.ROOT / 'sw_pt_robe').glob('p??22_robe236.mdl')):
            reference = path.with_name(path.name[:3] + '0' + path.name[5:])
            before = bytearray(path.read_bytes())
            before[180:244] = b'missing_coat'.ljust(64, b'\0')
            model = poses.Model(before)
            for nodes in [model.nodes, *(nodes for _, nodes in model.clips.values())]:
                for _, _, part, _, offset in nodes:
                    struct.pack_into('<i', before, offset + 28, part + 1000 if part >= 0 else part)
            after = repair.restore_parent(bytes(before), reference.read_bytes(), reference.stem)
            allowed = set(range(180, 244))
            for nodes in [model.nodes, *(nodes for _, nodes in model.clips.values())]:
                for _, _, _, _, offset in nodes:
                    allowed.update(range(offset + 28, offset + 32))
            with self.subTest(model=path.stem):
                self.assertTrue(all(i in allowed for i, (a, b) in enumerate(zip(before, after)) if a != b))
                self.assertEqual(after, repair.restore_parent(after, reference.read_bytes(), reference.stem))
                self.assertEqual(mdl.binary_nodes(bytes(before)), mdl.binary_nodes(after))
                self.assertEqual(model.clips.keys(), poses.Model(after).clips.keys())

    def test_robe_236_hides_separately_equipped_shoulders(self):
        lines = (repair.ROOT / 'sw_2da/parts_robe.2da').read_text().splitlines()
        columns = lines[2].split()
        row = next(line.split()[1:] for line in lines[3:]
                   if line.split() and line.split()[0] == '236')
        fields = dict(zip(columns, row))
        self.assertEqual('1', fields['HIDESHOL'])
        self.assertEqual('1', fields['HIDESHOR'])

    def test_robe_body_ids_match_the_shared_body_that_owns_shoulders(self):
        # Rigid shoulders attach below these canonical body bones. The stock
        # large-body parent a_ba2 inherits a_ba; changing robe part IDs must not
        # introduce a second, incompatible set of body animation IDs.
        canonical = parent_model('a_ba')
        bones = {name: part for name, _, part, _, _ in canonical.nodes}
        for side in ('l', 'r'):
            shoulder = next(node for node in canonical.nodes if node[0] == side + 'shoulder_g')
            self.assertEqual(side + 'bicep_g', canonical.nodes[shoulder[1]][0])
        checked = 0
        for path in sorted((repair.ROOT / 'sw_pt_robe').glob('p??22_robe236.mdl')):
            robe = poses.Model(path.read_bytes(), False)
            ids = {name: part for name, _, part, _, _ in robe.nodes}
            with self.subTest(model=path.stem):
                for name in ('rootdummy', 'torso_g', 'lbicep_g', 'rbicep_g',
                             'lforearm_g', 'rforearm_g'):
                    self.assertEqual(bones[name], ids[name], name)
            checked += 1
        self.assertEqual(8, checked)

    def test_every_large_variant_resolves_coat_tracks_for_idle_walk_run_and_sitting(self):
        checked = 0
        for path in sorted((repair.ROOT / 'sw_pt_robe').glob('p??22_robe236.mdl')):
            model = poses.Model(path.read_bytes())
            expected = path.stem[:3] + '0_robe236'
            with self.subTest(model=path.stem):
                self.assertEqual(expected, model.parent)
                clips, pending, visited = {}, model.parent, set()
                while pending:
                    self.assertNotIn(pending, visited)
                    visited.add(pending)
                    parent = parent_model(pending)
                    for name, clip in parent.clips.items():
                        clips.setdefault(name, clip)
                    pending = parent.parent
                clips.update(model.clips)
                helpers = {name: part for name, _, part, _, _ in model.nodes
                           if name.startswith('coat_') and name != 'coat_root' and 'top' not in name}
                self.assertEqual(12, len(helpers))
                for name in ('pause1', 'pause2', 'walk', 'run', 'sit', 'sitdown'):
                    tracks = {node[0]: node for node in clips[name][1]}
                    for bone, part in helpers.items():
                        self.assertEqual(part, tracks[bone][2], f'{name}/{bone}: animation ID mismatch')
                        self.assertIn(20, tracks[bone][3], f'{name}/{bone}: coat rotation is missing')
                for name in ('walk', 'run'):
                    rotations = [node[3][20][1] for node in clips[name][1]
                                 if node[0] in helpers and 20 in node[3]]
                    self.assertTrue(any(not mdl.equivalent_quaternion(values[0], value)
                                        for values in rotations for value in values[1:]),
                                    f'{name}: coat helpers must actually move')
                checked += 1
        self.assertEqual(8, checked)


if __name__ == '__main__':
    unittest.main()


