#!/usr/bin/env python3
"""Generate shared head shader profiles and validate their authored resources."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import functools
import struct

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_suffix('.json')
PROFILES = {
    'fs_head_legacy': (0, 0, 0, 0, 1),
    'fs_head_plain': (0, 0, 0, 0, 0),
    'fs_head_mapped': (1, 1, 0, 0, 0),
    'fs_head_hair': (1, 1, 0, 0, 1),
    'fs_head_full': (1, 1, 1, 1, 1),
}


def shader_source(profile: str) -> str:
    normal, specular, roughness, emission, environment = PROFILES[profile]
    return f'''// Shared head lighting profile; material parameters select authored effects.
#define SHADER_TYPE 2
#define LIGHTING 1
#define FOG 1
#define KEYHOLING 1
#define NORMAL_MAP {normal}
#define SPECULAR_MAP {specular}
#define ROUGHNESS_MAP {roughness}
#define HEIGHT_MAP 0
#define SELF_ILLUMINATION_MAP {emission}
#define ENVIRONMENT_MAP {environment}
#include "inc_standard"
#include "inc_hd_tint"
'''


def material_source(entry: dict) -> str:
    # Explicit defaults prevent a parameter from leaking from the previous MTR
    # when the client reuses a linked shader program.
    parameters = feature_defaults() | entry['parameters']
    lines = entry['declarations'] + [
        f'parameter float {name} {value:.12g}' for name, value in sorted(parameters.items())
    ]
    return '\n'.join(lines) + '\n'


def feature_defaults() -> dict[str, float]:
    source = (ROOT / 'sw_shader' / 'inc_hd_tint.shd').read_text()
    return {name: float(value) for name, value in
            re.findall(r'uniform float (\w+) = ([-\d.]+);', source)
            if not name.startswith('row')}






def dds_layout_errors(path: Path) -> list[str]:
    """Check complete authored mip chains as well as explicit base-only inputs."""
    data = path.read_bytes()
    if len(data) < 128 or data[:4] != b'DDS ' or data[84:88] not in (b'DXT1', b'DXT3', b'DXT5', b'ATI1', b'ATI2'):
        return [f'{path.name}: unsupported or truncated DDS']
    flags, height, width, linear_size, _, count = struct.unpack_from('<6I', data, 8)
    if min(width, height) < 1 or max(width, height) > 16384 or not flags & 0x20000:
        return [f'{path.name}: invalid DDS dimensions or ambiguous mip count']
    block_bytes = 8 if data[84:88] in (b'DXT1', b'ATI1') else 16
    full_count = max(width, height).bit_length()
    if count not in (1, full_count):
        return [f'{path.name}: incomplete authored mip chain']
    expected = sum(((max(1, width >> level)+3)//4) * ((max(1, height >> level)+3)//4) * block_bytes
                   for level in range(count))
    if len(data) != 128 + expected or linear_size != ((width+3)//4)*((height+3)//4)*block_bytes:
        return [f'{path.name}: DDS dimensions do not cover the complete block payload']
    txi = path.with_suffix('.txi')
    if count == 1 and (not txi.is_file() or 'mipmap 0' not in txi.read_text().lower()):
        return [f'{path.name}: base-only DDS requires mipmap 0']
    return []


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


@functools.lru_cache(maxsize=1)
def catalog(root: Path = ROOT) -> dict:
    """Allow the general tint generator to preserve the separately authored heads."""
    path = root / 'tools' / 'HeadTintAssets.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {
        'models': [], 'materials': {}, 'catalogRows': [], 'textures': {}, 'auxiliary': {},
        'compatibility': {'models': {}, 'rows': []}}


def owned_paths(root: Path = ROOT) -> set[Path]:
    manifest = catalog(root)
    return {root / relative for relative in set(manifest['textures']) | set(manifest.get('auxiliary', {}))}


def model_rows(root: Path = ROOT) -> list[tuple[str, str, list[int]]]:
    manifest = catalog(root)
    return [(model, material, [int(v) for v in layers.split(',')])
            for model, material, layers in manifest['catalogRows'] + manifest.get('compatibility', {}).get('rows', [])]




def validate() -> list[str]:
    manifest = load_manifest()
    errors = []
    defaults = feature_defaults()
    for profile in PROFILES:
        path = ROOT / 'sw_shader' / (profile + '.shd')
        if not path.is_file() or path.read_text() != shader_source(profile):
            errors.append(f'{path.name}: stale shared head shader profile')
    for name, entry in manifest['materials'].items():
        unknown = set(entry['parameters']) - set(defaults)
        if unknown:
            errors.append(f'{name}: unknown head parameters {sorted(unknown)}')
        if entry['shader'] not in PROFILES:
            errors.append(f'{name}: unknown head lighting profile')
        path = ROOT / 'sw_tint_mtr' / (name + '.mtr')
        if not path.is_file() or path.read_text() != material_source(entry):
            errors.append(f'{name}: stale head material profile')
    for relative, entry in manifest.get('textures', {}).items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f'{relative}: missing authored head texture')
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            errors.append(f'{relative}: authored head texture checksum differs')
        else:
            with Image.open(path) as image:
                if list(image.size) != entry['size']:
                    errors.append(f'{relative}: authored texture dimensions differ')
                if any(side < 1 or side & (side - 1) for side in image.size):
                    errors.append(f'{relative}: texture dimensions must be powers of two')
                if entry.get('codec') == 'BC3' and image.convert('RGBA').getchannel('A').getextrema()[0] == image.convert('RGBA').getchannel('A').getextrema()[1]:
                    errors.append(f'{relative}: DXT5 wastes alpha on an opaque or constant-alpha map')
            role = entry.get('role')
            allowed = {'normal': {'BC5'}, 'specular': {'BC1', 'BC4'},
                       'roughness': {'BC4'}, 'mask': {'BC4', 'BC5'},
                       'tint': {'BC5'}, 'categoryMask': {'BC5'},
                       'color': {'BC1', 'BC3'}, 'palette': {'TGA'}}
            if role not in allowed or entry.get('codec') not in allowed[role]:
                errors.append(f'{relative}: texture codec does not match its shader role')
            if role == 'normal' and max(entry['size']) > 512:
                errors.append(f'{relative}: head normal map exceeds 512 pixels')
            if path.stat().st_size > 1572864:
                errors.append(f'{relative}: exceeds the 1.5 MiB head texture budget')
            if path.suffix == '.tga' and not entry.get('exception'):
                errors.append(f'{relative}: lossless TGA requires a documented exception')
            if path.suffix == '.dds':
                errors.extend(dds_layout_errors(path))
                data = path.read_bytes()
                codecs = {'BC1': b'DXT1', 'BC3': b'DXT5', 'BC4': b'ATI1', 'BC5': b'ATI2'}
                if data[84:88] != codecs.get(entry.get('codec')):
                    errors.append(f'{relative}: DDS FourCC differs from its recorded codec')
                levels = struct.unpack_from('<I', data, 28)[0]
                expected_levels = 1 if role in ('tint', 'categoryMask') else max(entry['size']).bit_length()
                if levels != expected_levels or levels != entry.get('mipLevels'):
                    errors.append(f'{relative}: mip chain does not match its shader role')
    for relative, digest in manifest.get('auxiliary', {}).items():
        path = ROOT / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f'{relative}: authored head resource checksum differs')
    for relative, digest in manifest.get('staticMaterials', {}).items():
        path = ROOT / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f'{relative}: authored static material checksum differs')
    rows = []
    for line in (ROOT / 'sw_2da' / 'tintmap.2da').read_text().splitlines()[3:]:
        fields = line.split()
        if len(fields) == 4 and fields[1].lower() in manifest['models']:
            rows.append(fields[1:])
    if rows != manifest['catalogRows']:
        errors.append('Head model/material/channel catalog differs from tintmap.2da')
    compatibility = manifest.get('compatibility', {'models': {}, 'rows': []})
    current_compatibility = [line.split()[1:] for line in
        (ROOT / 'sw_2da/tintmap.2da').read_text().splitlines()[3:]
        if len(line.split()) == 4 and line.split()[1] in compatibility['models']]
    if current_compatibility != compatibility['rows']:
        errors.append('Existing phenotype-2 compatibility rows changed')
    for model in manifest['models']:
        model_path = ROOT / 'sw_pt_head' / (model + '.mdl')
        if not model_path.is_file():
            errors.append(f'{model}: missing catalog head model')
        elif hashlib.sha256(model_path.read_bytes()).hexdigest() != manifest['modelHashes'][model]:
            errors.append(f'{model}: compiled head geometry differs from the approved catalog')
    import GenerateTintMapAssets as tint
    from ModelLodAssets import ModelPath
    for model, limit in manifest.get('modelBudgets', {}).items():
        path = ROOT / 'sw_pt_head' / (model + '.mdl')
        data = path.read_bytes()
        fields = tint.read_binary_model_material_fields(path, data)
        triangles = sum(struct.unpack_from('<I', data, texture_offset - tint.MESH_TEXTURE0_OFFSET + 12)[0]
                        for texture_offset, _, _, _ in fields)
        if not re.fullmatch(r'p[fm][heodz]0_head\d{3}', model) or triangles > limit or limit > 10000:
            errors.append(f'{model}: {triangles} triangles exceeds the playable head budget of {limit}')
    for model, digest in compatibility['models'].items():
        path = ModelPath(ROOT / 'sw_pt_head' / (model + '.mdl'))
        if not re.fullmatch(r'p[fm][a-z]2_head\d{3}', model) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f'{model}: existing compatibility model differs')
    for model, material, _ in compatibility['rows']:
        path = ModelPath(ROOT / 'sw_pt_head' / (model + '.mdl'))
        if material not in manifest['materials'] or material not in {name for _, name in tint.read_model_material_bindings(path)}:
            errors.append(f'{model}: existing explicit compatibility binding differs for {material}')
    bindings = {model: {material or texture for texture, material in
                       tint.read_model_material_bindings(ROOT / 'sw_pt_head' / (model + '.mdl'))}
                for model in manifest['models'] if (ROOT / 'sw_pt_head' / (model + '.mdl')).is_file()}
    for model, material, _ in model_rows():
        if model in bindings and material not in bindings[model]:
            errors.append(f'{model}: model cannot address head material {material}')
    resources = {'null'}
    resources.update(p.stem.lower() for folder in ROOT.glob('sw_*') if folder.is_dir()
                     for p in folder.iterdir() if p.suffix.lower() in ('.dds', '.tga'))
    for name, entry in manifest['materials'].items():
        for texture in re.findall(r'^texture\d+\s+(\S+)', material_source(entry), re.M):
            if texture.lower() not in resources:
                errors.append(f'{name}: missing head texture {texture}')
    return errors


def generate() -> None:
    for profile in PROFILES:
        (ROOT / 'sw_shader' / (profile + '.shd')).write_text(shader_source(profile), encoding='ascii', newline='\n')
    manifest = load_manifest()
    for name, entry in manifest['materials'].items():
        (ROOT / 'sw_tint_mtr' / (name + '.mtr')).write_text(material_source(entry), encoding='ascii', newline='\n')
    for relative, text in manifest.get('auxiliaryText', {}).items():
        (ROOT / relative).write_text(text, encoding='ascii', newline='\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--apply', action='store_true')
    action.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.apply:
        generate()
    errors = validate()
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(1)
    manifest = load_manifest()
    print(f"Head assets verified: {len(PROFILES)} lighting profiles, {len(manifest['materials'])} material profiles.")


if __name__ == '__main__':
    main()
