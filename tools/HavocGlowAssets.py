"""Authored Havoc illumination profiles outside ordinary PLT tint masks."""
import re
from pathlib import Path

MAPPED_GLOW_SHADER = 'fs_hav_emit'
MAPPED_GLOW_MATERIALS = frozenset(
    f'{gender}0_fore{side}282' for gender in ('pmh', 'pfh') for side in ('l', 'r')
)
EMISSIVE_TINT_LAYERS = {'troopbracer': [7]}
# Undyed Havoc materials that still take ProgFX coats. progfxcoat.2da lists each
# model that binds one, so the server can write the coat to it.
COAT_ONLY_MATERIALS = {
    'havoc_box': tuple(f'p{gender}{race}0_chest316' for gender in 'fm' for race in 'deho'),
}


def model_rows(models, read_bindings):
    """Advertise Leather 2 only for real, explicit Havoc overlay bindings."""
    rows = []
    for model, path in sorted(models.items()):
        if not re.fullmatch(r'p[mf][hdeo]0_fore[lr]282', model):
            continue
        for bitmap, material in read_bindings(path):
            if material in EMISSIVE_TINT_LAYERS:
                row = (model, material, tuple(EMISSIVE_TINT_LAYERS[material]))
                if row not in rows:
                    rows.append(row)
    return rows


def validate(root: Path):
    errors = []
    for name in sorted(MAPPED_GLOW_MATERIALS):
        path = root/'sw_tint_mtr'/f'{name}.mtr'
        text = path.read_text().lower() if path.exists() else ''
        for required in ('customshaderfs fs_hav_emit', 'parameter float glowlayer 7.0'):
            if text.splitlines().count(required) != 1:
                errors.append(f'{name}: missing or duplicate Havoc glow directive {required}')
    for name, expected in {
        'havoc_box': ('customshaderFS fs_hav_static', 'texture5 pmh0_chest316_I', 'texture8 progfx_coat', 'parameter float progFxCoat 0.0'),
        'troopbracer': ('customshaderFS fs_hav_dev', 'texture6 havbrac_glow', 'parameter float glowLayer 7.0', 'parameter float glowOverlay 1.0'),
    }.items():
        path = root/'sw_item'/f'{name}.mtr'
        text = path.read_text() if path.exists() else ''
        for directive in expected:
            if text.splitlines().count(directive) != 1:
                errors.append(f'{name}: missing or duplicate {directive}')
    expected_rows = sorted(
        (model, material) for material, models in COAT_ONLY_MATERIALS.items() for model in models
    )
    coat_2da = root/'sw_2da'/'progfxcoat.2da'
    coat_rows = [
        tuple(line.split()[1:3]) for line in
        (coat_2da.read_text().splitlines()[3:] if coat_2da.exists() else [])
        if line.strip()
    ]
    if sorted(coat_rows) != expected_rows:
        errors.append('progfxcoat.2da does not list exactly the coat-only Havoc materials')
    for name in ('fs_hav_emit', 'fs_hav_dev', 'fs_hav_static'):
        if not (root/'sw_shader'/f'{name}.shd').exists():
            errors.append(f'Havoc glow shader missing: {name}')
    return errors
