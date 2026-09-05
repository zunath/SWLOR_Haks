#!/usr/bin/env python3
"""Compile source MDLs without changing the installed game or its registry.

The bundled EE-aware nwnmdlcomp understands materialname and renderhint, but
its loader predates EE's KEY layout. A temporary copy changes only the fallback
directory string to './'. An empty KEY and the real supermodel closure in that
directory satisfy its loader; the compilation code is unchanged. Source files
are replaced only after every output passes a geometry/material/skin audit.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

import GenerateTintMapAssets as tint

ROOT = Path(__file__).resolve().parents[1]
COMPILER_SHA256 = "0e32070c3e00a07a5f9e93b7e4a63a40dd5486b974900bbb8a0d2f4424c612bb"
FALLBACK_DIRECTORY = b"C:/NeverwinterNights/Nwn/"
ARRAY_PROPERTIES = {"verts", "tverts", "tverts1", "tverts2", "tverts3", "faces", "weights", "constraints", "normals", "colors"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def binary(data: bytes) -> bool:
    return data[:4] == bytes(4)


def supermodel(data: bytes) -> str:
    if binary(data):
        result = data[180:244].split(b"\0", 1)[0].decode("ascii").lower()
    else:
        match = re.search(rb"(?im)^\s*setsupermodel\s+\S+\s+(\S+)", data)
        result = match[1].decode("ascii").lower() if match else ""
    return "" if result == "null" else result


def changed_models(models: dict[str, Path], ref: str | None) -> dict[str, Path]:
    if ref is None:
        return {name: path for name, path in models.items() if not binary(path.read_bytes())}
    tree = subprocess.check_output(["git", "-C", str(ROOT), "ls-tree", "-r", ref], text=True)
    old = defaultdict(set)
    for line in tree.splitlines():
        metadata, path = line.split("\t", 1)
        if path.lower().endswith(".mdl"):
            old[Path(path).stem.lower()].add(metadata.split()[2])
    selected = {}
    for name, path in models.items():
        data = path.read_bytes()
        if binary(data):
            continue
        # Git normalizes text checkouts; line endings alone do not change a model.
        normalized = data.replace(b"\r\n", b"\n")
        hashes = {hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest() for value in (data, normalized)}
        if not hashes.intersection(old[name]):
            selected[name] = path
    return selected


def parse_nodes(text: str) -> list[tuple[str, str, dict]]:
    nodes = []
    for match in re.finditer(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b", text):
        properties = {}
        lines = [line.split("#", 1)[0].split() for line in match[3].splitlines()]
        index = 0
        while index < len(lines):
            values = lines[index]
            index += 1
            if not values:
                continue
            key = values[0].lower()
            if key in ARRAY_PROPERTIES or key.endswith("key") or key.endswith("bezierkey"):
                if len(values) == 1:
                    end = next((j for j in range(index, len(lines)) if lines[j] == ["endlist"]), None)
                    if end is None:
                        raise ValueError(f"Unterminated array {key}")
                    properties[key] = lines[index:end]
                    index = end + 1
                elif len(values) == 2 and values[1].isdigit():
                    count = int(values[1])
                    properties[key] = lines[index:index + count]
                    index += count
                else:
                    raise ValueError(f"Invalid array {key}: {values}")
            else:
                properties[key] = values[1:]
        nodes.append((match[1].lower(), match[2].lower(), properties))
    return nodes


def equivalent(left, right) -> bool:
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(equivalent(a, b) for a, b in zip(left, right))
    try:
        # The compiler stores float32; the decompiler prints seven decimals.
        return math.isclose(float(left), float(right), rel_tol=2e-6, abs_tol=1.1e-7)
    except (TypeError, ValueError):
        return str(left).lower() == str(right).lower()


def color_byte(value) -> int:
    # Match native MakeRGBA: float32 arithmetic and unsigned-byte conversion,
    # including the wrapped values found in legacy out-of-range RGB exports.
    single = lambda number: struct.unpack("<f", struct.pack("<f", number))[0]
    return int(single(single(single(float(value)) * 255) + 0.5)) & 255


def triangle_corners(properties: dict, face: list[str]) -> list:
    corners = []
    for corner in range(3):
        vertex = int(face[corner])
        values = [properties["verts"][vertex]]
        for key in ("tverts", "tverts1", "tverts2", "tverts3"):
            array = properties.get(key, [])
            if array:
                values.append(array[int(face[4 + corner])][:2])
        for key in ("weights", "constraints", "normals", "colors"):
            array = properties.get(key, [])
            if array:
                item = array[vertex]
                if key == "weights":
                    item = sorted([[item[i].lower(), item[i + 1]] for i in range(0, len(item), 2) if float(item[i + 1]) != 0])
                elif key == "colors":
                    item = [color_byte(value) / 255 for value in item]
                values.append(item)
        corners.append(values)
    return corners


def quaternion(values):
    x, y, z, angle = map(float, values)
    length = math.sqrt(x * x + y * y + z * z)
    factor = math.sin(angle / 2) / length if length else 0
    return [x * factor, y * factor, z * factor, math.cos(angle / 2)]


def equivalent_quaternion(a, b) -> bool:
    return any(all(math.isclose(x, sign * y, abs_tol=2e-6) for x, y in zip(a, b)) for sign in (1, -1))


def binary_nodes(data: bytes) -> list[tuple[str, list, dict | None]]:
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    raw = 12 + uint(4)
    result = []
    def geometry_nodes(pointer):
        offsets = []
        def walk(current):
            node = 12 + current
            offsets.append(node)
            children, count = uint(node + 72), uint(node + 76)
            for index in range(count):
                walk(uint(12 + children + index * 4))
        walk(pointer)
        return offsets
    def inspect(node, offsets, geometry):
        name = data[node + 32:node + 64].split(b"\0", 1)[0].decode("ascii").lower()
        keys, count, values = uint(node + 84), uint(node + 88), uint(node + 96)
        rotations = []
        for index in range(count):
            kind, rows, times, start, columns = struct.unpack_from("<IHHHB", data, 12 + keys + index * 12)
            if kind != 20:
                continue
            for row in range(rows):
                timestamp = struct.unpack_from("<f", data, 12 + values + (times + row) * 4)[0]
                value = struct.unpack_from("<4f", data, 12 + values + (start + row * columns) * 4)
                rotations.append((timestamp, value))
        mesh_values = None
        flags = uint(node + 108)
        if geometry and flags & 0x20:
            mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
            count = struct.unpack_from("<H", data, mesh + 448)[0]
            stride = uint(mesh + 440)
            def vector_array(pointer, columns, fmt="f"):
                if pointer == 0xFFFFFFFF:
                    return []
                size = struct.calcsize(fmt) * columns
                return [list(struct.unpack_from("<" + str(columns) + fmt, data, raw + pointer + index * (stride or size))) for index in range(count)]
            mesh_values = {"verts": vector_array(uint(mesh + 444), 3)}
            mesh_values["_mesh_offset"] = mesh
            mesh_values["normals"] = vector_array(uint(mesh + 468), 3)
            color_pointer = uint(mesh + 472)
            if color_pointer != 0xFFFFFFFF:
                mesh_values["colors"] = [[channel / 255 for channel in struct.unpack_from("<4B", data, raw + color_pointer + index * (stride or 4))[:3]] for index in range(count)]
            for index, key in enumerate(("tverts", "tverts1", "tverts2", "tverts3")):
                mesh_values[key] = vector_array(uint(mesh + 452 + index * 4), 2)
            face_pointer, face_count = uint(mesh + 8), uint(mesh + 12)
            faces = []
            for index in range(face_count):
                face = 12 + face_pointer + index * 32
                vertices = list(struct.unpack_from("<3H", data, face + 26))
                faces.append([*vertices, 0, *vertices, uint(face + 16)])
            mesh_values["faces"] = faces
            extra = mesh + 512
            if flags & 0x40:
                weights = vector_array(uint(extra + 12), 4)
                indices = vector_array(uint(extra + 16), 4, "h")
                bone_nodes = struct.unpack_from("<17h", data, extra + 64)
                rows = []
                for values, bones in zip(weights, indices):
                    row = []
                    for value, bone in zip(values, bones):
                        if value == 0 or bone < 0:
                            continue
                        bone_node = offsets[bone_nodes[bone]]
                        bone_name = data[bone_node + 32:bone_node + 64].split(b"\0", 1)[0].decode("ascii").lower()
                        row.extend([bone_name, value])
                    rows.append(row)
                mesh_values["weights"] = rows
                extra += 100
            if flags & 0x80:
                extra += 52
            if flags & 0x100:
                pointer, count = uint(extra), uint(extra + 4)
                mesh_values["constraints"] = [[struct.unpack_from("<f", data, 12 + pointer + index * 4)[0]] for index in range(count)]
        result.append((name, rotations, mesh_values))
    nodes = geometry_nodes(uint(84))
    for node in nodes:
        inspect(node, nodes, True)
    animations, count = uint(132), uint(136)
    for index in range(count):
        animation = 12 + uint(12 + animations + index * 4)
        nodes = geometry_nodes(uint(animation + 72))
        for node in nodes:
            inspect(node, nodes, False)
    return result


def validate_geometry(name: str, before: dict, after: dict) -> None:
    after = dict(after)
    if not before.get("normals"):
        after.pop("normals", None)
    if not before.get("colors"):
        after.pop("colors", None)
    old_faces, new_faces = before.get("faces", []), after.get("faces", [])
    if len(old_faces) != len(new_faces):
        raise ValueError(f"{name}: face count changed")
    if not old_faces:
        for key in ARRAY_PROPERTIES:
            if not equivalent(before.get(key, []), after.get(key, [])):
                raise ValueError(f"{name}: {key} changed")
        return
    for index, (old, new) in enumerate(zip(old_faces, new_faces)):
        if not equivalent(old[7:] or ["0"], new[7:] or ["0"]):
            raise ValueError(f"{name}: face {index} material ID changed")
        left, right = triangle_corners(before, old), triangle_corners(after, new)
        if not any(equivalent(left, right[rotation:] + right[:rotation]) for rotation in range(3)):
            raise ValueError(f"{name}: face {index} geometry/UV/skin weights changed")


def validate_round_trip(source: bytes, compiled: bytes, decompiled: str) -> None:
    if len(compiled) < 244 or not binary(compiled):
        raise ValueError("Compiler did not produce an NWN binary model")
    model_size, raw_size = struct.unpack_from("<II", compiled, 4)
    if 12 + model_size + raw_size != len(compiled):
        raise ValueError("Binary model section sizes do not match its payload")
    source_text = source.decode("latin1")
    before, after = parse_nodes(source_text), parse_nodes(decompiled)
    rotations = binary_nodes(compiled)
    if len(before) != len(after):
        raise ValueError(f"Node count changed: {len(before)} -> {len(after)}")
    if len(before) != len(rotations):
        raise ValueError(f"Binary node count changed: {len(before)} -> {len(rotations)}")
    for (kind, name, properties), (other_kind, other_name, output), (rotation_name, rotation_values, mesh_values) in zip(before, after, rotations):
        if (kind, name) != (other_kind, other_name):
            raise ValueError(f"Node hierarchy changed: {name} -> {other_name}")
        if rotation_name != name:
            raise ValueError(f"Binary node hierarchy differs at {name}")
        expected_rotations = properties.get("orientationkey")
        if expected_rotations is None and "orientation" in properties:
            expected_rotations = [["0", *properties["orientation"]]]
        if expected_rotations is not None:
            if len(expected_rotations) != len(rotation_values) or not all(equivalent(row[0], timestamp) and equivalent_quaternion(quaternion(row[1:]), value) for row, (timestamp, value) in zip(expected_rotations, rotation_values)):
                raise ValueError(f"{name}: binary orientation controller changed")
        for key in ("parent", "bitmap", "materialname", "renderhint"):
            left, right = properties.get(key, []), output.get(key, [])
            if not equivalent(left, right):
                raise ValueError(f"{name}: {key} changed: {left} -> {right}")
        scalar_defaults = {"render": ["1"], "shadow": ["1"], "transparencyhint": ["0"], "beaming": ["0"], "inheritcolor": ["0"], "rotatetexture": ["0"], "tilefade": ["0"]}
        appearance_keys = {"ambient", "diffuse", "specular", "shininess", *scalar_defaults}
        if mesh_values is None:
            appearance_keys.clear()  # Dummy/animation nodes have no mesh fields.
        if kind == "danglymesh":
            appearance_keys.update(("displacement", "period", "tightness"))
        for key in appearance_keys:
            expected = properties.get(key)
            if expected is not None and key in {"ambient", "diffuse", "specular"}:
                expected = expected[:3]  # These are RGB vectors; alpha is separate.
            if expected is not None and not equivalent(expected, output.get(key, scalar_defaults.get(key, []))):
                raise ValueError(f"{name}: appearance property {key} changed")
        geometry_properties = dict(properties)
        if kind != "danglymesh":
            geometry_properties.pop("constraints", None)
        validate_geometry(name, geometry_properties, mesh_values if mesh_values is not None else output)
        for key in {k for k in properties if k.endswith("key")}:
            left, right = properties.get(key, []), output.get(key, [])
            if key == "orientationkey":
                continue
            if not equivalent(left, right):
                raise ValueError(f"{name}: {key} changed ({len(left)} -> {len(right)} rows)")
        defaults = {"position": ["0", "0", "0"], "orientation": ["0", "0", "0", "0"], "scale": ["1"], "alpha": ["1"], "selfillumcolor": ["0", "0", "0"]}
        for key, default in defaults.items():
            if key == "orientation":
                continue
            if not equivalent(properties.get(key, default), output.get(key, default)):
                raise ValueError(f"{name}: transform/controller {key} changed")
    for key in ("newmodel", "setsupermodel", "classification", "setanimationscale"):
        pattern = rf"(?im)^\s*{key}\s+([^\r\n]+)"
        old, new = re.search(pattern, source_text), re.search(pattern, decompiled)
        if key == "classification" and old and old[1].lower() == "bodypart" and not new:
            continue
        if old and (not new or not equivalent(old[1].split(), new[1].split())):
            raise ValueError(f"Model property {key} changed")


def prepare_compiler(staging: Path) -> tuple[Path, str]:
    data = (ROOT / "nwnmdlcomp.exe").read_bytes()
    if digest(data) != COMPILER_SHA256 or data.count(FALLBACK_DIRECTORY) != 1:
        raise RuntimeError("Unsupported nwnmdlcomp.exe; review its material support and loader before updating the expected hash")
    replacement = b"./" + bytes(len(FALLBACK_DIRECTORY) - 2)
    patched = data.replace(FALLBACK_DIRECTORY, replacement)
    compiler = staging / "nwnmdlcomp.exe"
    compiler.write_bytes(patched)
    # Valid, empty legacy KEY. Dependencies are staged as real loose MDLs.
    (staging / "chitin.key").write_bytes(struct.pack("<4s4s6I32x", b"KEY ", b"V1  ", 0, 0, 64, 64, 0, 0))
    return compiler, digest(patched)


def repair_legacy_input(data: bytes, dangly_periods: dict[tuple[str, str], str] | None = None) -> tuple[bytes, list[str]]:
    """Fill omitted exporter defaults; retain every supplied coordinate/value."""
    repairs = []
    text = data.decode("latin1").replace("\r\n", "\n")
    model_match = re.search(r"(?im)^\s*newmodel\s+(\S+)", text)
    model = model_match[1].lower() if model_match else ""
    def fix_node(match):
        body = match[3]
        lines = body.splitlines(keepends=True)
        period = (dangly_periods or {}).get((model, match[2].lower()))
        if period is not None:
            if match[1].lower() != "danglymesh":
                raise ValueError(f"{model}/{match[2]}: period repair requires a dangly mesh")
            field_rows = [i for i, line in enumerate(lines) if [token.lower() for token in line.split("#", 1)[0].split()[:1]] == ["period"]]
            if len(field_rows) != 1 or len(lines[field_rows[0]].split("#", 1)[0].split()) != 1:
                raise ValueError(f"{model}/{match[2]}: period repair requires exactly one empty field")
            # The native parser ignores sscanf failure, leaving this malformed
            # field uninitialized. The caller must supply an author-verified
            # value explicitly; it is never inferred as a general default.
            lines[field_rows[0]] = f"  period {period}\n"
            repairs.append(f"{match[2]}: filled malformed empty dangly period with explicit value {period}")
        index = 0
        while index < len(lines):
            values = lines[index].split()
            if len(values) == 2 and values[0].lower().startswith("tverts") and values[1].isdigit():
                for row in range(index + 1, index + 1 + int(values[1])):
                    if row >= len(lines):
                        raise ValueError("Truncated texture-coordinate array")
                    if len(lines[row].split()) == 2:
                        lines[row] = lines[row].rstrip() + " 0\n"
                        repairs.append(f"{match[2]}: filled omitted UV W component")
            if len(values) == 2 and values[0].lower() == "constraints" and values[1].isdigit():
                count = int(values[1])
                available = 0
                for row in lines[index + 1:]:
                    try:
                        float(row.strip())
                        available += 1
                    except ValueError:
                        break
                parsed = parse_nodes(f"node {match[1]} {match[2]}\n{body}endnode\n")[0][2]
                used = {int(value) for face in parsed.get("faces", []) for value in face[:3]}
                if available == count - 1 and count - 1 not in used and not "".join(lines[index + 1 + available:]).strip():
                    lines.insert(index + 1 + available, "    0\n")
                    repairs.append(f"{match[2]}: filled omitted final dangly constraint with zero")
            index += 1
        fixed = "".join(lines)
        parsed = parse_nodes(f"node {match[1]} {match[2]}\n{fixed}endnode\n")[0][2]
        if parsed.get("faces") and not parsed.get("tverts") and parsed.get("bitmap", ["NULL"])[0].lower() == "null":
            count = max(int(value) for face in parsed["faces"] for value in face[4:7]) + 1
            fixed += f"  tverts {count}\n" + "    0 0 0\n" * count
            repairs.append(f"{match[2]}: supplied neutral UVs for untextured faces")
        return f"node {match[1]} {match[2]}\n{fixed}endnode"
    text = re.sub(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b", fix_node, text)
    return text.encode("latin1"), repairs


def protect_vertex_identity(data: bytes) -> bytes:
    """Use discarded UV W values to prevent the legacy compiler losing attributes.

    NmcCompareBuiltVertices compares UVW but omits skin weights. Normals/colors
    also need source identity because the legacy compiler discards some kinds.
    NmcGenerateBumpmapData uses only serialized CVector2 UVs; mirror flags come
    from the explicit mirror list. Neither uses W.
    """
    text = data.decode("latin1")
    def protect(match):
        body = match[3]
        props = parse_nodes(match[0])[0][2]
        if not props.get("faces") or not (props.get("weights") or props.get("normals") or props.get("colors")):
            return match[0]
        faces = []
        rows = []
        identities = {}
        old_uv = props.get("tverts", [])
        for face in props["faces"]:
            face = list(face)
            for corner in range(3):
                vertex, uv = int(face[corner]), int(face[4 + corner])
                pair = (vertex, uv)
                if pair not in identities:
                    identities[pair] = len(rows)
                    value = old_uv[uv][:2] if old_uv else ["0", "0"]
                    rows.append([*value, str(vertex + 1)])
                face[4 + corner] = str(identities[pair])
            faces.append(face)
        def replace_array(key, values, original_count):
            nonlocal body
            replacement = f"  {key} {len(values)}\n" + "".join("    " + " ".join(row) + "\n" for row in values)
            pattern = rf"(?im)^\s*{key}\s+\d+[^\n]*\n(?:[^\n]*\n){{{original_count}}}"
            if re.search(pattern, body):
                body = re.sub(pattern, lambda _: replacement, body, count=1)
            else:
                body += replacement
        replace_array("tverts", rows, len(old_uv))
        replace_array("faces", faces, len(props["faces"]))
        # Additional UV channels share face indices in this ASCII format.
        for key in ("tverts1", "tverts2", "tverts3"):
            old = props.get(key, [])
            if old:
                replace_array(key, [[*old[uv][:2], str(vertex + 1)] for vertex, uv in identities], len(old))
        return f"node {match[1]} {match[2]}\n{body}endnode"
    return re.sub(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b", protect, text).encode("latin1")


def restore_vertex_attributes(source: bytes, compiled: bytes) -> bytes:
    """Preserve authored normals and colors the old compiler omits/recomputes."""
    result = bytearray(compiled)
    raw = 12 + struct.unpack_from("<I", result, 4)[0]
    before = parse_nodes(source.decode("latin1"))
    after = binary_nodes(compiled)
    for (_, name, props), (other, _, mesh) in zip(before, after):
        if name != other:
            raise ValueError("Source/binary hierarchy differs before attribute restoration")
        if mesh is None or not props.get("faces"):
            continue
        for key, pointer_offset in (("normals", 468), ("colors", 472)):
            original = props.get(key, [])
            if not original:
                continue
            if struct.unpack_from("<I", result, mesh["_mesh_offset"] + 440)[0] != 0:
                raise ValueError(f"{name}: expected deinterleaved compiler vertex arrays")
            values = [None] * len(mesh["verts"])
            for old_face, new_face in zip(props["faces"], mesh["faces"]):
                for corner in range(3):
                    source_index, output_index = int(old_face[corner]), int(new_face[corner])
                    if not equivalent(props["verts"][source_index], mesh["verts"][output_index]):
                        raise ValueError(f"{name}: compiler changed vertex order before {key} restoration")
                    value = original[source_index]
                    if values[output_index] is not None and not equivalent(values[output_index], value):
                        raise ValueError(f"{name}: compiler merged distinct source {key}")
                    values[output_index] = value
            if any(value is None for value in values):
                raise ValueError(f"{name}: unmapped compiled vertex while restoring {key}")
            while (len(result) - raw) % 4:
                result.append(0)
            pointer = len(result) - raw
            if key == "normals":
                if any(pointer != 0xFFFFFFFF for pointer in struct.unpack_from("<6I", result, mesh["_mesh_offset"] + 476)):
                    raise ValueError(f"{name}: cannot replace normals while precomputed bump arrays exist")
                result.extend(b"".join(struct.pack("<3f", *map(float, value)) for value in values))
            else:
                for value in values:
                    channels = [color_byte(channel) for channel in value]
                    result.extend(bytes([*channels, 255]))
            struct.pack_into("<I", result, mesh["_mesh_offset"] + pointer_offset, pointer)
    struct.pack_into("<I", result, 8, len(result) - raw)
    return bytes(result)


def run_compiler(compiler: Path, staging: Path, arguments: list[str], log: str) -> None:
    with (staging / log).open("w", encoding="utf8") as stream:
        result = subprocess.run([str(compiler), *arguments], cwd=staging, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    text = (staging / log).read_text(encoding="utf8", errors="replace")
    if result.returncode or re.search(r"(?i)(?:error:|aborted|unable to locate|unable to open)", text):
        errors = [line for line in text.splitlines() if re.search(r"(?i)(?:error:|aborted|unable)", line)]
        raise RuntimeError(f"Model compiler failed ({log}): " + "\n".join(errors[:30]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Compile ASCII models whose content differs from this git ref; default: every active ASCII model")
    parser.add_argument("--model", action="append", help="Compile only this resref (repeatable), plus ASCII supermodels")
    parser.add_argument("--tint-bound", action="store_true", help="Compile only models owning a tint binding, plus ASCII supermodels")
    parser.add_argument("--game-data", type=Path, help="Installed EE data directory, used only when a required supermodel is absent from the HAK sources")
    parser.add_argument("--allow-missing-supermodels", action="store_true", help="Compile unresolved legacy roots as NULL, then restore their names, matching native loader behavior")
    parser.add_argument("--repair-legacy-inputs", action="store_true", help="Fill omitted zero UV components/constraints and missing neutral UVs in staging; log every repair")
    parser.add_argument("--dangly-period", action="append", default=[], metavar="MODEL:NODE:VALUE", help="With --repair-legacy-inputs, fill only this empty dangly period with an author-verified value; never replaces a supplied value")
    parser.add_argument("--apply", action="store_true", help="Replace source models only after the complete batch validates")
    args = parser.parse_args()
    dangly_periods = {}
    for repair in args.dangly_period:
        parts = repair.lower().split(":")
        if len(parts) != 3 or not all(parts):
            parser.error("--dangly-period requires MODEL:NODE:VALUE")
        try:
            value = float(parts[2])
        except ValueError:
            parser.error("--dangly-period value must be a finite positive number")
        if not math.isfinite(value) or value <= 0:
            parser.error("--dangly-period value must be a finite positive number")
        key = tuple(parts[:2])
        if key in dangly_periods:
            parser.error(f"Duplicate --dangly-period repair: {key}")
        dangly_periods[key] = parts[2]
    if dangly_periods and not args.repair_legacy_inputs:
        parser.error("--dangly-period requires --repair-legacy-inputs")
    models = tint.find_active_models()
    selected = {name.lower(): models[name.lower()] for name in args.model} if args.model else changed_models(models, args.since)
    if args.tint_bound:
        owners = {model for model, _, _ in tint.build_model_material_rows(tint.load_source_manifest())}
        selected = {name: path for name, path in selected.items() if name in owners}
    selected = {name: path for name, path in selected.items() if not binary(path.read_bytes())}
    if not selected:
        print("No ASCII models require compilation.")
        return
    staging = ROOT / "output" / f"model-compile-{time.time_ns()}"
    for folder in (staging, staging / "input", staging / "binary", staging / "decompiled"):
        folder.mkdir(parents=True, exist_ok=True)
    compiler, compiler_hash = prepare_compiler(staging)
    dependencies = {}
    inputs = {}
    repairs = {}
    missing_supermodels = []
    stock = None
    pending = list(selected)
    while pending:
        name = pending.pop()
        if name in dependencies:
            continue
        if name in models:
            data = models[name].read_bytes()
        else:
            if args.game_data is None:
                raise RuntimeError(f"Supermodel {name} is absent from HAK sources; supply --game-data")
            if stock is None:
                stock = tint.read_stock_key_models(args.game_data)
            if name not in stock:
                if not args.allow_missing_supermodels:
                    raise RuntimeError(f"Required supermodel {name} is absent from source and installed game")
                missing_supermodels.append(name)
                dependencies[name] = b""
                continue
            else:
                data = tint.extract_stock_bif_resource(*stock[name])
        dependencies[name] = data
        if args.repair_legacy_inputs and not binary(data):
            inputs[name], changes = repair_legacy_input(data, dangly_periods)
            if changes:
                repairs[name] = changes
        else:
            inputs[name] = data
        if name in models and not binary(data):
            selected[name] = models[name]
        parent = supermodel(data)
        if parent:
            pending.append(parent)
    restored_supermodels = {}
    for model, node in dangly_periods:
        if model not in repairs or not any(item.lower().startswith(f"{node}: filled malformed empty dangly period") for item in repairs[model]):
            raise ValueError(f"Requested dangly period repair did not match a selected input: {model}/{node}")
    for name, data in inputs.items():
        prepared = data
        if not binary(data):
            prepared = protect_vertex_identity(data)
            parent = supermodel(data)
            if parent in missing_supermodels:
                local = parse_nodes(data.decode("latin1").split("endmodelgeom", 1)[0])
                names = {node[1] for node in local}
                for _, node_name, props in local:
                    for row in props.get("weights", []):
                        if any(float(row[index + 1]) and row[index].lower() not in names for index in range(0, len(row), 2)):
                            raise ValueError(f"{name}/{node_name}: missing root owns a required skin bone")
                restored_supermodels[name] = parent
                prepared = re.sub(rb"(?im)^(\s*setsupermodel\s+\S+\s+)\S+", rb"\1NULL", prepared)
        (staging / f"{name}.mdl").write_bytes(prepared)
        if name in selected:
            (staging / "input" / f"{name}.mdl").write_bytes(prepared)
    print(f"Compiling {len(selected)} ASCII models; {len(dependencies)} models in the supermodel closure. Staging: {staging}", flush=True)
    run_compiler(compiler, staging, ["-cne", str(staging / "input" / "*.mdl"), str(staging / "binary") + "/"], "compile.log")
    missing = [name for name in selected if not (staging / "binary" / f"{name}.mdl").is_file()]
    if missing:
        raise RuntimeError(f"Compiler produced no output for {missing[:20]}")
    for name in selected:
        path = staging / "binary" / f"{name}.mdl"
        compiled = bytearray(restore_vertex_attributes(inputs[name], path.read_bytes()))
        if name in restored_supermodels:
            compiled[180:244] = restored_supermodels[name].encode("ascii").ljust(64, b"\0")
        path.write_bytes(compiled)
    print("Compilation complete; decompiling every output for validation.", flush=True)
    run_compiler(compiler, staging, ["-de", str(staging / "binary" / "*.mdl"), str(staging / "decompiled") + "/"], "decompile.log")
    report = {"compiler_sha256": COMPILER_SHA256, "staged_compiler_sha256": compiler_hash, "missing_supermodels": sorted(missing_supermodels), "input_repairs": repairs, "models": [], "failures": []}
    for name, path in sorted(selected.items()):
        compiled = (staging / "binary" / f"{name}.mdl").read_bytes()
        try:
            validate_round_trip(inputs[name], compiled, (staging / "decompiled" / f"{name}.mdl").read_text(encoding="latin1"))
        except ValueError as error:
            report["failures"].append({"model": name, "error": str(error)})
        report["models"].append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "source_sha256": digest(dependencies[name]), "compiled_sha256": digest(compiled), "bytes": len(compiled)})
    (staging / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    if report["failures"]:
        raise RuntimeError(f"{len(report['failures'])} model validation failures: {report['failures'][:20]}; full report: {staging / 'report.json'}")
    if args.apply:
        for name, path in selected.items():
            if path.read_bytes() != dependencies[name]:
                raise RuntimeError(f"Source changed during compilation: {path}")
        for name, path in selected.items():
            shutil.copyfile(staging / "binary" / f"{name}.mdl", path)
    print(f"Validated {len(selected)} models; {'applied' if args.apply else 'source unchanged'}. Report: {staging / 'report.json'}")


if __name__ == "__main__":
    main()
