"""Fail HAK packaging when source edits have left neck skins stale."""
import hashlib
import json
from pathlib import Path
from ModelLodAssets import ModelPath, content, model_paths

# Generator, skin remapping, and model parser code that decides every alias's bytes.
IMPLEMENTATION = ('tools/GenerateNeckModels.py', 'tools/NeckSkinBindings.py',
                  'tools/RobePoseAudit.py', 'tools/CompileModels.py', 'tools/CleanModels.py')


def source_digest(path):
    # Git may check out tool sources with CRLF line endings.
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def check(root):
    manifest = json.loads((root / 'tools/NeckModels.json').read_text())
    paths = {p.stem: p for directory in ('sw_pt_neck', 'sw_pt_root')
             for p in model_paths(root / directory)}
    owned = manifest.get('owned_inputs', {})
    failures = []
    for name, expected in manifest['inputs'].items():
        # Repository inputs must remain; stock inputs are audited only if overridden.
        path = ModelPath(root / owned[name]) if name in owned else paths.get(name)
        if path is None and name not in owned:
            continue
        if not path.is_file() or hashlib.sha256(content(path.read_bytes())).hexdigest() != expected:
            failures.append(name)
    for name, entry in manifest['aliases'].items():
        path = paths.get(name)
        if path is None or hashlib.sha256(content(path.read_bytes())).hexdigest() != entry['sha256']:
            failures.append(name)
    # Retired aliases render nothing; packaging them only adds size.
    failures.extend(name for name in manifest.get('retired', []) if name in paths)
    for name, expected in manifest['tables'].items():
        if hashlib.sha256((root / 'sw_2da' / name).read_text().encode()).hexdigest() != expected:
            failures.append(name)
    implementation = {**manifest.get('implementation', {}), **manifest.get('validation_implementation', {})}
    if set(implementation) != set(IMPLEMENTATION):
        failures.append('implementation')
    for relative, expected in implementation.items():
        path = root / relative
        if not path.is_file() or source_digest(path) != expected:
            failures.append(relative)
    if failures:
        raise ValueError(f'Stale neck assets: {failures[:20]}. Run GenerateNeckModels.py --game-data <NWN/data> --apply.')
    print(f'Neck catalog: {len(manifest["aliases"])} generated models match their audited sources.')


if __name__ == '__main__':
    check(Path(__file__).resolve().parents[1])
