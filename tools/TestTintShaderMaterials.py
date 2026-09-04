#!/usr/bin/env python3
"""Compile production tint shader pairs and exercise material setup on Windows.

First compiles and links the actual VS/FS pairs used by generated MTRs, with
the complete installed engine includes and GLSL preamble from nwmain.exe.
There are no replacement uniforms, samplers, or functions in this check.
All quality modes and lighting/gamma/keyhole/discard configurations are tested.

The SEPARATE numeric regression uses an inc_standard adapter to expose cached
material values. It is not a substitute for compiling the production shaders.
Both checks use a hidden, temporary WGL context; no game process, visible
window, or third-party Python module is used.

Run: python tools/TestTintShaderMaterials.py --game-data ".../Neverwinter Nights/data"
"""

from __future__ import annotations

import argparse
import ctypes as ct
from ctypes import wintypes as wt
from itertools import product
from pathlib import Path
import re
import struct


class PixelFormat(ct.Structure):
    _fields_ = [("size", wt.WORD), ("version", wt.WORD), ("flags", wt.DWORD)] + [
        (name, ct.c_ubyte) for name in (
            "pixel_type", "color_bits", "red_bits", "red_shift", "green_bits",
            "green_shift", "blue_bits", "blue_shift", "alpha_bits", "alpha_shift",
            "accum_bits", "accum_red_bits", "accum_green_bits", "accum_blue_bits",
            "accum_alpha_bits", "depth_bits", "stencil_bits", "aux_buffers",
            "layer_type", "reserved",
        )
    ] + [(name, wt.DWORD) for name in ("layer_mask", "visible_mask", "damage_mask")]


class OpenGL:
    def __init__(self):
        self.user = ct.WinDLL("user32")
        self.gdi = ct.WinDLL("gdi32")
        self.gl = ct.WinDLL("opengl32")
        self.user.CreateWindowExW.restype = wt.HWND
        self.user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ct.c_void_p]
        self.user.GetDC.restype = wt.HDC
        self.user.GetDC.argtypes = [wt.HWND]
        self.user.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        self.user.DestroyWindow.argtypes = [wt.HWND]
        self.gdi.ChoosePixelFormat.argtypes = [wt.HDC, ct.POINTER(PixelFormat)]
        self.gdi.SetPixelFormat.argtypes = [wt.HDC, ct.c_int, ct.POINTER(PixelFormat)]
        self.gl.wglCreateContext.restype = wt.HANDLE
        self.gl.wglCreateContext.argtypes = [wt.HDC]
        self.gl.wglMakeCurrent.argtypes = [wt.HDC, wt.HANDLE]
        self.gl.wglDeleteContext.argtypes = [wt.HANDLE]
        self.gl.wglGetProcAddress.restype = ct.c_void_p
        self.gl.wglGetProcAddress.argtypes = [ct.c_char_p]
        self.window = self.user.CreateWindowExW(0, "STATIC", "Tint material test", 0,
            0, 0, 8, 8, None, None, None, None)
        if not self.window:
            raise RuntimeError("Cannot create hidden OpenGL test window")
        self.dc = self.user.GetDC(self.window)
        self.context = None
        try:
            pixel = PixelFormat()
            pixel.size, pixel.version = ct.sizeof(pixel), 1
            pixel.flags, pixel.color_bits, pixel.alpha_bits = 4 | 32, 24, 8
            selected = self.gdi.ChoosePixelFormat(self.dc, ct.byref(pixel))
            if not selected or not self.gdi.SetPixelFormat(self.dc, selected, ct.byref(pixel)):
                raise RuntimeError("Cannot select OpenGL pixel format")
            self.context = self.gl.wglCreateContext(self.dc)
            if not self.context or not self.gl.wglMakeCurrent(self.dc, self.context):
                raise RuntimeError("Cannot activate OpenGL context")
        except Exception:
            self.close()
            raise

    def function(self, name, result, *arguments):
        address = self.gl.wglGetProcAddress(name.encode())
        if address not in (None, 0, 1, 2, 3, ct.c_void_p(-1).value):
            return ct.WINFUNCTYPE(result, *arguments)(address)
        function = getattr(self.gl, name)
        function.restype, function.argtypes = result, arguments
        return function

    def close(self):
        self.gl.wglMakeCurrent(None, None)
        if self.context:
            self.gl.wglDeleteContext(self.context)
        self.user.ReleaseDC(self.window, self.dc)
        self.user.DestroyWindow(self.window)


