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
import math
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


def material_parameters(source: str):
    parameters = []
    for line in source.splitlines():
        tokens = line.split()
        if not tokens or tokens[0].lower() != "parameter":
            continue
        if len(tokens) < 4 or tokens[1].lower() not in ("float", "int"):
            raise AssertionError(f"Invalid material parameter: {line}")
        kind, name = tokens[1].lower(), tokens[2]
        convert = float if kind == "float" else int
        parameters.append((kind, name, tuple(convert(value) for value in tokens[3:])))
    return parameters


def production_pairs(material_root: Path):
    pairs = set()
    parameters = {}
    count = 0
    for path in material_root.glob("*.mtr"):
        source = path.read_text(encoding="utf-8")
        vertex = re.search(r"^customshaderVS\s+(\S+)", source, re.MULTILINE | re.IGNORECASE)
        fragment = re.search(r"^customshaderFS\s+(\S+)", source, re.MULTILINE | re.IGNORECASE)
        if not vertex or not fragment:
            raise ValueError(f"Generated tint material has no explicit shader pair: {path}")
        pair = vertex[1].lower(), fragment[1].lower()
        pairs.add(pair)
        parameters.setdefault(pair, set()).update(material_parameters(source))
        count += 1
    if not pairs:
        raise ValueError(f"No generated material shader pairs in {material_root}")
    return sorted(pairs), count, parameters


def linked_material_parameters(test, program, parameters):
    count, maximum = ct.c_int(), ct.c_int()
    test.program_value(program, 0x8B86, ct.byref(count))  # GL_ACTIVE_UNIFORMS
    test.program_value(program, 0x8B87, ct.byref(maximum))
    uniforms = {}
    for index in range(count.value):
        name = ct.create_string_buffer(maximum.value)
        size, kind = ct.c_int(), ct.c_uint()
        test.active_uniform(program, index, len(name), None, ct.byref(size), ct.byref(kind), name)
        uniforms[name.value.decode()] = kind.value, size.value
    for kind, name, components in sorted({(kind, name, len(values)) for kind, name, values in parameters}):
        if name not in uniforms or test.location(program, name.encode()) < 0:
            raise AssertionError(f"MTR parameter does not resolve to an active shader uniform: {name}")
        # The installed client dispatches one float to glUniform1f and four to
        # glUniform4fv; scalar GLSL uniforms reject the latter with 0x502.
        expected = {("float", 1): 0x1406, ("float", 4): 0x8B52, ("int", 1): 0x1404}.get((kind, components))
        actual, array_size = uniforms[name]
        if expected is None or actual != expected or array_size != 1:
            raise AssertionError(f"MTR parameter {name}: {kind} with {components} values does not match "
                f"GLSL type {actual:#x}, array size {array_size}")


def upload_material_parameters(test, program, parameters, native_overrides=None, check_errors=True):
    """Use MTR component counts, including the client's native Vec4 text path."""
    test.use(program)
    native_overrides = native_overrides or {}
    for kind, name, defaults in parameters:
        values = defaults
        if name.lower() in native_overrides:
            native = native_overrides[name.lower()]
            # Native float overrides arrive as four %f tokens. A scalar MTR
            # explicitly keeps only the first token before reparsing its value.
            values = tuple(float(f"{value:.6f}") for value in native[:len(defaults)])
        location = test.location(program, name.encode())
        if kind == "int":
            test.integer(location, int(values[0]))
        elif len(values) == 1:
            test.scalar(location, values[0])
        else:
            test.vector_array(location, 1, (ct.c_float * 4)(*values))
        if check_errors:
            error = test.error()
            if error:
                raise AssertionError(f"MTR upload {name}, {len(values)} components: OpenGL error {error:#x}")


