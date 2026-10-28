#!/usr/bin/env python3
"""Compile the shared head profiles and compare color/coverage to their approved source shaders."""
from pathlib import Path
import argparse
import collections
import ctypes as ct
import math
import re
import subprocess

import HeadTintAssets as head
import TestTintShaderMaterials as tint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, required=True)
    args = parser.parse_args()
    manifest = head.load_manifest()
    engine = tint.EngineShaders(args.game_data, head.ROOT / 'sw_shader',
                               args.game_data.parent / 'bin/win32/nwmain.exe')
    adapter = tint.ADAPTER + '''
uniform int envMapCube;
vec3 vVertexTexCoordsEnvironmentCube=vec3(0.0);
vec2 vVertexTexCoordsEnvironment=vec2(0.0);
vec3 SampleEnvironmentMapCube(vec3 uv,float bias){return vec3(0.2,0.3,0.4);}
vec3 SampleEnvironmentMap(vec2 uv,float bias){return vec3(0.2,0.3,0.4);}
vec3 CalculateEnvironmentTexCoordsCube(vec3 n){return n;}
vec2 CalculateEnvironmentTexCoords(vec3 n){return n.xy;}
'''
    # Native compilation below checks lighting setup. Numeric comparisons expose
    # the production tint and alpha math before scene-dependent standard lighting.
    standard = tint.STANDARD[:tint.STANDARD.index('void ApplyStandardShader()')] + 'void ApplyStandardShader() {}\n'
    material = tint.stock_material(args.game_data)
    vertex = '#version 120\nuniform vec2 testUV; varying vec2 vVertexTexCoords; void main(){gl_Position=gl_Vertex;vVertexTexCoords=testUV;}'
    source_git = subprocess.Popen(['git', '-C', str(head.ROOT), 'cat-file', '--batch'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    def original(path):
        source_git.stdin.write(f"{manifest['sourceCommit']}:{path}\n".encode())
        source_git.stdin.flush()
        info = source_git.stdout.readline().split()
        if len(info) != 3 or info[1] != b'blob':
            raise ValueError(f'Approved source Git blob unavailable: {path}')
        data = source_git.stdout.read(int(info[2]))
        source_git.stdout.read(1)
        return data.decode('utf-8-sig')

    gl = tint.OpenGL()
    test = tint.MaterialTest(gl)
    programs = {}
    comparisons = 0
    try:
        pairs = collections.defaultdict(set)
        for name, entry in manifest['materials'].items():
            text = head.material_source(entry)
            vs = re.search(r'^customshaderVS\s+(\S+)', text, re.M)[1]
            pairs[vs, entry['shader']].update(tint.material_parameters(text))
        tint.compile_engine_pairs(test, engine, sorted(pairs), pairs)
        clear = gl.function('glClear', None, ct.c_uint)
        clear_color = gl.function('glClearColor', None, ct.c_float, ct.c_float, ct.c_float, ct.c_float)
        vector2 = gl.function('glUniform2f', None, ct.c_int, ct.c_float, ct.c_float)
        def program(name, old):
            key = name, old
            if key not in programs:
                shader = original(f'sw_shader/{name}.shd') if old else engine.read(name)
                shader = shader.replace('#include "inc_standard"', adapter + material + standard)
                shader = shader.replace('#include "inc_hd_tint"', (head.ROOT / 'sw_shader/inc_hd_tint.shd').read_text())
                programs[key] = test.program('#version 120\n' + shader, vertex)
            return programs[key]

        for name, entry in manifest['materials'].items():
            old_text = original(f'sw_tint_mtr/{name}.mtr')
            params = tint.material_parameters(old_text), tint.material_parameters(head.material_source(entry))
            programs_pair = program(entry['sourceShader'], True), program(entry['shader'], False)
            values = head.feature_defaults() | entry['parameters']
            uvs = [(0.5, 0.5)]
            if values['lipMode']:
                uvs += [(values['lipU0'] + values['lipUSpan'] * .5, values['lipV0'] + values['lipVSpan'] * .5)]
            if values['hairFilter']:
                uvs += [(0.2, 0.6)]
            for uv in uvs:
                for layer in range(10):
                    for mask in (.2, .3, .5, .8):
                        for rgb in (None, (layer, 175, 110, 220)):
                            pixels = []
                            for prog, parameters in zip(programs_pair, params):
                                test.use(prog)
                                for flag in ('useNativePalette', 'useTexture1Alpha', 'useTexture9Alpha'):
                                    # Simulate a previous material enabling every
                                    # optional input. Current MTRs must reset it.
                                    test.scalar(test.location(prog, flag.encode()), float(prog == programs_pair[1]))
                                tint.upload_material_parameters(test, prog, parameters)
                                if prog == programs_pair[1]:
                                    for flag in ('useNativePalette', 'useTexture1Alpha', 'useTexture9Alpha'):
                                        location = test.location(prog, flag.encode())
                                        if location >= 0:
                                            retained = ct.c_float()
                                            test.get_uniform(prog, location, ct.byref(retained))
                                            if retained.value != values[flag]:
                                                raise AssertionError(f'{name}: stale material flag {flag}')
                                vector2(test.location(prog, b'testUV'), *uv)
                                for unit, rgba in {6: (mask, mask, mask, mask), 8: (.4, .7, 0, 1)}.items():
                                    test.active_texture(0x84C0 + unit)
                                    test.bind_texture(0x0DE1, unit + 1)
                                    for parameter in (0x2800, 0x2801):
                                        test.texture_parameter(0x0DE1, parameter, 0x2600)
                                    test.texture_image(0x0DE1, 0, 0x8814, 1, 1, 0, 0x1908, 0x1406, (ct.c_float * 4)(*rgba))
                                    test.integer(test.location(prog, f'texUnit{unit}'.encode()), unit)
                                clear_color(-2, -2, -2, -2)
                                clear(0x4000)
                                test.sample(prog, 1, .7, rgb=rgb, palette_layer=layer)
                                pixel = (ct.c_float * 4)()
                                test.read(4, 4, 1, 1, 0x1908, 0x1406, pixel)
                                pixels.append(tuple(pixel))
                            comparisons += 1
                            if any(not math.isfinite(a) or not math.isfinite(b) or abs(a-b) > .00005
                                   for a, b in zip(*pixels)):
                                raise AssertionError(f'{name} UV={uv} layer={layer} mask={mask} RGB={rgb}: {pixels}')
        print(f'Head palette/RGB color and coverage parity passed: {comparisons} comparisons across '
              f"{len(manifest['materials'])} materials, all 10 channels, cutoffs, lips and scalp filtering.")
    finally:
        for prog in programs.values():
            test.delete_program(prog)
        gl.close()
        source_git.stdin.close()
        source_git.wait(timeout=10)


if __name__ == '__main__':
    main()
