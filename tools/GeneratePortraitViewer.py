#!/usr/bin/env python3
"""Generate browser portraits and the catalogue; source HAK assets stay untouched."""
import argparse
import json
import shlex
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'portrait-viewer'
UNRECORDED = '****'  # How a 2DA marks an empty cell.


def table(path):
    lines = [line.strip() for line in path.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('//')]
    header = shlex.split(lines[1])
    return [dict(zip(['id', *header], shlex.split(line))) for line in lines[2:]]


def preview(source):
    """Open a portrait texture as the top 100/128 the game shows."""
    with Image.open(source) as image:
        if source.suffix.lower() == '.dds':
            # NWN DDS rows run bottom-up; TGA files carry their own row order.
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        image = image.convert('RGB')
    return image.crop((0, 0, image.width, round(image.height * 100 / 128)))


# The game's size letters. A preview uses _h, else _m, and another size only when neither exists.
SIZES = 'hmlst'


def best(sizes):
    return next(sizes[size] for size in SIZES if size in sizes)


def portrait_files(paths, strict=True):
    """Group po_<base>_<size> files by lowercase base; reject or skip other names."""
    found = {}
    for path in paths:
        stem = path.stem.lower()
        if not stem.startswith('po_') or stem[-1] not in SIZES:
            if not strict:
                continue
            raise ValueError(f'Unrecognized portrait filename: {path.name}')
        found.setdefault(stem[3:-1], {})[stem[-1]] = path
    return found


def refresh_stock(game_data, wanted, stock):
    """Rebuild the committed base-game previews for the bases in wanted."""
    found = portrait_files((path for path in Path(game_data).rglob('*')
                           if path.is_file() and path.name.lower().startswith('po_')
                           and path.suffix.lower() in ('.tga', '.dds')), strict=False)
    stock.mkdir(parents=True, exist_ok=True)
    kept = wanted & found.keys()
    for base in sorted(kept):
        with preview(best(found[base])) as image:
            image.save(stock / f'{base}.webp', quality=55, method=6)
    for old in stock.glob('*.webp'):
        if old.stem not in kept:
            old.unlink()


def main(root=ROOT, output=OUTPUT, game_data=None):
    races = {r['id']: r['Label'] for r in table(root / 'sw_2da/racialtypes.2da')}
    # gender.2da names each Sex value by its constant, such as GENDER_MALE.
    genders = {r['id']: r['CONSTANT'].removeprefix('GENDER_').capitalize() for r in table(root / 'sw_2da/gender.2da')}

    def sex(value):
        if value == UNRECORDED:
            return 'Unspecified'
        return f'{genders[value]} ({value})' if value in genders else f'Value {value}'
    source_dir = root / 'sw_portrait'
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    images = portrait_files(sorted(source_dir.glob('*.dds')))
    (output / 'images').mkdir(parents=True, exist_ok=True)
    for base, sizes in images.items():
        with preview(best(sizes)) as image:
            image.save(output / 'images' / f'{base}.webp', quality=85)
    entries = [row for row in table(root / 'sw_2da/portraits.2da') if row['BaseResRef'] != UNRECORDED]
    if game_data is not None:
        refresh_stock(game_data, {row['BaseResRef'].lower() for row in entries} - images.keys(), output / 'stock')
    # The stock folder is committed; a normal build only reads it.
    stock = {path.stem for path in (output / 'stock').glob('*.webp')}
    rows = []
    listed = set()
    for row in entries:
        base = row['BaseResRef']
        key = base.lower()
        listed.add(key)
        race = row.get('Race', UNRECORDED)
        # A HAK portrait overrides the base game's portrait of the same name.
        art = 'hak' if key in images else 'stock' if key in stock else None
        image = {'hak': f'images/{key}.webp', 'stock': f'stock/{key}.webp', None: None}[art]
        # Scripts name a portrait by po_ and its BaseResRef, such as po_el_f_09_.
        rows.append(dict(name=base, resref=f'po_{base}', id=row['id'], race=races.get(race, f'Race {race}') if race != UNRECORDED else 'Unspecified',
                         sex=sex(row.get('Sex', UNRECORDED)), art=art, image=image))
    for base in sorted(images.keys() - listed):
        rows.append(dict(name=base, resref=f'po_{base}', id=None, race='Unspecified', sex='Unspecified', art='hak', image=f'images/{base}.webp'))
    (output / 'catalogue.js').write_text('window.PORTRAITS = ' + json.dumps(rows, ensure_ascii=True) + ';\n')
    # This directory owns generated previews only; prune after a successful build.
    for old in (output / 'images').glob('*.webp'):
        if old.stem not in images:
            old.unlink()
    print(f'{len(images)} HAK images; {sum(row["art"] == "stock" for row in rows)} base-game images; '
          f'{len(rows)} catalogue entries; {sum(row["art"] is None for row in rows)} entries without artwork.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', metavar='GAME_DATA',
                        help='rebuild portrait-viewer/stock from a folder of extracted base-game resources')
    main(game_data=parser.parse_args().stock)