def native_npc_rows(npc):
    colors = {
        "female": (("rowSkin", 0, 2), ("rowHair", 176, 31), ("rowCloth1", 704, 174),
            ("rowCloth2", 704, 3), ("rowLeath1", 880, 3), ("rowLeath2", 880, 174),
            ("rowMetal1", 352, 0), ("rowMetal2", 528, 8), ("rowTat1", 1056, 139), ("rowTat2", 1056, 2)),
        "rodian": (("rowSkin", 0, 80), ("rowHair", 176, 20), ("rowCloth1", 704, 97),
            ("rowCloth2", 704, 98), ("rowLeath1", 880, 99), ("rowLeath2", 880, 23)),
    }
    return {name.lower(): ((base + color + 0.5) / 2048, 0, 0, 0) for name, base, color in colors[npc]}


def check_native_npc_rows(test, engine, pairs, parameters):
    checks, negatives = 0, 0
    for quality in range(3):
        for vertex, fragment in pairs:
            program = test.program(engine.source(fragment, True, (quality, 0, 0, 1, 0)),
                engine.source(vertex, False, (quality, 0, 0, 1, 0)))
            try:
                rows = sorted(parameter for parameter in parameters[vertex, fragment] if parameter[1].startswith("row"))
                legacy = [(kind, name, (values[0], 0, 0, 0)) for kind, name, values in rows]
                try:
                    linked_material_parameters(test, program, legacy)
                except AssertionError:
                    pass
                else:
                    raise AssertionError("Four-component MTR/scalar GLSL negative control was accepted")
                for npc in ("female", "rodian"):
                    native = native_npc_rows(npc)
                    upload_material_parameters(test, program, rows)
                    upload_material_parameters(test, program, legacy, native, check_errors=False)
                    if test.error() != 0x502:
                        raise AssertionError("Legacy MTR upload did not reproduce GL_INVALID_OPERATION")
                    for _, name, defaults in rows:
                        value = ct.c_float()
                        test.get_uniform(program, test.location(program, name.encode()), ct.byref(value))
                        if abs(value.value - defaults[0]) > 1e-7:
                            raise AssertionError(f"Legacy MTR upload changed initializer for {name}")
                    negatives += 1
                    upload_material_parameters(test, program, rows, native)
                    for _, name, _ in rows:
                        if name.lower() not in native:
                            continue
                        value = ct.c_float()
                        test.get_uniform(program, test.location(program, name.encode()), ct.byref(value))
                        expected = float(f"{native[name.lower()][0]:.6f}")
                        if abs(value.value - expected) > 1e-7:
                            raise AssertionError(f"NPC {npc} {fragment}/{quality} {name}: GPU received "
                                f"{value.value}, expected {expected}")
                        checks += 1
            finally:
                test.delete_program(program)
    print(f"NWN material transport passed: {checks} actual NPC row uploads, {negatives} legacy vector-upload "
        "negative controls; all production shader variants and quality modes.", flush=True)


def compile_engine_pairs(test, engine, pairs, parameters):
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
            try:
                linked_material_parameters(test, program, parameters[vertex, fragment])
                upload_material_parameters(test, program, parameters[vertex, fragment])
            finally:
                test.delete_program(program)
            checks += 1
    print(f"Production engine compile/link passed: {checks} pairs across Minimal/Performance/High Quality "
        "and both fragment-lighting, gamma, keyhole, and no-discard settings; all generated MTR parameter "
        "names, GLSL types, component counts and actual uploads validated.", flush=True)
    program = test.program(engine.source("fs_plt_tinter", True, (1, 0, 0, 1, 0)),
        engine.source("vslit_sm", False, (1, 0, 0, 1, 0)))
    try:
        try:
            linked_material_parameters(test, program, [("float", name, (0,)) for name in ("tintSkin", "tintSkinR", "useCustomSkin")])
        except AssertionError:
            pass
        else:
            raise AssertionError("Obsolete material parameter negative control unexpectedly resolved")
    finally:
        test.delete_program(program)
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