def stock_material(game_data: Path) -> str:
    data = (game_data / "base_shaders.bif").read_bytes()
    if data[:8] != b"BIFFV1  ":
        raise ValueError("Unsupported base_shaders.bif format")
    count, _, table = struct.unpack_from("<III", data, 8)
    for index in range(count):
        _, start, size, _ = struct.unpack_from("<IIII", data, table + index * 16)
        source = data[start:start + size].decode("utf-8")
        if "void SetupSpecularity(vec3 Albedo)" in source:
            return source.replace('#include "inc_common"', "")
    raise ValueError("Installed base_shaders.bif has no SetupSpecularity shader")


class EngineShaders:
    """Resolve exact shader resrefs through the installed KEY/BIF index."""

    def __init__(self, game_data: Path, shader_root: Path, client: Path):
        self.game_data = game_data
        self.shader_root = shader_root
        self.resources = {}
        self.archives = {}
        key = (game_data / "nwn_base.key").read_bytes()
        if key[:8] != b"KEY V1  ":
            raise ValueError("Unsupported nwn_base.key format")
        _, count, bif_table, resource_table = struct.unpack_from("<IIII", key, 8)
        for index in range(count):
            name, kind, resource_id = struct.unpack_from("<16sHI", key, resource_table + index * 22)
            if kind != 2069:
                continue
            _, name_start, name_size, _ = struct.unpack_from("<IIHH", key, bif_table + (resource_id >> 20) * 12)
            filename = key[name_start:name_start + name_size].rstrip(b"\0").decode().replace("\\", "/")
            self.resources[name.rstrip(b"\0").decode().lower()] = (
                game_data.parent / filename, resource_id & 0xFFFFF)
        strings = client.read_bytes().split(b"\0")
        templates = [value.decode("ascii") for value in strings
            if value.startswith(b"#version 330 core\n") and b"SHADER_QUALITY_MODE" in value]
        outputs = [value.decode("ascii") for value in strings
            if value.startswith(b"#define gl_FragColor ") and b"out vec4" in value]
        if len(templates) != 1 or len(outputs) != 1:
            raise ValueError("Cannot identify the installed client's GLSL preamble/output declaration")
        self.preamble_template = templates[0]
        self.fragment_output = outputs[0]

    def read(self, name: str) -> str:
        local = self.shader_root / f"{name}.shd"
        if local.exists():
            return local.read_text(encoding="utf-8")
        if name not in self.resources:
            raise ValueError(f"Unresolved engine shader include: {name}")
        path, index = self.resources[name]
        if path not in self.archives:
            self.archives[path] = path.read_bytes()
        archive = self.archives[path]
        if archive[:8] != b"BIFFV1  ":
            raise ValueError(f"Unsupported BIF format: {path}")
        count, _, table = struct.unpack_from("<III", archive, 8)
        if index >= count:
            raise ValueError(f"Invalid KEY shader index {index} for {path}")
        _, start, size, kind = struct.unpack_from("<IIII", archive, table + index * 16)
        if kind != 2069:
            raise ValueError(f"KEY/BIF shader type mismatch: {name}")
        return archive[start:start + size].decode("utf-8")

    def expand(self, name: str, override: str | None = None, stack=()) -> str:
        if name in stack:
            raise ValueError(f"Circular shader include: {' -> '.join((*stack, name))}")
        source = self.read(name) if override is None else override
        return re.sub(r'^\s*#include\s+"([\w]+)(?:\.shd)?"\s*$',
            lambda match: self.expand(match[1].lower(), stack=(*stack, name)),
            source, flags=re.MULTILINE)

    def source(self, name: str, fragment: bool, configuration, override=None) -> str:
        quality, lighting, gamma, keyhole, discard = configuration
        # The scalar controls and compatibility declarations follow the exact
        # format embedded in this installation's client. Lights/bones are fixed
        # array capacities, not shader feature substitutes.
        preamble = self.preamble_template % (
            32, 128, gamma, lighting, quality, keyhole, 0, "0", "0", discard, 0,
            "in" if fragment else "out", self.fragment_output if fragment else "")
        return preamble + self.expand(name, override)


