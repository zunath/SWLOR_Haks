"""Root corrections must preserve native motion and remain readable by NWN."""
from pathlib import Path
import struct
import tempfile
import unittest

import CompileModels as mdl
import RobePoseAudit as poses
from UpdateNativeRootTracks import add_root_tracks


class NativeRootTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.stage = Path(temporary.name)
        cls.compiler, _ = mdl.prepare_compiler(cls.stage)
        binary = cls.stage / 'binary'
        binary.mkdir()
        geometry = '''node dummy rootpatch
 parent NULL
endnode
node dummy rootdummy
 parent rootpatch
 position 0 0 1
endnode
node trimesh body
 parent rootdummy
 bitmap NULL
 verts 3
 0 0 0
 1 0 0
 0 1 0
 tverts 3
 0 0 0
 1 0 0
 0 1 0
 faces 1
 0 1 2 1 0 1 2 0
endnode
'''

        def compile_model(corrected):
            clips = ''
            for name in ('cast', 'idle', 'finish'):
                # Deliberately alter the candidate's rotations too: only its
                # requested root positions may be imported into the original.
                angle = .9 if corrected else .2
                positions = ('positionkey 3\n 0 0 0 1\n .5 0 0 .84\n 1 0 0 1\n'
                             if corrected else '')
                clips += f'''newanim {name} rootpatch
 length 1
 transtime .125
 animroot rootdummy
 event .5 cast
 node dummy rootpatch
  parent NULL
 endnode
 node dummy rootdummy
  parent rootpatch
  orientationkey 2
  0 0 0 1 0
  1 0 0 1 {angle}
  {positions}
 endnode
doneanim {name} rootpatch
'''
            source = (f'newmodel rootpatch\nsetsupermodel rootpatch NULL\n'
                      f'classification CHARACTER\nsetanimationscale 1\n'
                      f'beginmodelgeom rootpatch\n{geometry}endmodelgeom rootpatch\n'
                      f'{clips}donemodel rootpatch\n')
            path = cls.stage / 'rootpatch.mdl'
            path.write_text(source)
            mdl.run_compiler(cls.compiler, cls.stage,
                             ['-cne', str(path), str(binary) + '/'], 'compile.log')
            return (binary / path.name).read_bytes()

        cls.original = compile_model(False)
        cls.candidate = compile_model(True)

    @staticmethod
    def root(model, name):
        return next(node for node in model.clips[name][1] if node[0] == 'rootdummy')

    def test_only_selected_root_arrays_change_and_raw_geometry_is_preserved(self):
        patched = add_root_tracks(self.original, self.candidate, ['finish', 'cast'])
        original_model = poses.Model(self.original)
        candidate_model = poses.Model(self.candidate)
        patched_model = poses.Model(patched)
        old_end = 12 + struct.unpack_from('<I', self.original, 4)[0]
        new_end = 12 + struct.unpack_from('<I', patched, 4)[0]
        self.assertGreater(len(self.original) - old_end, 0, 'fixture must contain mesh raw data')
        self.assertEqual(self.original[old_end:], patched[new_end:])
        allowed_bytes = set(range(4, 8))  # Size of the extended model section.
        for name in ('cast', 'finish'):
            old_root = self.root(original_model, name)
            new_root = self.root(patched_model, name)
            self.assertEqual(self.root(candidate_model, name)[3][8], new_root[3][8])
            self.assertEqual(old_root[3][20], new_root[3][20], 'retain original rotations')
            self.assertNotEqual(self.root(candidate_model, name)[3][20], new_root[3][20])
            allowed_bytes.update(range(old_root[4] + 84, old_root[4] + 108))
        self.assertEqual(original_model.clips['idle'], patched_model.clips['idle'])
        unexpected = [i for i in range(old_end)
                      if i not in allowed_bytes and self.original[i] != patched[i]]
        self.assertEqual([], unexpected, 'events, timing, skeleton and all other native bytes must survive')
        self.assertEqual(patched, add_root_tracks(self.original, self.candidate, ['cast', 'finish', 'cast']))

    def test_native_decompiler_reads_appended_tracks(self):
        patched = add_root_tracks(self.original, self.candidate, ['cast'])
        path = self.stage / 'rootpatch.mdl'
        path.write_bytes(patched)
        output = self.stage / 'decompiled'
        output.mkdir(exist_ok=True)
        mdl.run_compiler(self.compiler, self.stage,
                         ['-de', str(path), str(output) + '/'], 'decompile.log')
        source = (output / path.name).read_text()
        self.assertIn('positionkey', source)
        mdl.validate_round_trip(source.encode(), patched, source)

    def test_invalid_selection_and_existing_tracks_fail_without_mutating_inputs(self):
        original, candidate = self.original, self.candidate
        for names in ([], ['missing']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                add_root_tracks(original, candidate, names)
        with self.assertRaisesRegex(ValueError, 'already has'):
            add_root_tracks(candidate, candidate, ['cast'])
        with self.assertRaisesRegex(ValueError, 'one root position'):
            add_root_tracks(original, original, ['cast'])
        with self.assertRaisesRegex(ValueError, 'exactly one animation root'):
            add_root_tracks(original, candidate, ['cast'], root='missing')
        renamed = bytearray(candidate)
        renamed[20] = ord('x')
        with self.assertRaisesRegex(ValueError, 'identity or parent'):
            add_root_tracks(original, renamed, ['cast'])
        self.assertEqual(original, self.original)
        self.assertEqual(candidate, self.candidate)

    def test_out_of_bounds_position_values_are_rejected(self):
        candidate = bytearray(self.candidate)
        root = self.root(poses.Model(candidate), 'cast')[4]
        start, count = struct.unpack_from('<II', candidate, root + 84)
        key = next(12 + start + i * 12 for i in range(count)
                   if struct.unpack_from('<I', candidate, 12 + start + i * 12)[0] == 8)
        struct.pack_into('<H', candidate, key + 8, 65535)
        with self.assertRaisesRegex(ValueError, 'three-column position keys'):
            add_root_tracks(self.original, candidate, ['cast'])


if __name__ == '__main__':
    unittest.main()
