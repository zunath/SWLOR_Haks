"""The companion follows source additions, removals and metadata changes."""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from GeneratePortraitViewer import main

HEADER = '2DA V2.0\n\nBaseResRef Sex Race\n'


def load(output):
    text = (output / 'catalogue.js').read_text()
    return json.loads(text.removeprefix('window.PORTRAITS = ').rstrip().removesuffix(';'))


def source_tree(root):
    portraits = root / 'sw_portrait'
    tables = root / 'sw_2da'
    portraits.mkdir()
    tables.mkdir()
    (tables / 'racialtypes.2da').write_text('2DA V2.0\n\nLabel\n6 Human\n')
    return portraits, tables / 'portraits.2da', root / 'portrait-viewer'


class PortraitSynchronizationTest(unittest.TestCase):
    def test_add_remove_and_recategorize(self):
        with tempfile.TemporaryDirectory() as temporary:
            portraits, table, output = source_tree(Path(temporary))
            root = Path(temporary)
            table.write_text(HEADER + '1 first_ 0 6\n')
            Image.new('RGB', (32, 64), 'red').save(portraits / 'po_first_m.dds')
            main(root, output)
            self.assertTrue((output / 'images/first_.webp').is_file())

            # A new commit removes the old portrait and adds a new, unlisted one.
            (portraits / 'po_first_m.dds').unlink()
            Image.new('RGB', (32, 64), 'blue').save(portraits / 'po_second_m.dds')
            table.write_text(HEADER)
            main(root, output)
            self.assertEqual({p.name for p in (output / 'images').iterdir()}, {'second_.webp'})
            self.assertEqual([(r['name'], r['race']) for r in load(output)], [('second_', 'Unspecified')])

            # Metadata is rebuilt too, including references to absent base-game art.
            table.write_text(HEADER + '2 second_ 1 6\n3 stock_ 0 6\n')
            main(root, output)
            rows = load(output)
            self.assertEqual(rows[0]['race'], 'Human')
            self.assertEqual(rows[0]['sex'], '1')
            self.assertIsNone(rows[1]['image'])

            (portraits / 'po_second_m.dds').unlink()
            table.write_text(HEADER)
            main(root, output)
            self.assertEqual(list((output / 'images').iterdir()), [])
            self.assertEqual(load(output), [])

    def test_reads_files_the_way_the_game_does(self):
        with tempfile.TemporaryDirectory() as temporary:
            portraits, table, output = source_tree(Path(temporary))
            table.write_text('2DA V2.0\n// a comment line\nBaseResRef Sex Race\n1 Mixed_ 0 6\n2 norace_ 1 ****\n')
            # DDS rows run bottom-up, so the last source rows are the top of the portrait.
            largest = Image.new('RGB', (64, 128), 'blue')
            largest.paste('red', (0, 120, 64, 128))
            largest.save(portraits / 'PO_MIXED_H.dds')
            Image.new('RGB', (8, 16), 'green').save(portraits / 'po_mixed_t.dds')
            main(Path(temporary), output)

            rows = load(output)
            self.assertEqual([(r['name'], r['race'], r['image']) for r in rows],
                             [('Mixed_', 'Human', 'images/mixed_.webp'), ('norace_', 'Unspecified', None)])
            with Image.open(output / 'images/mixed_.webp') as preview:
                self.assertEqual(preview.size, (64, 100))
                red, _, blue = preview.convert('RGB').getpixel((32, 2))
                self.assertGreater(red, 200)
                self.assertLess(blue, 60)

    def test_rejects_unknown_portrait_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            portraits, table, output = source_tree(Path(temporary))
            table.write_text(HEADER)
            Image.new('RGB', (8, 16)).save(portraits / 'po_stray_q.dds')
            with self.assertRaises(ValueError):
                main(Path(temporary), output)


if __name__ == '__main__':
    unittest.main()
