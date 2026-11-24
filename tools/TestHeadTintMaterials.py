#!/usr/bin/env python3
"""Validate current head shaders, native palette values and material-state resets."""
from pathlib import Path
import argparse
import collections
import ctypes as ct
import math
import re

import HeadTintAssets as head
import TestTintShaderMaterials as tint


ADAPTER = tint.ADAPTER + """
vec3 RevertColorSpace(vec3 value) { return value; }
void ComputeLighting(inout vec4 value, vec3 normal) {}
void ApplyKeyhole(inout vec4 value) {}
void ApplyFog(inout vec4 value) {}
void ApplyDebugModeOutput(inout vec4 value) {}
"""


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
        adapter = ADAPTER
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
        adapter=ADAPTER
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
        ADAPTER + material + standard).replace('#include "inc_hd_tint"', include))
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



def check_head_coatings(test, game_data):
    root = head.ROOT / 'sw_shader'
    material = tint.stock_material(game_data)
    include = (root / 'inc_hd_tint.shd').read_text()
    standard = tint.STANDARD[:tint.STANDARD.index('void ApplyStandardShader()')] + 'void ApplyStandardShader() {}\n'
    checks = 0
    for gamma in (False, True):
        adapter = ADAPTER
        if gamma:
            adapter = adapter.replace('vec3 ApplyColorSpace(vec3 value) { return value; }',
                                      'vec3 ApplyColorSpace(vec3 value) { return value*value; }')
        for name, features in head.PROFILES.items():
            wrapper = (root / (name + '.shd')).read_text()
            program = test.program('#version 120\n' + wrapper.replace('#include "inc_standard"',
                adapter + material + standard).replace('#include "inc_hd_tint"', include))
            try:
                test.use(program)
                for flag in ('hairMatte', 'hairReflectivity', 'detailDiffuse', 'detailGlowChannel', 'glowWholeSurface'):
                    test.scalar(test.location(program, flag.encode()), 1)
                for alpha in (1.0, 0.8):
                    coat = (0.1, 0.3, 0.7, alpha)
                    bindings = {4: coat, 8: (1.0, 0.0, 0.0, 1.0)}
                    test.scalar(test.location(program, b'progFxCoat'), 0)
                    ordinary = test.sample(program, 1, .8, palette_layer=1, extra_textures=bindings)
                    unrelated_coat = test.sample(program, 1, .8, palette_layer=1,
                        extra_textures={4: (.9, .1, .2, .4), 8: bindings[8]})
                    tint.near(unrelated_coat, ordinary, f'{name}: disabled coating must not replace authored detail')
                    for sequence in (1.0, 1.0001):
                        test.scalar(test.location(program, b'progFxCoat'), sequence)
                        actual = test.sample(program, 1, .8, palette_layer=1, extra_textures=bindings)
                        env = 1-alpha if features[-1] else 0
                        expected = tuple(c*(1-env)+.666667*env for c in coat[:3])
                        if gamma:
                            expected = tuple(c*c for c in expected)
                        tint.near(actual, expected, f'{name}: coating slot 4, matte/glow suppression, phase={sequence}')
                        checks += 1
            finally:
                test.delete_program(program)
    print(f'Head coatings: {checks} GPU comparisons passed; authored slot 8 retained and refresh phases keep the same coat.')


