#!/usr/bin/env python3
"""Compile source MDLs without changing the installed game or its registry.

Uses CleanModels v4.4.2 bundled with cleanmodels-qt v1.1.7. Source files are
replaced only after every output passes a geometry/material/skin audit.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
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

import CleanModels
import GenerateTintMapAssets as tint

ROOT = Path(__file__).resolve().parents[1]
COMPILER_SHA256 = CleanModels.COMPILER_SHA256
ARRAY_PROPERTIES = {"verts", "tverts", "tverts1", "tverts2", "tverts3", "faces", "weights", "constraints", "normals", "colors"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def binary(data: bytes) -> bool:
    return data[:4] == bytes(4)


def animation_source(source: bytes, compiled: bytes) -> bytes | None:
    """Keep editable generated banks outside HAK folders, tied to both exact contents."""
    content = source.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
    if not content.startswith(b"# SWLOR authored animations for "):
        return None
    return (f"# SWLOR compiled animation source v1 {digest(compiled)} {digest(content)}\n".encode("ascii")
            + content)


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


@lru_cache(maxsize=1)
def external_part_types() -> set[str]:
    table = ROOT / "sw_2da" / "capart.2da"
    # Rows contain: row index, NAME, MDLNAME, NODENAME.
    return {row.split()[2].lower() for row in table.read_text().splitlines()[3:]
            if len(row.split()) >= 4 and row.split()[2].lower() != "robe"}


def external_skeleton_name(data: bytes) -> str | None:
    if binary(data):
        name = data[20:84].split(b"\0", 1)[0].decode("ascii").lower()
    else:
        match = re.search(rb"(?im)^\s*newmodel\s+(\S+)", data)
        name = match[1].decode("ascii").lower() if match else ""
    match = re.fullmatch(r"(p[fm][a-z]\d+)_([a-z]+)\d+", name)
    if match and match[2] not in {"head", "robe"} and match[2] in external_part_types():
        return match[1]
    return None


def binary_nodes(data: bytes, skin_skeleton: bytes | None = None) -> list[tuple[str, list, dict | None]]:
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
                skeleton_name = external_skeleton_name(data)
                if skeleton_name is not None:
                    from NeckSkinBindings import body_model
                    donor = skin_skeleton
                    if donor is None:
                        path = tint.find_active_models().get(skeleton_name)
                        if path is None:
                            raise ValueError(f"External skin requires its wearer skeleton: {skeleton_name}")
                        donor = path.read_bytes()
                    bone_names = [item[0] for item in body_model(donor).nodes]
                    if uint(extra + 24) != len(bone_names):
                        raise ValueError(f"{name}: external bone table does not match {skeleton_name}")
                else:
                    bone_names = [data[pointer + 32:pointer + 64].split(b"\0", 1)[0].decode("ascii").lower()
                                  for pointer in offsets]
                rows = []
                for values, bones in zip(weights, indices):
                    row = []
                    for value, bone in zip(values, bones):
                        if value == 0:
                            continue
                        if not 0 <= bone < len(bone_nodes) or not 0 <= bone_nodes[bone] < len(bone_names):
                            raise ValueError(f"{name}: skin weight references an invalid skeleton bone")
                        bone_name = bone_names[bone_nodes[bone]]
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


def validate_round_trip(source: bytes, compiled: bytes, decompiled: str, skin_skeleton: bytes | None = None) -> None:
    if len(compiled) < 244 or not binary(compiled):
        raise ValueError("Compiler did not produce an NWN binary model")
    model_size, raw_size = struct.unpack_from("<II", compiled, 4)
    if 12 + model_size + raw_size != len(compiled):
        raise ValueError("Binary model section sizes do not match its payload")
    source_text = source.decode("latin1")
    before, after = parse_nodes(source_text), parse_nodes(decompiled)
    rotations = binary_nodes(compiled, skin_skeleton)
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
        # CleanModels v4.4.2 omits a static identity rotation on the model root, which
        # the engine reads as identity; any other root rotation must still round-trip.
        if (properties.get("parent", [""])[0].lower() == "null" and not rotation_values and expected_rotations is not None
                and len(expected_rotations) == 1 and equivalent(expected_rotations[0][0], 0)
                and equivalent_quaternion(quaternion(expected_rotations[0][1:]), (0.0, 0.0, 0.0, 1.0))):
            expected_rotations = None
        if expected_rotations is not None:
            if len(expected_rotations) != len(rotation_values) or not all(equivalent(row[0], timestamp) and equivalent_quaternion(quaternion(row[1:]), value) for row, (timestamp, value) in zip(expected_rotations, rotation_values)):
                raise ValueError(f"{name}: binary orientation controller changed")
        # The native compiler emits a time-zero constant track as a static
        # property. Compare that representation without relaxing animated keys.
        def static_constants(values):
            result = dict(values)
            for key in ("positionkey", "scalekey"):
                rows = result.get(key, [])
                if len(rows) == 1 and float(rows[0][0]) == 0:
                    result[key.removesuffix("key")] = result.pop(key)[0][1:]
            return result
        properties, output = static_constants(properties), static_constants(output)
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
    source = CleanModels.find_compiler()
    compiler = staging / "cleanmodels.exe"
    shutil.copyfile(source, compiler)
    return compiler, digest(compiler.read_bytes())


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


@lru_cache(maxsize=1)
def material_resources() -> dict[str, Path]:
    # Match the active HAK precedence without rescanning all folders per compile.
    return {path.stem.lower(): path for directory in tint.hak_directories()
            for path in directory.glob("*.mtr")}


def material_references(source: bytes) -> set[str]:
    return {value.decode("ascii").lower() for value in
            re.findall(rb"(?im)^\s*(?:materialname|bitmap)\s+(\S+)", source)
            if value.lower() not in {b"null", b"****"}}


def compiler_material_state(names, refresh=False):
    """Record selected MTR contents and absent lookups that could gain an MTR."""
    if refresh:
        material_resources.cache_clear()
    active = material_resources()
    return {name: ({"path": active[name].relative_to(ROOT).as_posix(),
                    "sha256": digest(active[name].read_bytes())} if name in active else None)
            for name in sorted(names)}


def compiler_material_digest(source, state):
    selected = {name: state[name] for name in sorted(material_references(source))}
    return digest(json.dumps(selected, sort_keys=True).encode())


def run_compiler(compiler: Path, staging: Path, arguments: list[str], log: str,
                 game_dir: Path | None = None) -> None:
    # Keep the shared generator call interface; translate it to CleanModels once.
    modes = {"-cne": "compile", "-de": "decompile"}
    if len(arguments) != 3 or arguments[0] not in modes:
        raise ValueError(f"Unsupported model compiler arguments: {arguments}")
    source = Path(arguments[1])
    inputs = sorted(source.parent.glob(source.name))
    if not inputs:
        raise ValueError(f"No model compiler inputs: {source}")
    for path in inputs:
        data = path.read_bytes()
        if not binary(data):
            for name in material_references(data):
                target = staging / (name + ".mtr")
                if name in material_resources():
                    shutil.copyfile(material_resources()[name], target)
                else:
                    target.unlink(missing_ok=True)
    resources = [path for path in (ROOT / "sw_pt_root", ROOT / "sw_2da") if path.is_dir()]
    output = Path(arguments[2])
    output.mkdir(parents=True, exist_ok=True)
    CleanModels.run(compiler, staging, modes[arguments[0]], inputs, output, log, resources, game_dir)
    if modes[arguments[0]] == "compile":
        for path in inputs:
            CleanModels.validate_binary(output / path.name)


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
        skeleton = external_skeleton_name(data)
        if skeleton:
            pending.append(skeleton)
    restored_supermodels = {}
    for model, node in dangly_periods:
        if model not in repairs or not any(item.lower().startswith(f"{node}: filled malformed empty dangly period") for item in repairs[model]):
            raise ValueError(f"Requested dangly period repair did not match a selected input: {model}/{node}")
    for name, data in inputs.items():
        prepared = data if binary(data) else CleanModels.explicit_orientations(data)
        if not binary(data):
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
    run_compiler(compiler, staging, ["-cne", str(staging / "input" / "*.mdl"), str(staging / "binary") + "/"], "compile.log", args.game_data.parent if args.game_data else None)
    missing = [name for name in selected if not (staging / "binary" / f"{name}.mdl").is_file()]
    if missing:
        raise RuntimeError(f"Compiler produced no output for {missing[:20]}")
    for name in selected:
        path = staging / "binary" / f"{name}.mdl"
        compiled = bytearray(path.read_bytes())
        if name in restored_supermodels:
            compiled[180:244] = restored_supermodels[name].encode("ascii").ljust(64, b"\0")
        path.write_bytes(compiled)
    print("Compilation complete; decompiling every output for validation.", flush=True)
    run_compiler(compiler, staging, ["-de", str(staging / "binary" / "*.mdl"), str(staging / "decompiled") + "/"], "decompile.log", args.game_data.parent if args.game_data else None)
    report = {"compiler_sha256": COMPILER_SHA256, "staged_compiler_sha256": compiler_hash, "missing_supermodels": sorted(missing_supermodels), "input_repairs": repairs, "models": [], "failures": []}
    for name, path in sorted(selected.items()):
        compiled = (staging / "binary" / f"{name}.mdl").read_bytes()
        try:
            validate_round_trip(inputs[name], compiled, (staging / "decompiled" / f"{name}.mdl").read_text(encoding="latin1"),
                                inputs.get(external_skeleton_name(compiled)))
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
            compiled = (staging / "binary" / f"{name}.mdl").read_bytes()
            editable = animation_source(dependencies[name], compiled)
            if editable is not None:
                source_path = ROOT / "model_sources" / (path.relative_to(ROOT).as_posix() + ".ascii")
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(editable)
            shutil.copyfile(staging / "binary" / f"{name}.mdl", path)
    print(f"Validated {len(selected)} models; {'applied' if args.apply else 'source unchanged'}. Report: {staging / 'report.json'}")


if __name__ == "__main__":
    main()