def production_pairs(material_root: Path):
    pairs = set()
    count = 0
    for path in material_root.glob("*.mtr"):
        source = path.read_text(encoding="utf-8")
        vertex = re.search(r"^customshaderVS\s+(\S+)", source, re.MULTILINE | re.IGNORECASE)
        fragment = re.search(r"^customshaderFS\s+(\S+)", source, re.MULTILINE | re.IGNORECASE)
        if not vertex or not fragment:
            raise ValueError(f"Generated tint material has no explicit shader pair: {path}")
        pairs.add((vertex[1].lower(), fragment[1].lower()))
        count += 1
    if not pairs:
        raise ValueError(f"No generated material shader pairs in {material_root}")
    return sorted(pairs), count


def compile_engine_pairs(test, engine, pairs):
    checks = 0
    for quality, lighting, gamma, keyhole, discard in product(range(3), range(2), range(2), range(2), range(2)):
        configuration = quality, lighting, gamma, keyhole, discard
        for vertex, fragment in pairs:
            try:
                program = test.program(engine.source(fragment, True, configuration),
                    engine.source(vertex, False, configuration))
            except AssertionError as error:
                raise AssertionError(f"Production {vertex}/{fragment}, quality={quality}, "
                    f"fragment-lighting={lighting}, gamma={gamma}, keyhole={keyhole}, "
                    f"no-discard={discard}:\n{error}") from error
            test.delete_program(program)
            checks += 1
    print(f"Production engine compile/link passed: {checks} pairs across Minimal/Performance/High Quality "
        "and both fragment-lighting, gamma, keyhole, and no-discard settings.", flush=True)
    # Removing the base shader's explicit cutout sampler must fail using the
    # real engine includes, which only declare texUnit1 for NORMAL_MAP == 1.
    # This is the compile failure the old numeric adapter masked.
    base = engine.read("fs_plt_tinter")
    broken, removed = re.subn(r"\buniform\s+sampler2D\s+texUnit1\s*;", "", base)
    if removed != 1:
        raise AssertionError("Expected one explicit texUnit1 declaration for the base shader negative control")
    for quality in range(3):
        configuration = quality, 0, 0, 1, 0
        try:
            program = test.program(engine.source("fs_plt_tinter", True, configuration, broken),
                engine.source("vslit_sm", False, configuration))
        except AssertionError as error:
            message = str(error)
            if "texUnit1" not in message or not any(word in message for word in ("undefined variable", "undeclared")):
                raise AssertionError(f"Unexpected negative-control compile error: {error}") from error
        else:
            test.delete_program(program)
            raise AssertionError(f"Missing texUnit1 was not rejected for quality {quality}")
    print("Production engine negative controls passed: missing texUnit1 fails all three quality modes.", flush=True)
    return checks


