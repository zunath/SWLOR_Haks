"""Keep the client-tested chest154 skinning when applying skirt bindings."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ChestSkirtBindings as skirts


class AuthoredChestBindingTests(unittest.TestCase):
    def test_apply_preserves_and_restores_authored_chests(self):
        manifest = json.loads(skirts.MANIFEST.read_text())
        restored = {}
        originals = {}
        for gender in 'fm':
            name = f'p{gender}h0_chest154'
            relative = f'sw_pt_chest/{name}.mdl'
            self.assertNotIn(name, manifest['models'])
            restored[relative] = manifest['restored'][relative]
            originals[relative] = skirts.source(restored[relative])
            self.assertEqual((skirts.ROOT / relative).read_bytes(), originals[relative])

        # Exercise the actual CLI on just the two protected models, including
        # recovery from an accidental edit and a second, idempotent apply.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'bindings.json'
            config.write_text(json.dumps({'models': {}, 'restored': restored}))
            for relative, data in originals.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            sources = {restored[path]: data for path, data in originals.items()}
            with patch.object(skirts, 'ROOT', root), patch.object(skirts, 'MANIFEST', config), \
                    patch.object(skirts, 'source', side_effect=sources.__getitem__):
                with patch('sys.argv', ['ChestSkirtBindings.py', '--check']):
                    skirts.main()
                for relative in originals:
                    (root / relative).write_bytes(b'stale model')
                with patch('sys.argv', ['ChestSkirtBindings.py', '--check']):
                    with self.assertRaisesRegex(ValueError, 'Skirt bindings are stale'):
                        skirts.main()
                for _ in range(2):
                    with patch('sys.argv', ['ChestSkirtBindings.py', '--apply']):
                        skirts.main()
                    for relative, data in originals.items():
                        self.assertEqual((root / relative).read_bytes(), data)
                with patch('sys.argv', ['ChestSkirtBindings.py', '--check']):
                    skirts.main()


if __name__ == '__main__':
    unittest.main()
