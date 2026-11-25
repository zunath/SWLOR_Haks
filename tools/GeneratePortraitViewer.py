#!/usr/bin/env python3
"""Generate browser portraits and the catalogue; source HAK assets stay untouched."""
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


def main(root=ROOT, output=OUTPUT):
    races = {r['id']: r['Label'] for r in table(root / 'sw_2da/racialtypes.2da')}
    images = {}
    source_dir = root / 'sw_portrait'
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    for path in sorted(source_dir.glob('*.dds')):
        stem = path.stem.lower()
        if not stem.startswith('po_') or stem[-1] not in 'hlmst':
            raise ValueError(f'Unrecognized portrait filename: {path.name}')
        images.setdefault(stem[3:-1], {})[stem[-1]] = path
    (output / 'images').mkdir(parents=True, exist_ok=True)
    for base, sizes in images.items():
        source = next(sizes[size] for size in 'hlmst' if size in sizes)
        with Image.open(source) as image:
            # NWN DDS rows run bottom-up; the game uses the top 100/128 of the texture.
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert('RGB')
            image = image.crop((0, 0, image.width, round(image.height * 100 / 128)))
            image.save(output / 'images' / f'{base}.webp', quality=85)
    rows = []
    listed = set()
    for row in table(root / 'sw_2da/portraits.2da'):
        base = row['BaseResRef']
        if base == UNRECORDED:
            continue
        key = base.lower()
        listed.add(key)
        race = row.get('Race', UNRECORDED)
        rows.append(dict(name=base, id=row['id'], race=races.get(race, f'Race {race}') if race != UNRECORDED else 'Unspecified',
                         sex=row.get('Sex', UNRECORDED), image=f'images/{key}.webp' if key in images else None))
    for base in sorted(images.keys() - listed):
        rows.append(dict(name=base, id=None, race='Unspecified', sex=UNRECORDED, image=f'images/{base}.webp'))
    (output / 'catalogue.js').write_text('window.PORTRAITS = ' + json.dumps(rows, ensure_ascii=True) + ';\n')
    # This directory owns generated previews only; prune after a successful build.
    for preview in (output / 'images').glob('*.webp'):
        if preview.stem not in images:
            preview.unlink()
    print(f'{len(images)} portrait images; {len(rows)} catalogue entries; '
          f'{sum(row["image"] is None for row in rows)} entries without local artwork.')


if __name__ == '__main__':
    main()
