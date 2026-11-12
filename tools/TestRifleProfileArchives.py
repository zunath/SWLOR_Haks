"""Pinned authored archives must reproduce the existing native owner payload."""
from pathlib import Path
import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
import GenerateRifleGripProfiles as owner
import RifleProfileArchives as archives
import RobeAnimationBanks as banks

class RifleProfileArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'tools/rifle_profile_motions/test.json.gz'
        self.path.parent.mkdir(parents=True)
        self.payload = {'body': 'pfe0', 'motions': [], 'abilities': []}
        self.record = {'body': 'pfe0', 'motions': [], 'abilities': []}

    def write_archive(self):
        self.path.write_bytes(gzip.compress(json.dumps(self.payload).encode('ascii'), mtime=0))
        return {'file': 'tools/rifle_profile_motions/test.json.gz', 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}

    def test_changed_compressed_source_is_rejected(self):
        archive = self.write_archive()
        self.path.write_bytes(self.path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'archive changed'):
            archives.archive_record(self.record, archive, self.root)

    def test_foreign_body_is_rejected(self):
        self.payload['body'] = 'pmh0'
        with self.assertRaisesRegex(ValueError, 'wrong body'):
            archives.archive_record(self.record, self.write_archive(), self.root)

    def test_archive_paths_are_owned_and_relative(self):
        for name in ['../test.json.gz', 'tools/rifle_profile_motions/../test.json.gz', 'C:/Temp/test.json.gz']:
            with self.assertRaisesRegex(ValueError, 'archive path'):
                archives.archive_record(self.record, {'file': name}, self.root)

    def test_decompression_is_bounded(self):
        archive = self.write_archive()
        with patch.object(archives, 'MAX_ARCHIVE_BYTES', 2):
            with self.assertRaisesRegex(ValueError, 'bounded batch size'):
                archives.archive_record(self.record, archive, self.root)

    def test_part_inventory_cannot_change_body_or_skip_a_bank(self):
        record = {'body': 'pfe0', 'output': 'sw_cr_creature/a_rfl_pfe0.mdl', 'outputSha256': '0' * 64,
                  'bankParts': [{'output': 'sw_cr_creature/a_rfl_pfe0_b02.mdl', 'sha256': '0' * 64}]}
        with self.assertRaisesRegex(ValueError, 'bank partition'):
            archives.part_records(record)

    def test_overflow_bank_paths_preserve_the_owned_body_and_partition_sequence(self):
        for body in ['pfz0', 'pmz0', 'pmo0']:
            head = 'sw_cr_creature1/a_rfl_' + body
            record = {'body': body, 'output': head + '.mdl', 'outputSha256': '0' * 64,
                      'bankParts': [{'output': head + '.mdl', 'sha256': '0' * 64},
                                    {'output': head + '_b01.mdl', 'sha256': '0' * 64}]}
            self.assertEqual(2, len(archives.part_records(record)))
            record['bankParts'][1]['output'] = 'sw_cr_creature/a_rfl_' + body + '_b01.mdl'
            with self.assertRaisesRegex(ValueError, 'bank partition'):
                archives.part_records(record)

    def test_archived_group_reproduces_existing_native_bank_byte_for_byte(self):
        self.verify_native_reproduction(False)

    def test_all_profiles_in_archive_reproduce_existing_native_bank_byte_for_byte(self):
        self.verify_native_reproduction(True)

    def verify_native_reproduction(self, all_profiles):
        manifest = json.loads(owner.MANIFEST.read_text())
        record = copy.deepcopy(next(row for row in manifest['models'] if row['body'] == 'pfe0'))
        # Reproduce the original inline programs from either a flat or partitioned
        # installed bank. New production archives stay outside this small fixture.
        installed_parts = [(owner.ROOT / part['output']).read_bytes() for part in archives.part_records(record)]
        installed = banks.CompiledBridge(banks.join(installed_parts))
        selected = set(owner.hold.CLIPS) | archives.profile_names(record)
        expected = bytes(installed.pack(installed.name, installed.parent,
            [component for name, component in zip(installed.clip_names, installed.clips) if name in selected]))
        record['outputSha256'] = hashlib.sha256(expected).hexdigest()
        record.pop('bankParts', None)
        first = next(motion['name'][:-1] for motion in record['motions'] if motion['sourceClip'] == 'xbowr')
        self.payload = {'body': 'pfe0',
                        'motions': [motion for motion in record['motions'] if all_profiles or motion['name'].startswith(first)],
                        'abilities': [motion for motion in record['abilities'] if all_profiles or motion['name'].startswith(first)]}
        record['motions'] = [motion for motion in record['motions'] if not all_profiles and not motion['name'].startswith(first)]
        record['abilities'] = [motion for motion in record['abilities'] if not all_profiles and not motion['name'].startswith(first)]
        record['profileArchives'] = [self.write_archive()]
        manifest['models'] = [record]
        read_archive = archives.archive_record
        with patch.object(archives, 'archive_record', side_effect=lambda row, archive, root: read_archive(row, archive, self.root)):
            generated = owner.build(manifest, self.root / 'build', Path(r'C:/Program Files (x86)/Steam/steamapps/common/Neverwinter Nights'))
        self.assertEqual(generated[record['output']], expected)

if __name__ == '__main__':
    unittest.main()
