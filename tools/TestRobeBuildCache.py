import hashlib
import unittest

from RobeBuildCache import build_key, make_record, reusable


def digest(value):
    return hashlib.sha256(value).hexdigest()


class RobeBuildCacheTests(unittest.TestCase):
    def setUp(self):
        self.inputs = [b"newmodel robe\n", digest(b"compiler"), digest(b"parent"), digest(b"validator")]
        self.key = build_key(*self.inputs)
        self.binary = b"\x00\x00\x00\x00compiled robe"
        self.record = make_record(self.key, self.binary)
        self.ownership = digest(self.binary)

    def test_exact_owned_validated_output_can_be_reused(self):
        self.assertTrue(reusable(self.record, build_key(*self.inputs), self.binary, self.ownership))

    def test_every_input_change_invalidates_the_record(self):
        for index in range(4):
            with self.subTest(input=index):
                changed = self.inputs.copy()
                changed[index] = b"newmodel robe\r\n" if index == 0 else digest(b"changed")
                self.assertFalse(reusable(self.record, build_key(*changed), self.binary, self.ownership))

    def test_digest_case_is_not_a_content_change(self):
        key = build_key(self.inputs[0], *(value.upper() for value in self.inputs[1:]))
        self.assertEqual(self.key, key)
        self.assertTrue(reusable(self.record, key.upper(), self.binary, self.ownership.upper()))

    def test_tampered_output_fails_even_if_one_proof_is_updated(self):
        changed = self.binary + b"tamper"
        self.assertFalse(reusable(self.record, self.key, changed, self.ownership))
        self.assertFalse(reusable(make_record(self.key, changed), self.key, changed, self.ownership))
        self.assertFalse(reusable(self.record, self.key, changed, digest(changed)))

    def test_missing_or_malformed_proof_fails_closed(self):
        for record in (None, [], {}, {"version": True}, {**self.record, "version": 2},
                       {**self.record, "key": None}, {**self.record, "binarySha256": "invalid"}):
            with self.subTest(record=record):
                self.assertFalse(reusable(record, self.key, self.binary, self.ownership))
        for ownership in (None, "", "0" * 64):
            self.assertFalse(reusable(self.record, self.key, self.binary, ownership))
        self.assertFalse(reusable(self.record, self.key, b"", self.ownership))

    def test_build_proofs_require_complete_valid_inputs(self):
        for index in range(4):
            changed = self.inputs.copy()
            changed[index] = b"" if index == 0 else "not-a-digest"
            with self.subTest(input=index), self.assertRaises(ValueError):
                build_key(*changed)
        with self.assertRaises(ValueError):
            make_record(self.key, b"")


if __name__ == "__main__":
    unittest.main()
