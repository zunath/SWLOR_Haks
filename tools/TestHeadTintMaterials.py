#!/usr/bin/env python3
"""Validate current head shaders, native palette values and material-state resets."""
from pathlib import Path
import argparse
import collections
import ctypes as ct
import re

import HeadTintAssets as head
import TestTintShaderMaterials as tint


def check_reflectance_state(test, game_data):
    """Expose installed material state; retain production head tint/blend code.

    The numeric adapter uses a nonlinear color transform to distinguish blend
    order. Production engine includes are compiled separately below.
    """
    root = head.ROOT / 'sw_shader'
    material = tint.stock_material(game_data)
    include = (root / 'inc_hd_tint.shd').read_text()
    checks = 0
    for gamma in (False, True):
        adapter = tint.ADAPTER
        if gamma:
            adapter = adapter.replace('vec3 ApplyColorSpace(vec3 value) { return value; }',
                                      'vec3 ApplyColorSpace(vec3 value) { return value*value; }')

        def program(name, head_include=include):
            source = (root / (name + '.shd')).read_text()
            source = source.replace('#include "inc_standard"', adapter + material + tint.STANDARD)
            source = source.replace('#include "inc_hd_tint"', head_include)
            return test.program('#version 120\n' + source)

        reference = program('fs_plt_tinter_nm')
        mapped = program('fs_head_mapped')
        full = program('fs_head_full')
        # Isolate each reported bug while keeping the rest of the repaired code.
        stale_coverage = include.replace(
            'fEnvMapLevel = (hairMatte>0.5 && layer>0.5 && layer<1.5) ? 0.0 : 1.0-paletteColor.a;',
            'fEnvMapLevel = 0.0;')
        wrong_order = include.replace('vec3 surfaceColor = vTint;',
                                      'vec3 surfaceColor = ApplyColorSpace(vTint);').replace(
                                          'surfaceColor = ApplyColorSpace(surfaceColor);', '')
        stale = program('fs_head_full', stale_coverage)
        early_gamma = program('fs_head_full', wrong_order)
        try:
            for alpha in (1.0, 0.8, 0.4):
                tint.near(test.sample(mapped, 1, alpha, mapped=True, palette_layer=2),
                          (0.12,0.0,0.125+0.475*0.88**2),
                          f'authored head specular map gamma={gamma} alpha={alpha}')
                for color in (False, True):
                    expected = test.sample(reference, 1, alpha, color=color, palette_layer=2)
                    tint.near(test.sample(full, 1, alpha, color=color, palette_layer=2), expected,
                              f'head legacy blend gamma={gamma} alpha={alpha} color={color}')
                    checks += 1
                checks += 1
            expected = test.sample(reference, 1, 0.8, palette_layer=2)
            actual = test.sample(stale, 1, 0.8, palette_layer=2)
            if max(abs(a-b) for a,b in zip(actual, expected)) < 0.006:
                raise AssertionError('Missing mapped-head palette coverage negative control did not fail')
            if gamma:
                expected = test.sample(reference, 1, 0.8, color=True, palette_layer=2)
                actual = test.sample(early_gamma, 1, 0.8, color=True, palette_layer=2)
                if max(abs(a-b) for a,b in zip(actual, expected)) < 0.006:
                    raise AssertionError('Early-linearization negative control did not fail')
        finally:
            for item in (reference, mapped, full, stale, early_gamma):
                test.delete_program(item)
    print(f'Head reflectance state passed: {checks} GPU comparisons, with stale-coverage and '
          'early-linearization negative controls.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, required=True)
    args = parser.parse_args()
    manifest = head.load_manifest()
    engine = tint.EngineShaders(args.game_data, head.ROOT / 'sw_shader',
                               args.game_data.parent / 'bin/win32/nwmain.exe')
    pairs = collections.defaultdict(set)
    material_pairs = {}
    for name, entry in manifest['materials'].items():
        source = head.material_source(entry)
        vertex = re.search(r'^customshaderVS\s+(\S+)', source, re.M)[1]
        pair = vertex, entry['shader']
        material_pairs[name] = pair
        pairs[pair].update(tint.material_parameters(source))

    gl = tint.OpenGL()
    programs = {}
    try:
        test = tint.MaterialTest(gl)
        check_reflectance_state(test, args.game_data)
        tint.compile_engine_pairs(test, engine, sorted(pairs), pairs)
        # Expected RGBA comes from installed original palettes and native scheme
        # integers. This remains runnable after a squash or shallow clone.
        tint.check_native_palette_fallback(test, engine, sorted(pairs), head.ROOT)
        checked = 0
        for name, entry in manifest['materials'].items():
            vertex, fragment = material_pairs[name]
            if (vertex, fragment) not in programs:
                config = 1, 0, 0, 0, 0
                programs[vertex, fragment] = test.program(engine.source(fragment, True, config),
                                                         engine.source(vertex, False, config))
            program = programs[vertex, fragment]
            test.use(program)
            flags = ('useNativePalette', 'useTexture1Alpha', 'useTexture9Alpha')
            for flag in flags:
                test.scalar(test.location(program, flag.encode()), 1)
            tint.upload_material_parameters(test, program,
                                             tint.material_parameters(head.material_source(entry)))
            expected = head.feature_defaults() | entry['parameters']
            for flag in flags:
                location = test.location(program, flag.encode())
                if location < 0:
                    continue
                actual = ct.c_float()
                test.get_uniform(program, location, ct.byref(actual))
                if actual.value != expected[flag]:
                    raise AssertionError(f'{name}: stale {flag}={actual.value}, expected {expected[flag]}')
                checked += 1
        print(f'Head material-state resets passed: {checked} active optional inputs across '
              f"{len(manifest['materials'])} materials; no historical Git blobs required.")
    finally:
        for program in programs.values():
            test.delete_program(program)
        gl.close()


if __name__ == '__main__':
    main()
