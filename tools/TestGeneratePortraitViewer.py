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
    (tables / 'gender.2da').write_text('2DA V2.0\n\nNAME GENDER CONSTANT\n0 156 M GENDER_MALE\n1 157 F GENDER_FEMALE\n')
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
            self.assertEqual([(r['name'], r['race'], r['sex'], r['id'], r['art'], r['image']) for r in load(output)],
                             [('second_', 'Unspecified', 'Unspecified', None, 'hak', 'images/second_.webp')])

            # Metadata is rebuilt too, including references to absent base-game art.
            table.write_text(HEADER + '2 second_ 1 6\n3 stock_ 0 6\n')
            main(root, output)
            rows = load(output)
            self.assertEqual(rows[0]['race'], 'Human')
            self.assertEqual(rows[0]['sex'], 'Female (1)')
            self.assertIsNone(rows[1]['image'])

            (portraits / 'po_second_m.dds').unlink()
            table.write_text(HEADER)
            main(root, output)
            self.assertEqual(list((output / 'images').iterdir()), [])
            self.assertEqual(load(output), [])

    def test_reads_files_the_way_the_game_does(self):
        with tempfile.TemporaryDirectory() as temporary:
            portraits, table, output = source_tree(Path(temporary))
            table.write_text('2DA V2.0\n// a comment line\nBaseResRef Sex Race\n1 Mixed_ 0 6\n2 norace_ **** ****\n3 odd_ 7 6\n4 alien_ 0 9\n5 **** 0 6\n')
            # DDS rows run bottom-up, so the last source rows are the top of the portrait.
            largest = Image.new('RGB', (64, 128), 'blue')
            largest.paste('red', (0, 120, 64, 128))
            largest.save(portraits / 'PO_MIXED_H.dds')
            Image.new('RGB', (8, 16), 'green').save(portraits / 'po_mixed_t.dds')
            Image.new('RGB', (32, 64), 'green').save(portraits / 'po_mixed_m.dds')
            main(Path(temporary), output)

            rows = load(output)
            self.assertEqual([(r['name'], r['resref'], r['race'], r['sex'], r['image']) for r in rows],
                             [('Mixed_', 'po_Mixed_', 'Human', 'Male (0)', 'images/mixed_.webp'),
                              ('norace_', 'po_norace_', 'Unspecified', 'Unspecified', None),
                              ('odd_', 'po_odd_', 'Human', 'Value 7', None),
                              ('alien_', 'po_alien_', 'Race 9', 'Male (0)', None)])
            with Image.open(output / 'images/mixed_.webp') as preview:
                self.assertEqual(preview.size, (64, 100))
                red, _, blue = preview.convert('RGB').getpixel((32, 2))
                self.assertGreater(red, 200)
                self.assertLess(blue, 60)

            # A table with no Sex column still builds.
            table.write_text('2DA V2.0\n\nBaseResRef Race\n1 Mixed_ 6\n')
            main(Path(temporary), output)
            self.assertEqual(load(output)[0]['sex'], 'Unspecified')

    def test_rejects_unknown_portrait_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            portraits, table, output = source_tree(Path(temporary))
            table.write_text(HEADER)
            Image.new('RGB', (8, 16)).save(portraits / 'po_stray_q.dds')
            with self.assertRaises(ValueError):
                main(Path(temporary), output)


class BaseGamePortraitTest(unittest.TestCase):
    def test_stock_art_fills_rows_without_hak_art(self):
        with tempfile.TemporaryDirectory() as temporary:
            portraits, table, output = source_tree(Path(temporary))
            table.write_text(HEADER + '1 both_ 0 6\n2 Stock_ 0 6\n3 none_ 0 6\n')
            Image.new('RGB', (32, 64), 'red').save(portraits / 'po_both_m.dds')
            (output / 'stock').mkdir(parents=True)
            for base in ('both_', 'stock_', 'unused_'):
                Image.new('RGB', (8, 10)).save(output / 'stock' / f'{base}.webp')
            main(Path(temporary), output)

            self.assertEqual([(r['name'], r['art'], r['image']) for r in load(output)],
                             [('both_', 'hak', 'images/both_.webp'), ('Stock_', 'stock', 'stock/stock_.webp'),
                              ('none_', None, None)])
            # A normal build never prunes the committed stock folder.
            self.assertEqual({p.name for p in (output / 'stock').iterdir()},
                             {'both_.webp', 'stock_.webp', 'unused_.webp'})

    def test_refresh_reads_extracted_game_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            portraits, table, output = source_tree(root)
            table.write_text(HEADER + '1 Large_ 0 6\n2 small_ 0 6\n3 hak_ 0 6\n4 tiny_ 0 6\n5 wee_ 0 6\n6 gone_ 0 6\n')
            Image.new('RGB', (32, 64), 'red').save(portraits / 'po_hak_m.dds')
            Image.new('RGB', (64, 128), 'red').save(portraits / 'po_hak_l.dds')
            game = root / 'game' / 'portraits.bif'
            game.mkdir(parents=True)
            # TGA rows keep their stored order, so a red top band stays on top.
            large = Image.new('RGB', (256, 512), 'blue')
            large.paste('red', (0, 0, 256, 8))
            large.save(game / 'po_large_h.tga')
            Image.new('RGB', (128, 256), 'green').save(game / 'po_large_l.tga')
            Image.new('RGB', (64, 128), 'green').save(game / 'po_small_m.tga')
            Image.new('RGB', (128, 256), 'blue').save(game / 'po_small_l.tga')
            for size, width in (('l', 128), ('s', 32), ('t', 16)):
                Image.new('RGB', (width, width * 2)).save(game / f'po_tiny_{size}.tga')
            Image.new('RGB', (32, 64)).save(game / 'po_wee_s.tga')
            Image.new('RGB', (16, 32)).save(game / 'po_wee_t.tga')
            Image.new('RGB', (64, 128)).save(game / 'po_hak_m.tga')
            Image.new('RGB', (64, 128)).save(game / 'po_unlisted_m.tga')
            # The game ships a few odd names; the refresh skips them.
            Image.new('RGB', (64, 128)).save(game / 'po_odd_m .tga')
            (output / 'stock').mkdir(parents=True)
            Image.new('RGB', (8, 10)).save(output / 'stock' / 'stale_.webp')
            # gone_ is still wanted, but the game no longer ships it.
            Image.new('RGB', (8, 10)).save(output / 'stock' / 'gone_.webp')
            main(root, output, game_data=root / 'game')

            self.assertEqual({p.name for p in (output / 'stock').iterdir()}, {'large_.webp', 'small_.webp', 'tiny_.webp', 'wee_.webp'})
            with Image.open(output / 'stock/large_.webp') as image:
                self.assertEqual(image.size, (256, 400))
                red, _, blue = image.convert('RGB').getpixel((128, 2))
                self.assertGreater(red, 200)
                self.assertLess(blue, 60)
            with Image.open(output / 'stock/small_.webp') as image:
                # With no _h size, _m wins over _l.
                self.assertEqual(image.size, (64, 100))
            with Image.open(output / 'stock/tiny_.webp') as image:
                self.assertEqual(image.size, (128, 200))
            with Image.open(output / 'stock/wee_.webp') as image:
                self.assertEqual(image.size, (32, 50))
            self.assertEqual([r['art'] for r in load(output)], ['stock', 'stock', 'hak', 'stock', 'stock', None])
            with Image.open(output / 'images/hak_.webp') as image:
                # HAK art follows the same rule: with no _h size, _m wins over _l.
                self.assertEqual(image.size, (32, 50))


if __name__ == '__main__':
    unittest.main()
