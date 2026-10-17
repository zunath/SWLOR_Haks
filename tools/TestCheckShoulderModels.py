"""Packaging checks must follow current HAK priority and logical model inputs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import GenerateShoulderModels as shoulders
import GenerateTintMapAssets as tint
import ModelLodAssets as models


class CheckShoulderModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = tint.find_active_models()['pmh0_shol013'].read_bytes()

    def fixture(self, root):
        for directory in ('low', 'high', 'highest', 'tools', 'sw_2da'):
            (root / directory).mkdir()
        config = {'HakList': [{'Path': 'low'}, {'Path': 'high'}, {'Path': 'highest'}]}
        (root / 'hakbuilder.json').write_text(json.dumps(config))
        (root / 'low/pmh0_shol013.mdl').write_bytes(self.source)
        (root / 'low/pmh0_shol014.mdl').write_bytes(models.rename_identity(self.source, 'pmh0_shol014'))
        (root / 'high/pmh0_shol1000.mdl').write_bytes(models.rename_identity(self.source, 'pmh0_shol1000'))
        (root / 'sw_2da/shoulderrender.2da').write_text('2DA V2.0\n\n BODY SOURCE RENDER\n')
        inputs = {relative: shoulders.digest((root / relative).read_bytes())
                  for relative in ('hakbuilder.json', 'low/pmh0_shol013.mdl', 'low/pmh0_shol014.mdl')}
        implementation = {}
        for relative in shoulders.IMPLEMENTATION:
            (root / relative).write_bytes(f'# {relative}\r\n'.encode())
            implementation[relative] = shoulders.digest((root / relative).read_bytes())
        manifest = {'inputs': inputs, 'implementation': implementation,
                    'sources': ['pmh0_shol013', 'pmh0_shol014'],
                    'models': {'pmh0_shol1000': {'path': 'high/pmh0_shol1000.mdl',
                               'sha256': shoulders.digest((root / 'high/pmh0_shol1000.mdl').read_bytes())}},
                    'table_sha256': shoulders.digest((root / 'sw_2da/shoulderrender.2da').read_bytes())}
        (root / 'tools/ShoulderModels.json').write_text(json.dumps(manifest))

    def test_added_higher_priority_source_or_alias_fails_packaging(self):
        for relative, source in [('high/pmh0_shol014.mdl', 'low/pmh0_shol014.mdl'),
                                 ('highest/pmh0_shol1000.mdl', 'high/pmh0_shol1000.mdl')]:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                shoulders.check(root)
                (root / relative).write_bytes((root / source).read_bytes())
                with self.assertRaisesRegex(ValueError, 'Stale shoulder assets.*active model'):
                    shoulders.check(root)

    def test_priority_configuration_changes_fail_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / 'hakbuilder.json').write_text(json.dumps({'HakList': [{'Path': 'high'}, {'Path': 'low'}]}))
            with self.assertRaisesRegex(ValueError, 'Stale shoulder assets.*hakbuilder.json'):
                shoulders.check(root)

    def test_new_external_shoulder_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / 'low/pmh0_shol015.mdl').write_bytes(models.rename_identity(self.source, 'pmh0_shol015'))
            with self.assertRaisesRegex(ValueError, 'Stale shoulder assets.*active shoulder sources'):
                shoulders.check(root)

    def test_lod_inputs_and_outputs_are_checked_without_expanding_them(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(models, 'ROOT', Path(directory)):
            root = Path(directory)
            self.fixture(root)
            with patch.object(models, 'lod_supported', lambda path, root: True):
                models.apply(root)
            shoulders.check(root)
            self.assertFalse((root / 'low/pmh0_shol014.mdl').exists())
            self.assertFalse((root / 'high/pmh0_shol1000.mdl').exists())
            canonical = root / 'low/pmh0_shol013.mdl'
            changed = bytearray(canonical.read_bytes())
            changed[-1] ^= 1
            canonical.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'Canonical model changed'):
                shoulders.check(root)

    def test_discovery_and_mapping_code_changes_fail_packaging(self):
        for relative in shoulders.IMPLEMENTATION:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                (root / relative).write_text('# changed implementation\n')
                with self.assertRaisesRegex(ValueError, 'Stale shoulder assets'):
                    shoulders.check(root)


if __name__ == '__main__':
    unittest.main()