ADAPTER = """
#define lowp
#define highp
#define FRAGMENT_LIGHTING 1
#define FRAGMENT_NORMAL NORMAL_MAP
#define SPECULAR_LIGHT 1
#define SPECULAR_GEOMETRIC_SHADOWING 2
#define SPECULAR_DISTRIBUTION_MODEL 1
#define GAMMA_CORRECTION 0
const vec4 COLOR_WHITE = vec4(1.0);
const vec4 COLOR_BLACK = vec4(0.0);
uniform sampler2D texUnit0;
#if NORMAL_MAP == 1
uniform sampler2D texUnit1;
#endif
uniform sampler2D texUnit2;
uniform sampler2D texUnit3;
uniform int texture0Bound;
uniform int texture2Bound;
uniform int texture3Bound;
uniform vec4 materialFrontDiffuse;
uniform int inspectSpecularColor;
varying vec2 vVertexTexCoords;
vec2 vTexCoords;
vec3 vFragmentNormal = vec3(0.0, 0.0, 1.0);
vec3 vSurfaceNormal = vec3(0.0, 0.0, 1.0);
vec4 FragmentColor;
float fEnvMapLevel;
float sqr(float value) { return value * value; }
vec3 ApplyColorSpace(vec3 value) { return value; }
float ApplyColorSpace(float value) { return value; }
"""

STANDARD = """
void SetupStandardShaderInputs() {
    vTexCoords = vVertexTexCoords;
    vec4 mainTexture = texture2D(texUnit0, vTexCoords);
    // inc_standard's documented texture0Bound fallback.
    if (texture0Bound == 0) {
        mainTexture.rgb = vec3(0.666667);
        fEnvMapLevel = 1.0;
    } else {
        fEnvMapLevel = 1.0 - mainTexture.a;
        mainTexture.rgb = mix(mainTexture.rgb, vec3(0.666667), fEnvMapLevel);
    }
    FragmentColor.rgb *= mainTexture.rgb;
    SetupSpecularity(FragmentColor.rgb * materialFrontDiffuse.rgb);
}
void ApplyStandardShader() {
    // Make the actual derived material state directly observable.
    FragmentColor = vec4(inspectSpecularColor != 0
        ? SpecularColor : vec3(fSpecularity, fMetallicness, fRoughness), 1.0);
}
"""


