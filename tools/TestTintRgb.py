"""Verify actual custom RGB through every shipped tint shader on the GPU."""
import argparse
import random
import re
from pathlib import Path
from TestTintShaderMaterials import OpenGL, MaterialTest, ADAPTER, STANDARD, stock_material, near


def check_rgb_materials(test, shader, material, name):
    fragment = "#version 120\n" + shader.replace('#include "inc_standard"', ADAPTER + material + STANDARD)
    program = test.program(fragment)
    checks = 0
    try:
        for bound in (0, 1):
            for layer in range(10):
                metal = layer in (2, 3)
                rgb = (layer, 205, 228, 197)
                expected = (0.98, 1, 0.315) if metal else (0.04, 0, 0.6)
                near(test.sample(program, bound, 0.8, rgb=rgb), expected,
                     f"{name} RGB layer{layer} material bound={bound}")
                near(test.sample(program, bound, 0.8, rgb=rgb, overrides=True), (0.35, 0.7, 0.4),
                     f"{name} RGB layer{layer} authored parameters bound={bound}")
                checks += 2
                if name != "fs_plt_tinter":
                    roughness = 0.45 if name == "fs_plt_tinter_nm" else (
                        0.315 if metal else 0.125 + 0.475 * 0.88**2)
                    near(test.sample(program, bound, 0.8, rgb=rgb, mapped=True), (0.12, 0, roughness),
                         f"{name} RGB layer{layer} authored maps bound={bound}")
                    checks += 1
    finally:
        test.delete_program(program)
    # Force only RGB coverage back to opaque, reproducing the reported loss of
    # metal reflection while leaving normal/specular maps and RGB decoding intact.
    legacy, count = re.subn(r"(paletteColor = vec4\(clamp\(decodedRgb \* shadeScale, 0\.0, 1\.0\),)\s*\w+\);",
                           r"\1 1.0);", fragment)
    assert count == 1
    program = test.program(legacy)
    try:
        for layer in (2, 3):
            near(test.sample(program, 1, 0.8, rgb=(layer, 205, 228, 197)), (0.04, 0, 0.6),
                 f"{name} legacy RGB layer{layer} reflection loss")
    finally:
        test.delete_program(program)
    return checks


def check_authored_diffuse(test, root, material):
    shader = (root / "sw_shader/fs_plt_tinter_nm.shd").read_text()
    shader = shader.replace("gl_FragColor = vec4(FragmentColor.rgb, outputAlpha);", "gl_FragColor = paletteColor;")
    program = test.program("#version 120\n" + shader.replace('#include "inc_standard"', ADAPTER + material + STANDARD))
    checks = 0
    try:
        for diffuse in ((.2, .3, .7, 1), (.05, .07, .1, 1), (.8, .8, .9, 1)):
            # Native palette transport and default row state both retain the
            # authored color; unrelated palette pixels must not change the art.
            near(test.sample(program, 1, 1, authored_diffuse=diffuse), diffuse[:3], "authored diffuse preset")
            scale = sum(a * b for a, b in zip(diffuse, (.2126, .7152, .0722))) / .5
            for color in ((210, 55, 45), (40, 190, 90), (70, 105, 220), (0, 0, 0), (255, 255, 255)):
                near(test.sample(program, 1, 1, rgb=(4, *color), authored_diffuse=diffuse),
                     tuple(min(c / 255 * scale, 1) for c in color), "authored diffuse RGB")
                checks += 1
            # The same material receives a preset again, exercising RGB reset.
            test.scalar(test.location(program, b"rowCloth1"), .343994140625)
            near(test.sample(program, 1, 1, authored_diffuse=diffuse), diffuse[:3], "authored diffuse reset")
            checks += 2
    finally:
        test.delete_program(program)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    rng = random.Random(314159)
    colors = [(205, 228, 197), (0, 0, 0), (255, 255, 255), (127, 255, 255), (128, 0, 0),
              (1, 17, 91), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    colors += [tuple(rng.randrange(256) for _ in range(3)) for _ in range(128)]
    gl = OpenGL()
    checks = 0
    material_checks = 0
    try:
        test = MaterialTest(gl)
        material = stock_material(args.game_data)
        print(f"Authored diffuse RGB/preset/reset GPU checks passed: {check_authored_diffuse(test, root, material)}", flush=True)
        for name in ("fs_plt_tinter", "fs_plt_tinter_nm", "fs_plt_hair_nm"):
            shader = (root / "sw_shader" / (name + ".shd")).read_text()
            material_checks += check_rgb_materials(test, shader, material, name)
            # Inspect the computed albedo before scene lighting; all color/shade decoding
            # remains production code. The separate full harness compiles native includes.
            shader, count = re.subn(r"gl_FragColor\s*=\s*vec4\(FragmentColor.rgb, outputAlpha\);",
                                    "gl_FragColor = paletteColor;", shader)
            assert count == 1
            fragment = "#version 120\n" + shader.replace('#include "inc_standard"', ADAPTER + material + STANDARD)
            program = test.program(fragment)
            try:
                for layer in range(10):
                    for color in colors:
                        near(test.sample(program, 1, 0, rgb=(layer, *color)),
                             tuple(value / 255 for value in color), f"{name} layer{layer} RGB{color}")
                        checks += 1
            finally:
                test.delete_program(program)
            # The previous palette-only renderer must fail the reported green fixture.
            legacy = fragment.replace("if (v >= 1.0)", "if (v >= 4.0)")
            program = test.program(legacy)
            try:
                actual = test.sample(program, 1, 0, rgb=(7, 205, 228, 197))
                assert any(abs(a - b / 255) > .1 for a, b in zip(actual, (205, 228, 197)))
            finally:
                test.delete_program(program)
    finally:
        gl.close()
    print(f"Exact RGB GPU checks passed: {checks}; all10 layers, three shaders, black/white, byte boundaries,"
          " reported pale green and128 deterministic colors. Three legacy negative controls failed as expected.")
    print(f"RGB material GPU checks passed: {material_checks}; both metal layers retain reflection, other layers "
          "stay opaque, and authored maps/parameters survive. Six legacy reflection-loss controls reproduced.")


if __name__ == "__main__":
    main()
