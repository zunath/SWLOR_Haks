"""Verify female thigh 16 retains native geometry and its own palette channels."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import GenerateTintMapAssets as tint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stock = tint.read_stock_key_models(args.game_data)
    palettes = tint.read_stock_key_resources(args.game_data, 6)
    entries = {entry['model']: entry for entry in
               json.loads((root / 'tools/TintMapSources.json').read_text())}
    rows = [line.split() for line in (root / 'sw_2da/tintmap.2da').read_text().splitlines()[3:]]
    names = sorted(name for name in stock if re.fullmatch(r'pf[a-z](0|2)_leg[lr]016', name))
    assert {'pfh0_legl016', 'pfh0_legr016', 'pfh2_legl016', 'pfh2_legr016'} <= set(names)

    for name in names:
        side = name[-4]
        folder = 'sw_pt_lthigh' if side == 'l' else 'sw_pt_rthigh'
        material = f'pfh0_leg{side}016'
        path = root / folder / (name + '.mdl')
        original = tint.extract_stock_bif_resource(*stock[name])
        fields = tint.read_binary_model_material_fields(path, original)
        assert len(fields) == 1, name
        expected = bytearray(original)
        offset = fields[0][2]
        expected[offset:offset + 64] = material.encode().ljust(64, b'\0')
        assert path.read_bytes() == expected, (name, 'native geometry or other model fields changed')
        assert tint.read_model_material_bindings(path) == [(fields[0][1], material)], name
        matches = [row for row in rows if row[1] == name]
        assert len(matches) == 1 and matches[0][2:] == [material, '2,3,6,7'], (name, matches)

    masks = set()
    for side in 'lr':
        material = f'pfh0_leg{side}016'
        entry = entries[material]
        native = tint.extract_stock_bif_resource(*palettes[material], expected_type=6)
        assert entry['sourceSha256'] == hashlib.sha256(native).hexdigest(), material
        assert (entry['width'], entry['height'], entry['layers']) == (128, 128, [2, 3, 6, 7]), material
        # PLT stores shade/layer byte pairs; categorical leather and metal IDs must be exact.
        native_layers = tint.np.frombuffer(native, dtype=tint.np.uint8, offset=24).reshape(128, 128, 2)[:, :, 1]
        texture = root / entry['output']
        assert tint.np.array_equal(tint.decode_dds_layers(texture, 128, 128), native_layers), material
        shades = tint.decode_dds_shades(texture, 128, 128)
        assert hashlib.sha256(shades.tobytes()).hexdigest() == entry['shadeSha256'], material
        declaration = (root / 'sw_tint_mtr' / (material + '.mtr')).read_text()
        assert f'texture7 {entry["texture"]}' in declaration, material
        assert 'parameter float tintMapWidth 128.0' in declaration, material
        assert texture.with_suffix('.txi').read_text() == 'mipmap 0\n', material
        masks.add(entry['output'])
    assert len(masks) == 1, 'Identical left/right native palettes should share their mask'
    print(f'PASS: {len(names)} female thigh models preserve native geometry; both palettes and every catalog row match.')


if __name__ == '__main__':
    main()