class MaterialTest:
    def __init__(self, gl: OpenGL):
        self.gl = gl
        self.create_shader = gl.function("glCreateShader", ct.c_uint, ct.c_uint)
        self.source = gl.function("glShaderSource", None, ct.c_uint, ct.c_int, ct.POINTER(ct.c_char_p), ct.c_void_p)
        self.compile = gl.function("glCompileShader", None, ct.c_uint)
        self.shader_value = gl.function("glGetShaderiv", None, ct.c_uint, ct.c_uint, ct.POINTER(ct.c_int))
        self.shader_log = gl.function("glGetShaderInfoLog", None, ct.c_uint, ct.c_int, ct.c_void_p, ct.c_void_p)
        self.create_program = gl.function("glCreateProgram", ct.c_uint)
        self.attach = gl.function("glAttachShader", None, ct.c_uint, ct.c_uint)
        self.link = gl.function("glLinkProgram", None, ct.c_uint)
        self.program_value = gl.function("glGetProgramiv", None, ct.c_uint, ct.c_uint, ct.POINTER(ct.c_int))
        self.program_log = gl.function("glGetProgramInfoLog", None, ct.c_uint, ct.c_int, ct.c_void_p, ct.c_void_p)
        self.use = gl.function("glUseProgram", None, ct.c_uint)
        self.delete_shader = gl.function("glDeleteShader", None, ct.c_uint)
        self.delete_program = gl.function("glDeleteProgram", None, ct.c_uint)
        self.location = gl.function("glGetUniformLocation", ct.c_int, ct.c_uint, ct.c_char_p)
        self.integer = gl.function("glUniform1i", None, ct.c_int, ct.c_int)
        self.scalar = gl.function("glUniform1f", None, ct.c_int, ct.c_float)
        self.vector = gl.function("glUniform4f", None, ct.c_int, ct.c_float, ct.c_float, ct.c_float, ct.c_float)
        self.active_texture = gl.function("glActiveTexture", None, ct.c_uint)
        self.bind_texture = gl.function("glBindTexture", None, ct.c_uint, ct.c_uint)
        self.texture_parameter = gl.function("glTexParameteri", None, ct.c_uint, ct.c_uint, ct.c_int)
        self.texture_image = gl.function("glTexImage2D", None, ct.c_uint, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_uint, ct.c_uint, ct.c_void_p)
        self.begin = gl.function("glBegin", None, ct.c_uint)
        self.vertex = gl.function("glVertex2f", None, ct.c_float, ct.c_float)
        self.end = gl.function("glEnd", None)
        self.read = gl.function("glReadPixels", None, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_uint, ct.c_uint, ct.c_void_p)
        # Window framebuffers apply pixel ownership tests even while hidden.
        # Render into a floating-point texture so no window needs to be shown.
        self.bind_texture(0x0DE1, 100)
        self.texture_image(0x0DE1, 0, 0x8814, 8, 8, 0, 0x1908, 0x1406, None)
        framebuffer = ct.c_uint()
        gl.function("glGenFramebuffers", None, ct.c_int, ct.POINTER(ct.c_uint))(1, ct.byref(framebuffer))
        gl.function("glBindFramebuffer", None, ct.c_uint, ct.c_uint)(0x8D40, framebuffer.value)
        gl.function("glFramebufferTexture2D", None, ct.c_uint, ct.c_uint, ct.c_uint, ct.c_uint, ct.c_int)(0x8D40, 0x8CE0, 0x0DE1, 100, 0)
        status = gl.function("glCheckFramebufferStatus", ct.c_uint, ct.c_uint)(0x8D40)
        if status != 0x8CD5:
            raise RuntimeError(f"Incomplete OpenGL test framebuffer: {status:#x}")
        gl.function("glViewport", None, ct.c_int, ct.c_int, ct.c_int, ct.c_int)(0, 0, 8, 8)

    def program(self, fragment: str, vertex: str | None = None) -> int:
        shaders = []
        program = self.create_program()
        try:
            if vertex is None:
                vertex = "#version 120\nvarying vec2 vVertexTexCoords; void main() { gl_Position = gl_Vertex; vVertexTexCoords = vec2(0.5); }"
            for kind, source in ((0x8B31, vertex), (0x8B30, fragment)):
                shader = self.create_shader(kind)
                shaders.append(shader)
                pointer = ct.c_char_p(source.encode())
                self.source(shader, 1, ct.byref(pointer), None)
                self.compile(shader)
                success = ct.c_int()
                self.shader_value(shader, 0x8B81, ct.byref(success))
                if not success.value:
                    log = ct.create_string_buffer(16384)
                    self.shader_log(shader, len(log), None, log)
                    raise AssertionError(log.value.decode())
                self.attach(program, shader)
            self.link(program)
            success = ct.c_int()
            self.program_value(program, 0x8B82, ct.byref(success))
            if not success.value:
                log = ct.create_string_buffer(16384)
                self.program_log(program, len(log), None, log)
                raise AssertionError(log.value.decode())
            return program
        except Exception:
            self.delete_program(program)
            raise
        finally:
            for shader in shaders:
                self.delete_shader(shader)

    def sample(self, program: int, bound: int, alpha: float, *, mapped=False, overrides=False, color=False):
        self.use(program)
        for name, value in {"texture0Bound": bound, "texture2Bound": int(mapped),
                "texture3Bound": int(mapped), "inspectSpecularColor": int(color)}.items():
            self.integer(self.location(program, name.encode()), value)
        for name, value in {"tintMapWidth": 1, "tintMapHeight": 1,
                "Specularity": 0.35 if overrides else 0,
                "Roughness": 0.4 if overrides else 0,
                "Metallicness": 0.7 if overrides else 0}.items():
            self.scalar(self.location(program, name.encode()), value)
        self.vector(self.location(program, b"materialFrontDiffuse"), 1, 1, 1, 1)
        for unit, value in {0: (1, 1, 1, 1), 1: (0.5, 0.5, 1, 1),
                2: (0.12, 0, 0, 1), 3: (0.45, 0, 0, 1),
                7: (0.5, 0.05, 0, 1), 9: (1, 1, 1, 1),
                10: (0.2, 0.4, 0.6, alpha)}.items():
            self.active_texture(0x84C0 + unit)
            self.bind_texture(0x0DE1, unit + 1)
            for parameter in (0x2800, 0x2801):
                self.texture_parameter(0x0DE1, parameter, 0x2600)
            pixels = (ct.c_float * 4)(*value)
            self.texture_image(0x0DE1, 0, 0x8814, 1, 1, 0, 0x1908, 0x1406, pixels)
            self.integer(self.location(program, f"texUnit{unit}".encode()), unit)
        self.begin(0x0007)
        for point in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            self.vertex(*point)
        self.end()
        values = (ct.c_float * 4)()
        self.read(4, 4, 1, 1, 0x1908, 0x1406, values)
        return tuple(values[:3])