def check_head_glow_fog(test, engine):
    """Keep masked eye/lens emission visible through fog; fog ordinary surfaces."""
    root = head.ROOT / 'sw_shader'
    include = (root / 'inc_hd_tint.shd').read_text()
    prefix = include[:include.index('void main()')]
    vertex = '''#version 120
varying float fFogFragCoord;
uniform float probeFog;
void main() { gl_Position = gl_Vertex; fFogFragCoord = probeFog; }
'''
    actual_main = '''
void main() {
    FragmentColor = vec4(0.2, 0.3, 0.4, 1.0);
    ApplyHeadShader(vec3(0.8, 0.35, 0.1), vec2(0.4, 0.8), 8.0);
    gl_FragColor = FragmentColor;
}
'''
    reference_main = '''
uniform vec4 referenceColor;
uniform float referenceMode;
void main() {
    FragmentColor = referenceColor;
    ApplyFog(FragmentColor);
    if (referenceMode < 2.5 && referenceMode > 0.5)
        FragmentColor.rgb = mix(FragmentColor.rgb, ApplyColorSpace(vec3(0.8, 0.35, 0.1)),
                               referenceMode < 1.5 ? 0.4 : 0.8);
    if (referenceMode > 2.5 && referenceMode < 3.5) {
        vec3 emission = vec3(0.8, 0.35, 0.1)*1.75 + vec3(1.0, 0.4375, 0.125)*0.16;
        FragmentColor.rgb = emission/(vec3(1.0) + emission*0.32);
    }
    #if GAMMA_CORRECTION == 1
        #if COLOR_CORRECTION_TYPE == 1
            FragmentColor = ColorClamp(FragmentColor);
        #elif COLOR_CORRECTION_TYPE == 2
            FragmentColor.rgb = ACESFilm(FragmentColor.rgb);
        #endif
    #endif
    gl_FragColor = FragmentColor;
}
'''
    # Attenuating the masked emission recreates the owner-rejected dim eyes.
    negative = prefix.replace('ApplyFog(FragmentColor);', '').replace(
        '\t#if GAMMA_CORRECTION == 1',
        'ApplyFog(FragmentColor);\n\t#if GAMMA_CORRECTION == 1')
    modes = ('none', 'detail-r', 'detail-g', 'layer', 'whole')
    fog_color = (0.12, 0.19, 0.27)
    checks = 0
    for name in head.PROFILES:
        wrapper = (root / (name + '.shd')).read_text().replace(
            '#define LIGHTING 1', '#define LIGHTING 0').replace('#define KEYHOLING 1', '#define KEYHOLING 0')

        def program(text, gamma, correction):
            source = engine.source(name, True, (1, 0, gamma, 0, 0), override=wrapper.replace(
                '#include "inc_hd_tint"', text))
            source, count = re.subn(r'(#define COLOR_CORRECTION_TYPE\s+)\S+',
                                    lambda match: match[1] + str(correction), source, count=1)
            if count != 1:
                raise AssertionError('Installed color-correction control was not found')
            return test.program(source, vertex)

        def configure(item, mode, fog):
            test.use(item)
            for flag, value in (('probeFog', fog), ('detailGlowChannel', 0),
                                ('glowMinLayer', -1), ('glowMaxLayer', -1), ('glowWholeSurface', 0)):
                test.scalar(test.location(item, flag.encode()), value)
            test.vector(test.location(item, b'fogColor'), *fog_color, 1)
            if mode.startswith('detail'):
                test.scalar(test.location(item, b'detailGlowChannel'), 1 if mode.endswith('r') else 2)
            elif mode == 'layer':
                test.scalar(test.location(item, b'glowMinLayer'), 8)
                test.scalar(test.location(item, b'glowMaxLayer'), 8)
            elif mode == 'whole':
                test.scalar(test.location(item, b'glowWholeSurface'), 1)

        for gamma in (0, 1):
            base = program(prefix + actual_main, gamma, 0)
            broken = program(negative + actual_main, gamma, 0)
            try:
                for mode in modes:
                    configure(base, 'whole' if mode == 'whole' else 'none', 0)
                    unfogged = test.sample(base, 1, 1)
                    for correction in (0, 1, 2):
                        actual = program(prefix + actual_main, gamma, correction)
                        reference = program(prefix + reference_main, gamma, correction)
                        try:
                            for fog in (0, 0.5, 1):
                                configure(actual, mode, fog)
                                observed = test.sample(actual, 1, 1)
                                configure(reference, 'none', fog)
                                test.vector(test.location(reference, b'referenceColor'), *unfogged, 1)
                                test.scalar(test.location(reference, b'referenceMode'), modes.index(mode))
                                expected = test.sample(reference, 1, 1)
                                tint.near(observed, expected,
                                          f'{name}: {mode} gamma={gamma} correction={correction} fog={fog}')
                                checks += 1
                        finally:
                            test.delete_program(actual)
                            test.delete_program(reference)
                    if mode in ('detail-r', 'detail-g', 'layer'):
                        configure(broken, mode, 1)
                        if max(abs(a-b) for a, b in zip(test.sample(broken, 1, 1), fog_color)) > 0.006:
                            raise AssertionError('Fog-attenuated eye-emission negative control did not fail')
            finally:
                test.delete_program(base)
                test.delete_program(broken)
    print(f'Head emission finalization: {checks} native GPU comparisons passed across all profiles, '
          'glow paths, fog amounts and color-correction modes; dim-eye negative controls failed.')