def tint_dds_layout(header: bytes, size: int, txi: str):
    """Reject the ambiguous base-only DDS inputs that NWN uploaded as mip chains.

    The crash dump showed glCompressedTexImage2D reading mip level 1 beyond the
    allocation. Packed tint maps intentionally contain only level 0: require
    BOTH an explicit count and the texture policy that disables mip uploading.
    A valid generic DDS header alone does not establish NWN loader safety.
    """
    if len(header) != 128 or header[:4] != b"DDS " or header[84:88] != b"ATI2":
        raise AssertionError("Tint texture must have a complete ATI2 DDS header")
    flags, height, width, linear_size, _, count = struct.unpack_from("<6I", header, 8)
    if not flags & 0x20000 or count != 1:
        raise AssertionError("Base-only tint DDS requires DDSD_MIPMAPCOUNT and explicit mipmap count 1")
    if width <= 0 or height <= 0:
        raise AssertionError("Tint DDS dimensions must be positive")
    payload_size = ((width + 3) // 4) * ((height + 3) // 4) * 16
    if linear_size != payload_size or size != 128 + payload_size:
        raise AssertionError("Tint DDS declared base-level dimensions must exactly cover its BC5 payload")
    directives = re.findall(r"^\s*mipmap\s+(\d+)\s*$", txi, re.MULTILINE | re.IGNORECASE)
    if directives != ["0"]:
        raise AssertionError("Base-only tint DDS requires a sibling TXI with one mipmap 0 directive")
    return width, height, payload_size


def audit_tint_dds(root: Path):
    paths = sorted(path for directory in ("sw_tint0", "sw_tint1", "sw_tint2")
        for path in (root / directory).glob("*.dds"))
    if not paths:
        raise AssertionError("No packed tint DDS assets found")
    for path in paths:
        with path.open("rb") as source:
            header = source.read(128)
        txi_path = path.with_suffix(".txi")
        txi = txi_path.read_text() if txi_path.exists() else ""
        try:
            tint_dds_layout(header, path.stat().st_size, txi)
        except AssertionError as error:
            raise AssertionError(f"Unsafe NWN tint texture {path}: {error}") from error
    # Prove the former count-zero/default-mipmap combination cannot get through
    # this guard, even though uploading its base bytes manually would succeed.
    zero_count = bytearray(header)
    struct.pack_into("<I", zero_count, 8, struct.unpack_from("<I", zero_count, 8)[0] & ~0x20000)
    struct.pack_into("<I", zero_count, 28, 0)
    for invalid_header, invalid_txi in ((zero_count, txi), (header, ""), (header, "mipmap 1\n")):
        try:
            tint_dds_layout(invalid_header, path.stat().st_size, invalid_txi)
        except AssertionError:
            continue
        raise AssertionError("Unsafe DDS mipmap negative control unexpectedly passed")
    print(f"NWN tint texture input checks passed: {len(paths)} DDS/TXI pairs; ambiguous count, missing TXI, "
        "and enabled-mipmap negative controls rejected.", flush=True)


def draw_engine_materials(test, engine, root: Path):
    """Draw production shaders with the reported NPCs' compressed DDS bytes.

    This validates native GL uploads and draws, not NWN's separate DDS parser.
    Exercise both base-level and generated-mipmap sampling without fabricating
    any bytes beyond the payload declared by each DDS header.
    """
    gl = test.gl
    attribute = gl.function("glGetAttribLocation", ct.c_int, ct.c_uint, ct.c_char_p)
    attribute_value = gl.function("glVertexAttrib4f", None, ct.c_uint, ct.c_float, ct.c_float, ct.c_float, ct.c_float)
    attribute_pointer = gl.function("glVertexAttribPointer", None, ct.c_uint, ct.c_int, ct.c_uint, ct.c_ubyte, ct.c_int, ct.c_void_p)
    enable_attribute = gl.function("glEnableVertexAttribArray", None, ct.c_uint)
    disable_attribute = gl.function("glDisableVertexAttribArray", None, ct.c_uint)
    matrix = gl.function("glUniformMatrix4fv", None, ct.c_int, ct.c_int, ct.c_ubyte, ct.c_void_p)
    compressed_image = gl.function("glCompressedTexImage2D", None, ct.c_uint, ct.c_int, ct.c_uint,
        ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_void_p)
    generate_mipmap = gl.function("glGenerateMipmap", None, ct.c_uint)
    draw = gl.function("glDrawArrays", None, ct.c_uint, ct.c_int, ct.c_int)
    finish = gl.function("glFinish", None)
    error = gl.function("glGetError", ct.c_uint)
    clear_color = gl.function("glClearColor", None, ct.c_float, ct.c_float, ct.c_float, ct.c_float)
    clear = gl.function("glClear", None, ct.c_uint)
    gl.function("glPixelStorei", None, ct.c_uint, ct.c_int)(0x0CF5, 1)
    identity = (ct.c_float * 16)(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    positions = (ct.c_float * 12)(-1, -1, -0.5, 1, 3, -1, -0.5, 1, -1, 3, -0.5, 1)
    coordinates = (ct.c_float * 6)(0, 0, 2, 0, 0, 2)

    def check(label):
        value = error()
        if value:
            raise AssertionError(f"{label}: OpenGL error {value:#x}")

    def tga(unit, path):
        data = path.read_bytes()
        width, height, bits = struct.unpack_from("<HHB", data, 12)
        start = 18 + data[0]
        payload = data[start:]
        if data[2] != 2 or bits not in (24, 32) or len(payload) != width * height * (bits // 8):
            raise AssertionError(f"Unexpected raw TGA layout: {path}")
        test.active_texture(0x84C0 + unit)
        test.bind_texture(0x0DE1, unit + 1)
        pixels = ct.create_string_buffer(payload)
        test.texture_image(0x0DE1, 0, 0x8058, width, height, 0,
            0x80E1 if bits == 32 else 0x80E0, 0x1401, pixels)
        check(f"TGA upload {path.name}")

    checks = 0
    for material in ("pme0_head056", "pfh0_head121", "pfh0_robe187"):
        source = (root / "sw_tint_mtr" / f"{material}.mtr").read_text()
        parameters = material_parameters(source)
        vertex = re.search(r"^customshaderVS\s+(\S+)", source, re.MULTILINE)[1]
        fragment = re.search(r"^customshaderFS\s+(\S+)", source, re.MULTILINE)[1]
        mask = re.search(r"^texture7\s+(\S+)", source, re.MULTILINE)[1]
        dds = next(root.glob(f"sw_tint*/{mask}.dds"))
        data = dds.read_bytes()
        width, height, _ = tint_dds_layout(data[:128], len(data), dds.with_suffix(".txi").read_text())
        payload = data[128:]
        pixels = ct.create_string_buffer(payload)
        for quality, lighting in product(range(3), range(2)):
            configuration = quality, lighting, 0, 0, 0
            program = test.program(engine.source(fragment, True, configuration),
                engine.source(vertex, False, configuration))
            attributes = []
            try:
                test.use(program)
                for name in ("m_mv", "m_proj", "m_texture", "m_view_inv", "m_view"):
                    matrix(test.location(program, name.encode()), 1, 0, identity)
                for name, size, buffer in (("vPos", 4, positions), ("vTcIn", 2, coordinates)):
                    index = attribute(program, name.encode())
                    if index >= 0:
                        enable_attribute(index)
                        attribute_pointer(index, size, 0x1406, 0, 0, buffer)
                        attributes.append(index)
                for name, value in {"vNormal": (0, 0, 1, 0), "vTangent": (1, 0, 0, 0),
                        "fHandedness": (1, 0, 0, 0), "vColor": (1, 1, 1, 1)}.items():
                    index = attribute(program, name.encode())
                    if index >= 0:
                        attribute_value(index, *value)
                for name in ("materialFrontDiffuse", "materialFrontAmbient", "materialFrontEmissive"):
                    test.vector(test.location(program, name.encode()), 1, 1, 1, 1)
                test.integer(test.location(program, b"staticLighting"), 1)
                upload_material_parameters(test, program, parameters,
                    native_npc_rows("rodian" if material == "pme0_head056" else "female"))
                for unit in range(16):
                    test.active_texture(0x84C0 + unit)
                    test.bind_texture(0x0DE1, unit + 1)
                    test.texture_parameter(0x0DE1, 0x2800, 0x2601)
                    test.texture_parameter(0x0DE1, 0x2801, 0x2601)
                    test.texture_parameter(0x0DE1, 0x813D, 0)
                    dummy = (ct.c_float * 4)(0.5, 0.5, 1, 1)
                    test.texture_image(0x0DE1, 0, 0x8814, 1, 1, 0, 0x1908, 0x1406, dummy)
                    test.integer(test.location(program, f"texUnit{unit}".encode()), unit)
                test.integer(test.location(program, b"texUnitEnv"), 14)
                test.integer(test.location(program, b"texUnitEnvCube"), 15)
                test.active_texture(0x84C0 + 15)
                test.bind_texture(0x8513, 200)
                for face in range(6):
                    test.texture_image(0x8515 + face, 0, 0x8814, 1, 1, 0, 0x1908, 0x1406, dummy)
                test.texture_parameter(0x8513, 0x2801, 0x2601)
                test.texture_parameter(0x8513, 0x813D, 0)
                tga(0, root / "sw_item" / "plt_white.tga")
                tga(10, root / "sw_item" / "plt_palette.tga")
                test.integer(test.location(program, b"texture0Bound"), 1)
                test.active_texture(0x84C0 + 7)
                test.bind_texture(0x0DE1, 8)
                compressed_image(0x0DE1, 0, 0x8DBD, width, height, 0, len(payload), pixels)
                check(f"BC5 compressed upload {dds.name}")
                for mip_mode in ("base-level", "default-incomplete", "generated-mips"):
                    test.texture_parameter(0x0DE1, 0x813D, 0 if mip_mode == "base-level" else 1000)
                    test.texture_parameter(0x0DE1, 0x2801, 0x2601 if mip_mode == "base-level" else 0x2703)
                    if mip_mode == "generated-mips":
                        generate_mipmap(0x0DE1)
                    check(f"BC5 {mip_mode} setup {dds.name}")
                    clear_color(0.9, 0.1, 0.9, 0.123)
                    clear(0x4000)
                    draw(0x0004, 0, 3)
                    finish()
                    check(f"Production draw {material}/{quality}/{lighting}/{mip_mode}")
                    result = (ct.c_float * 4)()
                    test.read(4, 4, 1, 1, 0x1908, 0x1406, result)
                    if not all(math.isfinite(value) for value in result) or abs(result[3] - 1) > 0.001:
                        raise AssertionError(f"Production draw did not produce a finite opaque fragment: {tuple(result)}")
                    checks += 1
            finally:
                for index in attributes:
                    disable_attribute(index)
                test.delete_program(program)
    print(f"Production draws passed: {checks}; actual NPC BC5 DDS + atlas/white TGA, all quality/lighting modes, "
        "base/default/generated mip sampling. NWN DDS parsing is not exercised.", flush=True)


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
        self.active_uniform = gl.function("glGetActiveUniform", None, ct.c_uint, ct.c_uint, ct.c_int,
            ct.POINTER(ct.c_int), ct.POINTER(ct.c_int), ct.POINTER(ct.c_uint), ct.c_void_p)
        self.get_uniform = gl.function("glGetUniformfv", None, ct.c_uint, ct.c_int, ct.POINTER(ct.c_float))
        self.error = gl.function("glGetError", ct.c_uint)
        self.integer = gl.function("glUniform1i", None, ct.c_int, ct.c_int)
        self.scalar = gl.function("glUniform1f", None, ct.c_int, ct.c_float)
        self.vector = gl.function("glUniform4f", None, ct.c_int, ct.c_float, ct.c_float, ct.c_float, ct.c_float)
        self.vector_array = gl.function("glUniform4fv", None, ct.c_int, ct.c_int, ct.POINTER(ct.c_float))
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
    pairs, materials, parameters = production_pairs(root / "sw_tint_mtr")
    audit_tint_dds(root)
    gl = OpenGL()
    checks = 0
    try:
        renderer = gl.function("glGetString", ct.c_char_p, ct.c_uint)(0x1F01).decode()
        test = MaterialTest(gl)
        print(f"Validating {len(pairs)} production shader pairs from {materials} generated MTRs on {renderer}.", flush=True)
        compile_engine_pairs(test, engine, pairs, parameters)
        check_native_npc_rows(test, engine, pairs, parameters)
        draw_engine_materials(test, engine, root)
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
