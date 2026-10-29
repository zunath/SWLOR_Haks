#!/usr/bin/env python3
"""Validate current head shaders, native palette values and material-state resets."""
from pathlib import Path
import argparse
import collections
import ctypes as ct
import re

import HeadTintAssets as head
import TestTintShaderMaterials as tint


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
        tint.compile_engine_pairs(test, engine, sorted(pairs), pairs)
        # Expected RGBA comes from installed original palettes and native scheme
        # integers. This remains runnable after a squash or shallow clone.
        tint.check_native_palette_fallback(test, engine, sorted(pairs), head.ROOT)
        tint.check_native_palette_fallback(test, engine, sorted(pairs), head.ROOT,
                                          head.ROOT / 'sw_tint2/cu_pal1024.tga')
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
