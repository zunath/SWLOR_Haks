"""Generate original Shield Bash particle assets, then compile and validate the model.

Run from any directory: python tools/GenerateShieldBashVfx.py
No external artwork or Python packages are required.
"""
from pathlib import Path
import math
import struct
import tempfile
import CompileModels as compiler_tools

ROOT = Path(__file__).resolve().parents[1]
MODEL = "sw_shldbash"


def texture(name, dust):
    # Uncompressed 32-bit BGRA TGA, top-left origin, eight alpha bits.
    size = 128
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            u, v = (x + .5) / size * 2 - 1, (y + .5) / size * 2 - 1
            if dust:
                radius = math.hypot(u, v)
                cloud = .72 + .14 * math.sin(13*u + 4*v) * math.cos(11*v - 3*u)
                cloud += .09 * math.sin(29*u - 17*v)
                alpha = max(0, 1-radius*radius)**2 * cloud
                shade = 220
            else:
                # Narrow, softly feathered sliver. Emitter colors cool as it fades.
                alpha = math.exp(-u*u*95 - v*v*5) * max(0, 1-v*v)
                # Black RGB edges also remain invisible with additive blending.
                shade = round(255 * alpha)
            pixels.extend((shade, shade, shade, round(255 * max(0, min(1, alpha)))))
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, size, size, 32, 0x28)
    (ROOT / "sw_vfx" / f"{name}.tga").write_bytes(header + pixels)


def emitter(name, texture_name, dust):
    props = {
        "parent": MODEL, "position": "0 0 0", "orientation": "0 0 1 0",
        "update": "Explosion", "render": "Normal", "blend": "Normal" if dust else "Lighten",
        "spawntype": 0, "xsize": 8 if dust else 2, "ysize": 8 if dust else 2,
        "inherit": 0, "inherit_local": 0, "inheritvel": 0, "inherit_part": 0,
        "renderorder": 0, "threshold": 0, "combinetime": 0, "deadspace": 0,
        "colorStart": ".52 .43 .32" if dust else "1 .86 .55",
        "colorEnd": ".36 .32 .28" if dust else ".85 .35 .08",
        "alphaStart": .26 if dust else 1, "alphaEnd": 0,
        "sizeStart": .12 if dust else .045, "sizeEnd": .32 if dust else .008,
        "sizeStart_y": 0, "sizeEnd_y": 0,
        "birthrate": 8 if dust else 24, "lifeExp": .55 if dust else .4, "mass": 0 if dust else 1.2,
        "spread": 6.283185, "particleRot": .5 if dust else 5,
        "velocity": .45 if dust else 2.4, "randvel": .2 if dust else 1.2,
        "bounce_co": 0, "blurlength": 0, "loop": 0, "bounce": 0,
        "m_isTinted": 0, "splat": 0, "affectedByWind": "false",
        "texture": texture_name, "twosidedtex": 1, "xgrid": 1, "ygrid": 1,
        "fps": 1, "frameStart": 0, "frameEnd": 0, "random": 0,
        "lightningDelay": 0, "lightningRadius": 0, "lightningSubDiv": 0,
        "lightningScale": 0, "blastRadius": 0, "blastLength": 0, "p2p": 0,
    }
    return f"node emitter {name}\n" + "".join(f"  {k} {v}\n" for k,v in props.items()) + "endnode\n"


def main():
    texture("sw_bash_spark", False)
    texture("sw_bash_dust", True)
    text = f"# Original SWLOR Shield Bash impact: metallic sparks and dust\nnewmodel {MODEL}\nsetsupermodel {MODEL} null\nclassification EFFECT\nsetanimationscale 1\nbeginmodelgeom {MODEL}\nnode dummy {MODEL}\n parent NULL\nendnode\n"
    text += emitter("sparks", "sw_bash_spark", False)
    text += emitter("dust", "sw_bash_dust", True)
    text += f"endmodelgeom {MODEL}\nnewanim impact {MODEL}\n length .9\n transtime 0\n animroot {MODEL}\n event .1 detonate\nnode dummy {MODEL}\n parent NULL\nendnode\n"
    # Explosion emitters respond to a discrete animation event, rather than
    # depending on frame-rate integration over a very short fountain window.
    for name in ("sparks", "dust"):
        text += f"node emitter {name}\n parent {MODEL}\nendnode\n"
    text += f"doneanim impact {MODEL}\ndonemodel {MODEL}\n"
    source = ROOT / "model_sources" / "vfx" / f"{MODEL}.mdl.ascii"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="ascii")
    # This standalone effect has no supermodel dependencies. Stage just this
    # model; do not scan or rebuild the entire installed animation library.
    with tempfile.TemporaryDirectory(prefix="shield-bash-") as folder:
        staging = Path(folder)
        for child in ("input", "binary", "decompiled"):
            (staging / child).mkdir()
        (staging / "input" / f"{MODEL}.mdl").write_text(text, encoding="ascii")
        compiler, _ = compiler_tools.prepare_compiler(staging)
        compiler_tools.run_compiler(compiler, staging, ["-cne", str(staging / "input" / "*.mdl"), str(staging / "binary") + "/"], "compile.log")
        compiler_tools.run_compiler(compiler, staging, ["-de", str(staging / "binary" / "*.mdl"), str(staging / "decompiled") + "/"], "decompile.log")
        compiled = (staging / "binary" / f"{MODEL}.mdl").read_bytes()
        decoded = (staging / "decompiled" / f"{MODEL}.mdl").read_text(encoding="latin1")
        compiler_tools.validate_round_trip(text.encode("ascii"), compiled, decoded)
        events = [line.split() for line in decoded.splitlines() if line.strip().startswith("event ")]
        if len(events) != 1 or events[0][2] != "detonate" or abs(float(events[0][1]) - .1) > .00001:
            raise ValueError("Compiler did not preserve the single particle burst event")
        # The general model audit focuses on geometry. Explicitly protect the
        # particle settings too, especially emission cutoff and transparency.
        before = compiler_tools.parse_nodes(text)
        after = compiler_tools.parse_nodes(decoded)
        for (kind, name, props), (_, _, output) in zip(before, after):
            if kind != "emitter":
                continue
            for key in ("texture", "update", "render", "blend", "birthrate", "birthratekey",
                        "lifeexp", "alphastart", "alphaend", "sizestart", "sizeend",
                        "colorstart", "colorend", "velocity", "randvel", "spread", "loop"):
                if key in props and not compiler_tools.equivalent(props[key], output.get(key, [])):
                    raise ValueError(f"{name}: compiler changed particle setting {key}")
        (ROOT / "sw_vfx" / f"{MODEL}.mdl").write_bytes(compiled)
        print(f"Compiled and round-trip validated {MODEL}: {len(compiled)} bytes")


if __name__ == "__main__":
    main()
