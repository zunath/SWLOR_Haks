#!/usr/bin/env python3
"""Compare tint lighting inputs with NWN's original PLT diffuse material path.

Run: python -B tools/TestTintPaletteLighting.py --game-data ".../NWN/data"
Uses installed engine includes and original palettes, with a hidden WGL context.
"""
import argparse
import ctypes as ct
from itertools import product
from pathlib import Path
import re

from TestTintShaderMaterials import EngineShaders, MaterialTest, OpenGL, stock_palette_pixels


def check_palette_lighting(test, engine, root):
    gl = test.gl
    attribute = gl.function("glGetAttribLocation", ct.c_int, ct.c_uint, ct.c_char_p)
    pointer = gl.function("glVertexAttribPointer", None, ct.c_uint, ct.c_int, ct.c_uint, ct.c_ubyte, ct.c_int, ct.c_void_p)
    enable = gl.function("glEnableVertexAttribArray", None, ct.c_uint)
    disable = gl.function("glDisableVertexAttribArray", None, ct.c_uint)
    matrix = gl.function("glUniformMatrix4fv", None, ct.c_int, ct.c_int, ct.c_ubyte, ct.c_void_p)
    draw = gl.function("glDrawArrays", None, ct.c_uint, ct.c_int, ct.c_int)
    viewport = gl.function("glViewport", None, ct.c_int, ct.c_int, ct.c_int, ct.c_int)
    positions = (ct.c_float * 12)(-1, -1, -.5, 1, 3, -1, -.5, 1, -1, 3, -.5, 1)
    coordinates = (ct.c_float * 6)(0, 0, 2, 0, 0, 2)
    identity = (ct.c_float * 16)(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    rows = (("rowSkin", 0), ("rowHair", 176), ("rowMetal1", 352), ("rowMetal2", 528),
            ("rowCloth1", 704), ("rowCloth2", 704), ("rowLeath1", 880), ("rowLeath2", 880),
            ("rowTat1", 1056), ("rowTat2", 1056))
    palettes = stock_palette_pixels(engine.game_data)
    original = [(ct.c_float * (256 * 10 * 4))(*(value / 255
        for palette in palettes for shade in range(256)
        for value in (palette[((175 - color) * 256 + shade) * 4 + 2],
                      palette[((175 - color) * 256 + shade) * 4 + 1],
                      palette[((175 - color) * 256 + shade) * 4],
                      palette[((175 - color) * 256 + shade) * 4 + 3]))) for color in range(176)]
    mask = (ct.c_float * (256 * 10 * 4))(*(value for layer in range(10) for shade in range(256)
        for value in (shade / 255, (layer + .5) / 10, 0, 1)))
    atlas = ct.create_string_buffer((root / "sw_item/plt_palette.tga").read_bytes()[18:])
    output = (ct.c_float * (256 * 10 * 4))()

    def texture(unit, width, height, data, *, atlas_texture=False):
        test.active_texture(0x84C0 + unit)
        test.bind_texture(0x0DE1, unit + 1)
        test.texture_image(0x0DE1, 0, 0x8058 if atlas_texture else 0x8814, width, height, 0,
                           0x80E1 if atlas_texture else 0x1908, 0x1401 if atlas_texture else 0x1406, data)
        for parameter in (0x2800, 0x2801):
            test.texture_parameter(0x0DE1, parameter, 0x2600)
        test.texture_parameter(0x0DE1, 0x813D, 0)

    def prepare(program):
        test.use(program)
        for name in ("m_mv", "m_proj", "m_mvp", "m_texture", "m_view_inv", "m_view"):
            matrix(test.location(program, name.encode()), 1, 0, identity)
        test.vector(test.location(program, b"materialFrontDiffuse"), 1, 1, 1, 1)
        test.integer(test.location(program, b"texture0Bound"), 1)
        for unit in (0, 1, 2, 3, 6, 7, 9, 10):
            test.integer(test.location(program, f"texUnit{unit}".encode()), unit)
        test.scalar(test.location(program, b"tintMapWidth"), 256)
        test.scalar(test.location(program, b"tintMapHeight"), 10)
        active = []
        for name, size, data in (("vPos", 4, positions), ("vTcIn", 2, coordinates)):
            index = attribute(program, name.encode())
            if index >= 0:
                enable(index)
                pointer(index, size, 0x1406, 0, 0, data)
                active.append(index)
        return active

    # Inspect the actual engine surface and cached specular color before scene
    # lighting. The reference runs the unmodified SetupStandardShaderInputs with
    # original PLT RGBA as texture0, rather than duplicating its math in Python.
    inspect = """
    #if SPECULAR_LIGHT == 1 && (FRAGMENT_LIGHTING == 1 || NORMAL_MAP == 1)
        gl_FragColor = vec4(inspectSpecular != 0 ? SpecularColor : FragmentColor.rgb, fEnvMapLevel);
    #else
        gl_FragColor = vec4(FragmentColor.rgb, fEnvMapLevel);
    #endif
    """
    checks = 0
    try:
        viewport(0, 0, 256, 10)
        test.active_texture(0x84C0)
        test.bind_texture(0x0DE1, 100)
        test.texture_image(0x0DE1, 0, 0x8814, 256, 10, 0, 0x1908, 0x1406, None)
        texture(7, 256, 10, mask)
        texture(10, 256, 2048, atlas, atlas_texture=True)
        for unit, pixel in ((1, (.5, .5, 1, 1)), (2, (.12, 0, 0, 1)),
                            (3, (.45, 0, 0, 1)), (6, (0, 0, 0, 1))):
            texture(unit, 1, 1, (ct.c_float * 4)(*pixel))
        for name, quality, lighting, gamma in product(
                ("fs_plt_tinter", "fs_plt_tinter_nm", "fs_plt_hair_nm"), range(3), range(2), range(2)):
            config = quality, lighting, gamma, 0, 0
            shader = engine.read(name)
            header = shader.split('#include "inc_standard"')[0] + '#include "inc_standard"\n'
            reference = header + "uniform int inspectSpecular;\nvoid main() {\n" + (
                "FragmentColor = vec4(1.0); SetupStandardShaderInputs();\n" + inspect + "\n}")
            actual = shader.replace('#include "inc_standard"', '#include "inc_standard"\nuniform int inspectSpecular;')
            actual = actual.replace("ApplyStandardShader();", "")
            actual, count = re.subn(r"gl_FragColor\s*=\s*vec4\(FragmentColor\.rgb,\s*outputAlpha\);", inspect, actual)
            if count != 1:
                raise AssertionError(f"Cannot inspect final surface in {name}")
            vertex = "vslit_sm" if name == "fs_plt_tinter" else "vslit_sm_nm"
            programs = [test.program(engine.source(name, True, config, source), engine.source(vertex, False, config))
                        for source in (reference, actual)]
            try:
                for mapped, specular in product((False, True), (False, True)):
                    # All preset colors and shades include the reported dark
                    # brown hair and both female chest304 metal channels.
                    for color in range(176):
                        texture(0, 256, 10, original[color])
                        samples = []
                        for program in programs:
                            active = prepare(program)
                            try:
                                test.integer(test.location(program, b"inspectSpecular"), int(specular))
                                for unit in (2, 3):
                                    test.integer(test.location(program, f"texture{unit}Bound".encode()), int(mapped))
                                for row, base in rows:
                                    test.scalar(test.location(program, row.encode()), (base + color + .5) / 2048)
                                draw(0x0004, 0, 3)
                                test.read(0, 0, 256, 10, 0x1908, 0x1406, output)
                                if test.error():
                                    raise AssertionError("OpenGL error comparing palette lighting")
                                samples.append(tuple(output))
                            finally:
                                for index in active:
                                    disable(index)
                        difference = max(abs(a - b) for a, b in zip(*samples))
                        if difference > 3e-6:
                            index = max(range(len(output)), key=lambda i: abs(samples[0][i] - samples[1][i]))
                            raise AssertionError(f"{name} quality{quality} lighting{lighting} gamma{gamma} "
                                f"mapped{mapped} specular{specular} color{color} layer{index // 1024} "
                                f"shade{index // 4 % 256}: native={samples[0][index]} tint={samples[1][index]}, "
                                f"max error={difference}")
                        checks += 1
            finally:
                for program in programs:
                    test.delete_program(program)
    finally:
        viewport(0, 0, 8, 8)
        test.active_texture(0x84C0)
        test.bind_texture(0x0DE1, 100)
        test.texture_image(0x0DE1, 0, 0x8814, 8, 8, 0, 0x1908, 0x1406, None)
    print(f"Native palette lighting parity passed: {checks} comparisons; all176 colors, all256 shades, "
          "all10 layers, three tint shaders, every quality/lighting/gamma mode, mapped/unmapped "
          "specularity and both diffuse/specular color.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    engine = EngineShaders(args.game_data, root / "sw_shader", args.game_data.parent / "bin/win32/nwmain.exe")
    gl = OpenGL()
    try:
        check_palette_lighting(MaterialTest(gl), engine, root)
    finally:
        gl.close()


if __name__ == "__main__":
    main()
