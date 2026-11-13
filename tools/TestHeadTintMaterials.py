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


def check_metal_finish(test,game_data,shader_root):
    material=tint.stock_material(game_data)
    include=(shader_root/'inc_hd_tint.shd').read_text()
    wrapper=(shader_root/'fs_head_full.shd').read_text()
    line='if (layer>1.5 && layer<3.5) fEnvMapLevel *= clamp(metalReflectanceScale,0.0,1.0);'
    assert line in include
    checks=0
    for gamma in (False,True):
        adapter=tint.ADAPTER
        if gamma:adapter=adapter.replace('vec3 ApplyColorSpace(vec3 value) { return value; }','vec3 ApplyColorSpace(vec3 value) { return value*value; }')
        def program(text):
            return test.program('#version 120\n'+wrapper.replace('#include "inc_standard"',adapter+material+tint.STANDARD).replace('#include "inc_hd_tint"',text))
        full=program(include);negative=program(include.replace(line,''))
        try:
            for layer in range(10):
                for alpha in (1.0,.8,.4):
                    for rgb in (None,(layer,90,145,190)):
                        test.use(full);test.scalar(test.location(full,b'metalReflectanceScale'),1)
                        baseline=test.sample(full,1,alpha,palette_layer=layer,rgb=rgb)
                        test.scalar(test.location(full,b'metalReflectanceScale'),.04)
                        actual=test.sample(full,1,alpha,palette_layer=layer,rgb=rgb)
                        if layer in (2,3):
                            env=(1-alpha)*.04
                            spec=.04+.94*min(env*8,1)
                            expected=(spec,max(0,min(1,3*spec-.6)),.6+(.125-.6)*min(env*3,1))
                        else:expected=baseline
                        tint.near(actual,expected,f'scoped finish gamma={gamma} layer={layer} alpha={alpha} rgb={rgb}')
                        checks+=1
            actual=test.sample(negative,1,.4,palette_layer=2)
            env=.6*.04;expected=(.04+.94*env*8,max(0,3*(.04+.94*env*8)-.6),.6+(.125-.6)*env*3)
            assert max(abs(a-b) for a,b in zip(actual,expected))>.05,'Missing finish control negative test failed'
        finally:
            test.delete_program(full);test.delete_program(negative)
    print(f'Scoped finish: {checks} GPU state checks passed; all 8 nonmetal layers unchanged, palette/RGB coverage retained, missing-control negatives failed as expected.')


def check_skin_detail_gain(test, game_data):
    """The Trandoshan gain covers both palette skin and fixed diffuse details."""
    root = head.ROOT / 'sw_shader'
    material = tint.stock_material(game_data)
    standard = tint.STANDARD[:tint.STANDARD.index('void ApplyStandardShader()')] + 'void ApplyStandardShader() {}\n'
    wrapper = (root / 'fs_head_mapped.shd').read_text()
    include = (root / 'inc_hd_tint.shd').read_text()
    program = test.program('#version 120\n' + wrapper.replace('#include "inc_standard"',
        tint.ADAPTER + material + standard).replace('#include "inc_hd_tint"', include))
    checks = 0
    try:
        test.use(program)
        test.scalar(test.location(program, b'detailDiffuse'), 1)
        for coverage in (0, 0.5, 1):
            test.active_texture(0x84C0 + 8)
            test.bind_texture(0x0DE1, 9)
            pixels = (ct.c_float * 4)(coverage, 0, 0, 1)
            test.texture_image(0x0DE1, 0, 0x8814, 1, 1, 0, 0x1908, 0x1406, pixels)
            test.integer(test.location(program, b'texUnit8'), 8)
            for layer in range(10):
                for gain in (1, 1.4, 1.47, 1.82):
                    test.scalar(test.location(program, b'skinGain'), gain)
                    test.scalar(test.location(program, b'detailSkinGain'), gain)
                    actual = test.sample(program, 1, 1, palette_layer=layer,
                                         authored_diffuse=(0.3, 0.4, 0.2, 1))
                    if gain == 1:
                        baseline = actual
                    else:
                        expected = tuple(v * (gain if layer == 0 else 1) for v in baseline)
                        tint.near(actual, expected, f'skin detail coverage={coverage} layer={layer}')
                        checks += 1
        # A different material's older skin-only gain must not brighten its fixed detail.
        test.scalar(test.location(program, b'skinGain'), 0.8)
        test.scalar(test.location(program, b'detailSkinGain'), 1)
        tint.near(test.sample(program, 1, 1, palette_layer=0, authored_diffuse=(0.3, 0.4, 0.2, 1)),
                  (0.3, 0.4, 0.2), 'fixed detail keeps its previous gain by default')
    finally:
        test.delete_program(program)
    print(f'Skin brightness: {checks} GPU comparisons passed; dyed and fixed detail gain together, non-skin unchanged.')


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
        check_metal_finish(test, args.game_data, head.ROOT / 'sw_shader')
        check_skin_detail_gain(test, args.game_data)
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
            flags = ('useNativePalette', 'useTexture1Alpha', 'useTexture9Alpha', 'metalReflectanceScale', 'detailSkinGain')
            for flag in flags:
                test.scalar(test.location(program, flag.encode()), 0 if flag == 'metalReflectanceScale' else 1)
            tint.upload_material_parameters(test, program,
                                             tint.material_parameters(head.material_source(entry)))
            expected = head.feature_defaults() | entry['parameters']
            for flag in flags:
                location = test.location(program, flag.encode())
                if location < 0:
                    continue
                actual = ct.c_float()
                test.get_uniform(program, location, ct.byref(actual))
                if abs(actual.value - expected[flag]) > 0.000001:
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
