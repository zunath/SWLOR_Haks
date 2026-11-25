"""The companion follows source additions, removals and metadata changes."""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from GeneratePortraitViewer import main


class PortraitSynchronizationTest(unittest.TestCase):
    def test_add_remove_and_recategorize(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            portraits = root / 'sw_portrait'
            tables = root / 'sw_2da'
            output = root / 'portrait-viewer'
            portraits.mkdir()
            tables.mkdir()
            (tables / 'racialtypes.2da').write_text('2DA V2.0\n\nLabel\n6 Human\n')
            table = tables / 'portraits.2da'
            header = '2DA V2.0\n\nBaseResRef Sex Race\n'
            table.write_text(header + '1 first_ 0 6\n')
            Image.new('RGB', (32, 64), 'red').save(portraits / 'po_first_m.dds')
            main(root, output)
            self.assertTrue((output / 'images/first_.webp').is_file())

            # A new commit removes the old portrait and adds a new, unlisted one.
            (portraits / 'po_first_m.dds').unlink()
            Image.new('RGB', (32, 64), 'blue').save(portraits / 'po_second_m.dds')
            table.write_text(header)
            main(root, output)
            self.assertEqual({p.name for p in (output / 'images').iterdir()}, {'second_.webp'})
            rows = json.loads((output / 'catalogue.js').read_text()[len('window.PORTRAITS = '):-2])
            self.assertEqual([(r['name'], r['race']) for r in rows], [('second_', 'Unspecified')])

            # Metadata is rebuilt too, including references to absent base-game art.
            table.write_text(header + '2 second_ 1 6\n3 stock_ 0 6\n')
            main(root, output)
            rows = json.loads((output / 'catalogue.js').read_text()[len('window.PORTRAITS = '):-2])
            self.assertEqual(rows[0]['race'], 'Human')
            self.assertEqual(rows[0]['sex'], '1')
            self.assertIsNone(rows[1]['image'])

            (portraits / 'po_second_m.dds').unlink()
            table.write_text(header)
            main(root, output)
            self.assertEqual(list((output / 'images').iterdir()), [])
            self.assertEqual((output / 'catalogue.js').read_text(), 'window.PORTRAITS = [];\n')


if __name__ == '__main__':
    unittest.main()
