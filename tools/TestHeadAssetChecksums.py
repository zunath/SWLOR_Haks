"""Head source checksums survive Git's text EOL conversion, not content edits."""
from pathlib import Path
import tempfile
import unittest

from HeadTintAssets import authored_digest


class HeadAssetChecksumTests(unittest.TestCase):
    def test_text_endings_are_canonical_but_directive_edits_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ('.txi', '.mtr'):
                path = Path(folder) / ('resource' + suffix)
                path.write_bytes(b'mipmap 0\n')
                expected = authored_digest(path)
                path.write_bytes(b'mipmap 0\r\n')
                self.assertEqual(authored_digest(path), expected)
                path.write_bytes(b'mipmap 1\r\n')
                self.assertNotEqual(authored_digest(path), expected)

    def test_binary_resources_are_never_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ('.dds', '.mdl'):
                path = Path(folder) / ('resource' + suffix)
                path.write_bytes(b'payload\r\n')
                expected = authored_digest(path)
                path.write_bytes(b'payload\n')
                self.assertNotEqual(authored_digest(path), expected)


if __name__ == '__main__':
    unittest.main()