def check_head_layer_filtering(test, engine):
    """Minified/oblique mipmapped masks must retain their base-level categories."""
    root = head.ROOT / 'sw_shader'
    include = (root / 'inc_hd_tint.shd').read_text()
    prefix = include[:include.index('void main()')]
    probe = '''
void main() {
    gl_FragColor = vec4(HeadTintLayer(texUnit7,vVertexTexCoords,vec2(64.0))/10.0,
                       texture2D(texUnit7,fract(vVertexTexCoords),-10.0).r,
                       HeadTintLayer(texUnit9,vVertexTexCoords,vec2(64.0))/10.0,1.0);
}
'''
    vertex = '''#version 120
varying vec2 vVertexTexCoords;
uniform vec4 probeTransform;
void main() {
    gl_Position = gl_Vertex;
    vVertexTexCoords = gl_Vertex.xy*probeTransform.xy+probeTransform.zw;
}
'''
    filtered = prefix.replace('texelFetch(mask,texel,0).g',
                              'texture2D(mask,(floor(fract(uv)*size)+vec2(0.5))/size,-10.0).g')
    parameter_f = test.gl.function('glTexParameterf', None, ct.c_uint, ct.c_uint, ct.c_float)
    delete = test.gl.function('glDeleteTextures', None, ct.c_int, ct.POINTER(ct.c_uint))
    textures = (ct.c_uint * 2)(300, 301)
    checks = 0
    try:
        for unit, texture in ((7, 300), (9, 301)):
            test.active_texture(0x84C0 + unit)
            test.bind_texture(0x0DE1, texture)
            for level in range(7):
                size = 64 >> level
                data = []
                for y in range(size):
                    for x in range(size):
                        parity = (x+y) % 2
                        data.extend((parity if level == 0 else 0.5,
                                     (0.05+0.8*parity) if level == 0 else 0.45, 0, 1))
                test.texture_image(0x0DE1, level, 0x8814, size, size, 0,
                                   0x1908, 0x1406, (ct.c_float * len(data))(*data))
            for flag, value in ((0x2800, 0x2601), (0x2801, 0x2703), (0x813D, 6)):
                test.texture_parameter(0x0DE1, flag, value)
            parameter_f(0x0DE1, 0x84FE, 16)
        if test.error():
            raise AssertionError('Anisotropic mipmapped mask setup failed')
        for name in head.PROFILES:
            wrapper = (root / (name+'.shd')).read_text()
            programs = []
            try:
                for source in (prefix, filtered):
                    programs.append(test.program(engine.source(name, True, (1, 0, 0, 0, 0),
                        override=wrapper.replace('#include "inc_hd_tint"', source+probe)), vertex))
                for scale_x, scale_y in ((3.1, 0.07), (0.07, 3.1), (1.3, 0.1)):
                    results = []
                    for program in programs:
                        test.use(program)
                        test.integer(test.location(program, b'texUnit7'), 7)
                        test.integer(test.location(program, b'texUnit9'), 9)
                        test.vector(test.location(program, b'probeTransform'), scale_x, scale_y, 0.5313, 0.4737)
                        test.begin(0x0007)
                        for point in ((-1,-1),(1,-1),(1,1),(-1,1)):
                            test.vertex(*point)
                        test.end()
                        pixels = (ct.c_float * 256)()
                        test.read(0, 0, 8, 8, 0x1908, 0x1406, pixels)
                        results.append(pixels)
                    negative_failed = False
                    for y in range(8):
                        for x in range(8):
                            u = ((x+0.5)/4-1)*scale_x+0.5313
                            v = ((y+0.5)/4-1)*scale_y+0.4737
                            parity = (math.floor((u % 1)*64)+math.floor((v % 1)*64)) % 2
                            expected = parity*0.8
                            index = 4*(y*8+x)
                            for channel in (0, 2):
                                if abs(results[0][index+channel]-expected) > 0.0001:
                                    raise AssertionError(f'{name}: filtered category at {x},{y}: '
                                                         f'{results[0][index+channel]} != {expected}; uv={u},{v}')
                                checks += 1
                            if not 0.01 < results[0][index+1] < 0.99:
                                raise AssertionError('Shade filtering was unexpectedly disabled')
                            negative_failed |= abs(results[1][index]-expected) > 0.1
                    if not negative_failed:
                        raise AssertionError('Filtered-category negative control did not fail')
            finally:
                for program in programs:
                    test.delete_program(program)
    finally:
        delete(2, textures)
    print(f'Head categorical masks: {checks} anisotropic/mipmapped GPU checks passed; '
          'shade filtering retained and filtered-category negative controls failed.')


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
        check_head_coatings(test, args.game_data)
        check_head_glow_fog(test, engine)
        check_head_layer_filtering(test, engine)
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
            flags = ('useNativePalette', 'useTexture1Alpha', 'useTexture9Alpha', 'metalReflectanceScale', 'detailSkinGain', 'progFxCoat')
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
