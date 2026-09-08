import hashlib
import struct
import unittest
from unittest.mock import patch

import CompileModels as mdl


class AnimationBankSourcesTests(unittest.TestCase):
    def test_only_generated_banks_keep_an_editable_source(self):
        self.assertIsNone(mdl.animation_source(b"newmodel ordinary\n", bytes(244)))

    def test_both_contents_are_hashed_and_line_endings_are_portable(self):
        source = b"# SWLOR authored animations for hero\r\nnewmodel an_hero\r\n"
        compiled = bytes(244) + b"native model payload"
        result = mdl.animation_source(source, compiled)
        header, content = result.split(b"\n", 1)
        self.assertEqual(content, source.replace(b"\r\n", b"\n"))
        self.assertEqual(header.decode(), "# SWLOR compiled animation source v1 " +
                         hashlib.sha256(compiled).hexdigest() + " " + hashlib.sha256(content).hexdigest())
        self.assertEqual(result, mdl.animation_source(b"\xef\xbb\xbf" + content, compiled))
        self.assertNotEqual(result, mdl.animation_source(source, compiled + b"changed"))

    def test_round_trip_accepts_only_equivalent_time_zero_constants(self):
        def validate(before, after):
            source = f"node dummy sample\nparent NULL\n{before}\nendnode\n".encode()
            output = f"node dummy sample\nparent NULL\n{after}\nendnode\n"
            compiled = struct.pack('<III', 0, 232, 0) + bytes(232)
            with patch.object(mdl, 'binary_nodes', return_value=[('sample', [], None)]):
                mdl.validate_round_trip(source, compiled, output)
        validate('positionkey 1\n0 1 2 3\nscalekey 1\n0 2', 'position 1 2 3\nscale 2')
        for before, after in [('positionkey 1\n0 1 2 3', 'position 1 2 4'),
                              ('scalekey 1\n0 2', 'scale 1'),
                              ('scalekey 1\n0.5 2', 'scale 2'),
                              ('scalekey 2\n0 1\n1 2', 'scale 2')]:
            with self.subTest(before=before), self.assertRaises(ValueError):
                validate(before, after)


if __name__ == "__main__":
    unittest.main()
