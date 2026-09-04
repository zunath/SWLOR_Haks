#!/usr/bin/env python3
"""Exercise the tint shaders' material setup on Windows OpenGL.

Uses a hidden, temporary WGL context and the installed game's unmodified
inc_material shader. The small inc_standard adapter reproduces its diffuse
binding fallback and exposes the resulting material values as framebuffer
colors. No game process, visible window, or third-party Python module is used.

Run: python tools/TestTintShaderMaterials.py --game-data ".../Neverwinter Nights/data"
"""

from __future__ import annotations

import argparse
import ctypes as ct
from ctypes import wintypes as wt
from pathlib import Path
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
uniform sampler2D texUnit1;
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

    def program(self, fragment: str) -> int:
        shaders = []
        program = self.create_program()
        try:
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
    args = parser.parse_args()
    material = stock_material(args.game_data)
    root = Path(__file__).resolve().parents[1]
    gl = OpenGL()
    checks = 0
    try:
        renderer = gl.function("glGetString", ct.c_char_p, ct.c_uint)(0x1F01).decode()
        test = MaterialTest(gl)
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
    print(f"Tint shader GPU material checks passed: {checks} on {renderer}; all three legacy variants reproduced chrome.")


if __name__ == "__main__":
    main()