def near(actual, expected, label):
    if any(abs(left - right) > 0.006 for left, right in zip(actual, expected)):
        raise AssertionError(f"{label}: got {actual}, expected {expected}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--client-exe", type=Path, help="defaults to the installed bin/win32/nwmain.exe")
    args = parser.parse_args()
    material = stock_material(args.game_data)
    root = Path(__file__).resolve().parents[1]
    client = args.client_exe or args.game_data.parent / "bin" / "win32" / "nwmain.exe"
    engine = EngineShaders(args.game_data, root / "sw_shader", client)
    pairs, materials = production_pairs(root / "sw_tint_mtr")
    gl = OpenGL()
    checks = 0
    try:
        renderer = gl.function("glGetString", ct.c_char_p, ct.c_uint)(0x1F01).decode()
        test = MaterialTest(gl)
        print(f"Validating {len(pairs)} production shader pairs from {materials} generated MTRs on {renderer}.", flush=True)
        compile_engine_pairs(test, engine, pairs)
        for name in ("fs_plt_tinter", "fs_plt_tinter_nm", "fs_plt_hair_nm"):
            shader = (root / "sw_shader" / f"{name}.shd").read_text()
            fragment = "#version 120\n" + shader.replace('#include "inc_standard"', ADAPTER + material + STANDARD)
            program = test.program(fragment)
            try:
                for bound in (0, 1):
                    near(test.sample(program, bound, 1), (0.04, 0, 0.6), f"{name} opaque bound={bound}")
                    near(test.sample(program, bound, 0.8), (0.98, 1, 0.315), f"{name} metal bound={bound}")
                    near(test.sample(program, bound, 1, color=True), (0.2, 0.4, 0.6), f"{name} diffuse bound={bound}")
                    near(test.sample(program, bound, 1, overrides=True), (0.35, 0.7, 0.4), f"{name} authored parameters bound={bound}")
                    checks += 4
                    if name != "fs_plt_tinter":
                        roughness = 0.45 if name == "fs_plt_tinter_nm" else 0.125 + 0.475 * 0.88**2
                        near(test.sample(program, bound, 1, mapped=True), (0.12, 0, roughness), f"{name} authored maps bound={bound}")
                        checks += 1
            finally:
                test.delete_program(program)
            # Negative control: removing only the repair reproduces the chrome
            # failure on the same GPU, shader and installed material function.
            # Remove the production call, leaving the engine's setup intact.
            last = fragment.rfind("SetupSpecularity(FragmentColor.rgb * materialFrontDiffuse.rgb);")
            legacy = fragment[:last] + fragment[last:].replace("SetupSpecularity(FragmentColor.rgb * materialFrontDiffuse.rgb);", "", 1)
            program = test.program(legacy)
            try:
                near(test.sample(program, 0, 1), (0.98, 1, 0.125), f"{name} legacy chrome reproduction")
                checks += 1
            finally:
                test.delete_program(program)
    finally:
        gl.close()
    print(f"Separate material-state adapter checks passed: {checks}; all three legacy variants reproduced chrome.")


if __name__ == "__main__":
    main()
