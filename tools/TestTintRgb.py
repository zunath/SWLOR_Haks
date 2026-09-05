"""Verify actual custom RGB through every shipped tint shader on the GPU."""
import argparse
import random
import re
from pathlib import Path
from TestTintShaderMaterials import OpenGL, MaterialTest, ADAPTER, STANDARD, stock_material, near


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
    try:
        test = MaterialTest(gl)
        material = stock_material(args.game_data)
        for name in ("fs_plt_tinter", "fs_plt_tinter_nm", "fs_plt_hair_nm"):
            shader = (root / "sw_shader" / (name + ".shd")).read_text()
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
            legacy = re.sub(r"    // Values below one remain.*?    vec3 vTint",
                            "    vec3 vTint", fragment, flags=re.S)
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


if __name__ == "__main__":
    main()
