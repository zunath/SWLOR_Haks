#!/usr/bin/env python3
"""Convert 3D model PLTs to compact, shader-driven BC5 tint maps.

The packed DDS keeps the PLT shade in red and its layer id in green.  It uses
the original PLT resref. The generated tintmap.2da is the authoritative
model/material/layer catalog consumed by the game server and appearance
editor; material names are read from the binary MDLs rather than inferred
from model names. Palette-driven inventory icons remain PLTs because the NWN
UI requires a same-resref PLT and cannot consume model material shaders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_ICON_PLT_PATTERN = re.compile(
    r"^(?:ip[fm]_|ihelm_|icloak_|idye_)",
    re.IGNORECASE,
)
TINT_DIRECTORIES = tuple(REPOSITORY_ROOT / f"sw_tint{index}" for index in range(3))
OUTPUT_MTR_DIRECTORY = REPOSITORY_ROOT / "sw_tint_mtr"
OUTPUT_2DA = REPOSITORY_ROOT / "sw_2da" / "tintmap.2da"
HAK_CONFIG = REPOSITORY_ROOT / "hakbuilder.json"
SOURCE_MANIFEST = Path(__file__).with_name("TintMapSources.json")
WHITE_TEXTURE = REPOSITORY_ROOT / "sw_item" / "plt_white.tga"
PALETTE_TEXTURE = REPOSITORY_ROOT / "sw_item" / "plt_palette.tga"
PALETTE_TXI = REPOSITORY_ROOT / "sw_item" / "plt_palette.txi"
TINT_SHADER = REPOSITORY_ROOT / "sw_shader" / "fs_plt_tinter.shd"
_MTR_PATHS_BY_RESREF: dict[str, Path] | None = None
_SOURCE_MTR_PATHS_BY_RESREF: dict[str, list[Path]] | None = None
_BC4_LAYER_CANDIDATES: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
_EXACT_LAYER_ENCODINGS: dict[tuple[int, ...], tuple[int, int, np.ndarray]] = {}
_ACTIVE_MODELS: dict[str, Path] | None = None
_TABLE_REFERENCED_RESREFS: set[str] | None = None
_HAK_DIRECTORIES: tuple[Path, ...] | None = None

PLT_HEADER = b"PLT V1  "
PLT_DATA_OFFSET = 24
LAYER_NAMES = (
    "Skin",
    "Hair",
    "Metal1",
    "Metal2",
    "Cloth1",
    "Cloth2",
    "Leather1",
    "Leather2",
    "Tattoo1",
    "Tattoo2",
)
TEXTURE1_ALPHA_SHADERS = {"fs_plt_hair", "pfh0_neck199", "pmh0_neck199"}
TEXTURE1_ALPHA_MATERIALS = {"pfh0_neck199", "pmh0_head248", "pmh0_neck199"}
TEXTURE9_ALPHA_MATERIALS = {
    "pfh0_head232": "pfh0_head232_a",
    "pmh0_head231": "pmh0_head231_a",
}

FILE_HEADER_SIZE = 12
NODE_HEADER_SIZE = 112
LIGHT_HEADER_SIZE = 92
EMITTER_HEADER_SIZE = 212
REFERENCE_HEADER_SIZE = 68
MESH_TEXTURE0_OFFSET = 120


def require_repository_root() -> None:
    expected = {"sw_2da", "sw_shader", "sw_item", "sw_tint_mtr", "sw_tint0", "sw_tint1", "sw_tint2"}
    missing = sorted(name for name in expected if not (REPOSITORY_ROOT / name).is_dir())
    if not HAK_CONFIG.is_file() or missing:
        raise RuntimeError(
            "Refusing to run outside the SWLOR HAK repository; missing: "
            + ", ".join(([HAK_CONFIG.name] if not HAK_CONFIG.is_file() else []) + missing)
        )


def load_hak_config() -> dict[str, object]:
    config_text = HAK_CONFIG.read_text(encoding="utf-8-sig")
    # The established builder config permits trailing commas even though the
    # standard-library JSON parser does not.
    return json.loads(re.sub(r",\s*([}\]])", r"\1", config_text))


def hak_directories() -> tuple[Path, ...]:
    global _HAK_DIRECTORIES
    if _HAK_DIRECTORIES is not None:
        return _HAK_DIRECTORIES

    directories: list[Path] = []
    for hak in load_hak_config().get("HakList", []):
        relative_path = hak.get("Path")
        if not relative_path:
            continue
        directory = (REPOSITORY_ROOT / str(relative_path)).resolve()
        if not directory.is_dir():
            raise RuntimeError(f"Configured HAK source directory does not exist: {directory}")
        directories.append(directory)

    _HAK_DIRECTORIES = tuple(directories)
    return _HAK_DIRECTORIES


def is_inventory_icon_plt(path: Path) -> bool:
    return (
        path.suffix.lower() == ".plt"
        and INVENTORY_ICON_PLT_PATTERN.match(path.stem) is not None
    )


def is_tint_material_plt(path: Path) -> bool:
    return path.suffix.lower() == ".plt" and not is_inventory_icon_plt(path)


def mtr_path(material: str) -> Path:
    global _MTR_PATHS_BY_RESREF

    if _MTR_PATHS_BY_RESREF is None:
        _MTR_PATHS_BY_RESREF = {}
        for path in OUTPUT_MTR_DIRECTORY.glob("*.mtr"):
            key = path.stem.lower()
            if key in _MTR_PATHS_BY_RESREF:
                raise RuntimeError(
                    f"Duplicate case-insensitive MTR resref: {_MTR_PATHS_BY_RESREF[key]} and {path}"
                )
            _MTR_PATHS_BY_RESREF[key] = path

    key = material.lower()
    if key not in _MTR_PATHS_BY_RESREF:
        _MTR_PATHS_BY_RESREF[key] = OUTPUT_MTR_DIRECTORY / f"{key}.mtr"
    return _MTR_PATHS_BY_RESREF[key]


def source_mtr_paths(material: str) -> list[Path]:
    global _SOURCE_MTR_PATHS_BY_RESREF

    if _SOURCE_MTR_PATHS_BY_RESREF is None:
        _SOURCE_MTR_PATHS_BY_RESREF = {}
        output_directory = OUTPUT_MTR_DIRECTORY.resolve()
        for directory in hak_directories():
            if directory.resolve() == output_directory:
                continue
            for path in directory.glob("*.mtr"):
                _SOURCE_MTR_PATHS_BY_RESREF.setdefault(path.stem.lower(), []).append(path)

    return _SOURCE_MTR_PATHS_BY_RESREF.get(material.lower(), [])


def find_plts(predicate: Callable[[Path], bool]) -> tuple[dict[str, Path], list[Path]]:
    active: dict[str, Path] = {}
    all_paths: list[Path] = []

    # Later configured HAKs have higher resource priority, so scan in reverse
    # and retain the first physical resource for each resref.
    for directory in reversed(hak_directories()):
        for path in sorted(directory.glob("*.plt"), key=lambda value: value.name.lower()):
            if not predicate(path):
                continue
            all_paths.append(path)
            active.setdefault(path.stem.lower(), path)

    return active, all_paths


def find_tint_material_plts() -> tuple[dict[str, Path], list[Path]]:
    return find_plts(is_tint_material_plt)


def find_inventory_icon_plts() -> tuple[dict[str, Path], list[Path]]:
    return find_plts(is_inventory_icon_plt)


def find_tint_material_plts_outside_sources() -> list[Path]:
    source_roots = set(hak_directories())
    return [
        path
        for directory in REPOSITORY_ROOT.iterdir()
        if directory.is_dir() and directory.resolve() not in source_roots
        for path in directory.glob("*.plt")
        if is_tint_material_plt(path)
    ]


def find_active_models() -> dict[str, Path]:
    global _ACTIVE_MODELS
    if _ACTIVE_MODELS is not None:
        return _ACTIVE_MODELS
    if not HAK_CONFIG.exists():
        raise RuntimeError(f"Missing HAK configuration: {HAK_CONFIG}")

    models: dict[str, Path] = {}
    # Later HAKs have higher resource priority, so later paths replace earlier ones.
    for directory in hak_directories():
        current_hak: dict[str, Path] = {}
        for path in directory.glob("*.mdl"):
            model = path.stem.lower()
            if model in current_hak:
                raise RuntimeError(
                    f"Duplicate case-insensitive model resref in {directory}: "
                    f"{current_hak[model]} and {path}"
                )
            current_hak[model] = path
        models.update(current_hak)

    _ACTIVE_MODELS = models
    return _ACTIVE_MODELS


def find_table_referenced_resrefs() -> set[str]:
    global _TABLE_REFERENCED_RESREFS
    if _TABLE_REFERENCED_RESREFS is not None:
        return _TABLE_REFERENCED_RESREFS

    resrefs: set[str] = set()
    for directory in hak_directories():
        for path in directory.glob("*.2da"):
            if path.resolve() == OUTPUT_2DA.resolve():
                continue
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
            resrefs.update(
                token.lower()
                for token in re.findall(r"[A-Za-z0-9_.-]+", text)
                if len(token) <= 16
            )

    _TABLE_REFERENCED_RESREFS = resrefs
    return _TABLE_REFERENCED_RESREFS


def read_binary_model_material_fields(path: Path, data: bytes) -> list[tuple[int, str]]:
    def read_uint32(offset: int) -> int:
        if offset < 0 or offset + 4 > len(data):
            raise ValueError(f"{path}: truncated uint32 at 0x{offset:x}")
        return struct.unpack_from("<I", data, offset)[0]

    def read_resref(offset: int, length: int) -> str:
        if offset < 0 or offset + length > len(data):
            raise ValueError(f"{path}: truncated resref at 0x{offset:x}")
        return data[offset : offset + length].split(b"\0", 1)[0].decode(
            "ascii", errors="strict"
        ).lower()

    if len(data) < FILE_HEADER_SIZE + 76:
        raise ValueError(f"{path}: expected an NWN1 binary MDL")

    pending = [read_uint32(FILE_HEADER_SIZE + 72)]
    visited: set[int] = set()
    materials: list[tuple[int, str]] = []
    while pending:
        pointer = pending.pop()
        if pointer in visited:
            continue
        visited.add(pointer)

        node = FILE_HEADER_SIZE + pointer
        if node < FILE_HEADER_SIZE or node + NODE_HEADER_SIZE > len(data):
            raise ValueError(f"{path}: invalid node pointer 0x{pointer:x}")

        child_array_pointer = read_uint32(node + 72)
        child_count = read_uint32(node + 76)
        if child_count > 100_000:
            raise ValueError(f"{path}: invalid child count {child_count}")
        child_array = FILE_HEADER_SIZE + child_array_pointer
        if child_count and child_array + child_count * 4 > len(data):
            raise ValueError(f"{path}: invalid child array")
        pending.extend(read_uint32(child_array + index * 4) for index in range(child_count))

        content = read_uint32(node + 108)
        if content & 0x20 == 0:
            continue

        mesh = node + NODE_HEADER_SIZE
        if content & 0x02:
            mesh += LIGHT_HEADER_SIZE
        if content & 0x04:
            mesh += EMITTER_HEADER_SIZE
        if content & 0x10:
            mesh += REFERENCE_HEADER_SIZE
        material_offset = mesh + MESH_TEXTURE0_OFFSET
        material = read_resref(material_offset, 64)
        if material and material != "null":
            materials.append((material_offset, material))

    return materials


def read_model_materials(path: Path) -> list[str]:
    data = path.read_bytes()

    is_binary = len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == 0
    if not is_binary:
        text = data.decode("ascii", errors="strict")
        materials = {
            match.group(1).lower()
            for match in re.finditer(
                r"(?im)^\s*(?:bitmap|texture0)\s+([^\s#]+)",
                text,
            )
            if match.group(1).lower() != "null"
        }
        return sorted(materials)

    return sorted({material for _, material in read_binary_model_material_fields(path, data)})


def replace_model_materials(path: Path, replacements: dict[str, str]) -> bool:
    normalized = {
        source.lower(): target.lower()
        for source, target in replacements.items()
        if source.lower() != target.lower()
    }
    if not normalized:
        return False

    data = path.read_bytes()
    is_binary = len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == 0
    if not is_binary:
        text = data.decode("ascii", errors="strict")
        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            source = match.group(2).lower()
            target = normalized.get(source)
            if target is None:
                return match.group(0)
            changed = True
            return f"{match.group(1)}{target}"

        updated = re.sub(
            r"(?im)^(\s*(?:bitmap|texture0)\s+)([^\s#]+)",
            replace,
            text,
        )
        if changed:
            path.write_text(updated, encoding="ascii", newline="")
        return changed

    updated = bytearray(data)
    changed = False
    for offset, source in read_binary_model_material_fields(path, data):
        target = normalized.get(source)
        if target is None:
            continue
        encoded = target.encode("ascii")
        if len(encoded) > 16:
            raise ValueError(f"{path}: generated material resref is too long: {target}")
        updated[offset : offset + 64] = encoded.ljust(64, b"\0")
        changed = True

    if changed:
        path.write_bytes(updated)
    return changed


def model_material_scope(model: str, path: Path) -> str:
    directory = path.parent.name.lower()
    if directory.startswith("sw_pt_"):
        # Only one model from a modular body-part directory can be displayed at
        # a time. Left/right and other independently colored parts live in
        # separate directories and therefore receive separate material names.
        return f"part:{directory.removeprefix('sw_pt_')}"
    # Full creature models use the creature-wide palette rather than armor
    # part colors, so sharing their original material remains intentional.
    return "shared:creature"


def scoped_material_alias(source: str, scope: str) -> str:
    scope_name = scope.split(":", 1)[-1]
    scope_hint = re.sub(r"[^a-z0-9]", "", scope_name)[:2].ljust(2, "x")
    digest = hashlib.sha256(f"{source}\0{scope}".encode("ascii")).hexdigest()[:6]
    return f"{source[:6]}_{scope_hint}_{digest}"


def build_alias_source_lookup(
    entries: dict[str, dict[str, object]],
) -> dict[str, str]:
    materials = set(entries)
    aliases: dict[str, str] = {}
    for source, entry in sorted(entries.items()):
        for value in entry.get("aliases", []):
            alias = str(value).lower()
            if alias in materials and alias != source:
                raise RuntimeError(
                    f"Generated material alias '{alias}' collides with source material '{alias}'"
                )
            existing = aliases.get(alias)
            if existing is not None and existing != source:
                raise RuntimeError(
                    f"Generated material alias '{alias}' collides for '{existing}' and '{source}'"
                )
            aliases[alias] = source
    return aliases


def find_model_tint_material_references(
    path: Path,
    materials: set[str],
    alias_sources: dict[str, str],
) -> dict[str, str]:
    try:
        return {
            material: alias_sources.get(material, material)
            for material in read_model_materials(path)
            if material in materials or material in alias_sources
        }
    except (UnicodeDecodeError, ValueError):
        # A handful of legacy helpers have malformed or nonstandard compiled
        # headers. Retain their candidate material strings rather than risk
        # deleting a live texture.
        raw = path.read_bytes()
        return {
            value[:-1].decode("ascii").lower(): alias_sources.get(
                value[:-1].decode("ascii").lower(),
                value[:-1].decode("ascii").lower(),
            )
            for value in re.findall(rb"[A-Za-z0-9_.-]{1,64}\0", raw)
            if value[:-1].decode("ascii").lower() in materials
            or value[:-1].decode("ascii").lower() in alias_sources
        }


def build_model_material_plan(
    entries: dict[str, dict[str, object]],
) -> tuple[
    list[tuple[str, str, list[int]]],
    dict[Path, dict[str, str]],
    dict[str, str],
]:
    models = find_active_models()
    materials = set(entries)
    alias_sources = build_alias_source_lookup(entries)
    records: dict[tuple[str, str, str], dict[str, object]] = {}

    for model, path in sorted(models.items()):
        scope = model_material_scope(model, path)
        references = find_model_tint_material_references(path, materials, alias_sources)
        for current, source in references.items():
            key = (model, source, scope)
            record = records.setdefault(
                key,
                {"model": model, "source": source, "scope": scope, "path": path, "current": set()},
            )
            current_materials = record["current"]
            assert isinstance(current_materials, set)
            current_materials.add(current)

    # The repository does not carry every stock model (notably many cloaks).
    # Preserve conventional same-name mappings that cannot be patched locally.
    table_references = find_table_referenced_resrefs()
    for source in sorted(entries):
        if source in models and source not in table_references:
            continue
        if any(record["model"] == source and record["source"] == source for record in records.values()):
            continue
        scope = f"stock:{source}"
        records[(source, source, scope)] = {
            "model": source,
            "source": source,
            "scope": scope,
            "path": None,
            "current": {source},
        }

    scopes_by_source: dict[str, set[str]] = {}
    for record in records.values():
        source = str(record["source"])
        scopes_by_source.setdefault(source, set()).add(str(record["scope"]))

    rows: set[tuple[str, str, tuple[int, ...]]] = set()
    replacements: dict[Path, dict[str, str]] = {}
    active_aliases: dict[str, str] = {}
    for record in records.values():
        model = str(record["model"])
        source = str(record["source"])
        scope = str(record["scope"])
        path = record["path"]
        material = source
        if path is not None and scope.startswith("part:") and len(scopes_by_source[source]) > 1:
            material = scoped_material_alias(source, scope)
            if material in materials and material != source:
                raise RuntimeError(
                    f"Generated material alias '{material}' collides with source material '{material}'"
                )
            existing_source = active_aliases.get(material)
            if existing_source is not None and existing_source != source:
                raise RuntimeError(
                    f"Generated material alias '{material}' collides for '{existing_source}' and '{source}'"
                )
            active_aliases[material] = source
            current_materials = record["current"]
            assert isinstance(current_materials, set)
            for current in current_materials:
                if current != material:
                    replacements.setdefault(path, {})[str(current)] = material

        layers = tuple(int(layer) for layer in entries[source]["layers"])
        rows.add((model, material, layers))

    return (
        [(model, material, list(layers)) for model, material, layers in sorted(rows)],
        replacements,
        active_aliases,
    )


def build_model_material_rows(
    entries: dict[str, dict[str, object]],
) -> list[tuple[str, str, list[int]]]:
    rows, _, _ = build_model_material_plan(entries)
    return rows


def find_used_tint_materials(entries: dict[str, dict[str, object]]) -> set[str]:
    models = find_active_models()
    materials = set(entries)
    alias_sources = build_alias_source_lookup(entries)
    used: set[str] = set()
    for path in models.values():
        used.update(find_model_tint_material_references(path, materials, alias_sources).values())

    # Stock resources missing from the HAK source tree conventionally use the
    # same model/material resref; keep their converted source masks available.
    used.update(material for material in materials if material not in models)
    used.update(materials & find_table_referenced_resrefs())
    return used


def tint_directory(model: str) -> Path:
    digest = hashlib.sha256(model.encode("ascii")).digest()
    return TINT_DIRECTORIES[int.from_bytes(digest[:4], "little") % len(TINT_DIRECTORIES)]


def packed_dds_path(model: str, entry: dict[str, object] | None = None) -> Path:
    if entry is not None and entry.get("output"):
        return REPOSITORY_ROOT / str(entry["output"])
    return tint_directory(model) / f"{model}.dds"


def read_plt(path: Path) -> tuple[int, int, np.ndarray, np.ndarray, str]:
    raw = path.read_bytes()
    if len(raw) < PLT_DATA_OFFSET or raw[:8] != PLT_HEADER:
        raise ValueError(f"{path} is not a PLT V1 resource")

    width, height = struct.unpack_from("<II", raw, 16)
    expected_length = PLT_DATA_OFFSET + width * height * 2
    if len(raw) != expected_length:
        raise ValueError(f"{path} has length {len(raw)}, expected {expected_length}")

    pixels = np.frombuffer(raw, dtype=np.uint8, offset=PLT_DATA_OFFSET).reshape(height, width, 2)
    shade = pixels[:, :, 0].copy()
    layer = pixels[:, :, 1].copy()
    invalid_layers = np.unique(layer[layer > 9])
    if invalid_layers.size:
        raise ValueError(f"{path} contains unsupported layer ids: {invalid_layers.tolist()}")

    return width, height, shade, layer, hashlib.sha256(raw).hexdigest()


def encode_layer_ids(layer: np.ndarray) -> np.ndarray:
    # Center each id in one of ten equal shader decoding bins.
    return np.rint((layer.astype(np.float32) + 0.5) * 255.0 / 10.0).astype(np.uint8)


def iter_blocks(channel: np.ndarray) -> np.ndarray:
    height, width = channel.shape
    padded_height = (height + 3) & ~3
    padded_width = (width + 3) & ~3
    padded = np.pad(channel, ((0, padded_height - height), (0, padded_width - width)), mode="edge")
    return padded.reshape(padded_height // 4, 4, padded_width // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16)


def compress_bc4(channel: np.ndarray, batch_size: int = 16_384) -> bytes:
    blocks = iter_blocks(channel)
    output = bytearray(blocks.shape[0] * 8)
    shifts = (np.arange(16, dtype=np.uint64) * 3)[None, :]

    for start in range(0, blocks.shape[0], batch_size):
        values = blocks[start : start + batch_size].astype(np.int16)
        endpoint0 = values.max(axis=1)
        endpoint1 = values.min(axis=1)

        # The endpoint order selects BC4's eight-value interpolation mode.
        same = endpoint0 == endpoint1
        endpoint1 = np.where(same & (endpoint0 > 0), endpoint0 - 1, endpoint1)
        endpoint0 = np.where(same & (endpoint0 == 0), 1, endpoint0)

        palette = np.empty((values.shape[0], 8), dtype=np.int16)
        palette[:, 0] = endpoint0
        palette[:, 1] = endpoint1
        for index in range(1, 7):
            palette[:, index + 1] = ((7 - index) * endpoint0 + index * endpoint1 + 3) // 7

        indices = np.abs(values[:, :, None] - palette[:, None, :]).argmin(axis=2).astype(np.uint64)
        packed_indices = np.bitwise_or.reduce(indices << shifts, axis=1)

        batch = bytearray(values.shape[0] * 8)
        batch[0::8] = endpoint0.astype(np.uint8).tobytes()
        batch[1::8] = endpoint1.astype(np.uint8).tobytes()
        packed_bytes = packed_indices.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
        for byte_index in range(6):
            batch[2 + byte_index :: 8] = packed_bytes[:, byte_index].tobytes()

        offset = start * 8
        output[offset : offset + len(batch)] = batch

    return bytes(output)


def bc4_palette(endpoint0: np.ndarray, endpoint1: np.ndarray) -> np.ndarray:
    endpoint0 = endpoint0.astype(np.float64, copy=False)
    endpoint1 = endpoint1.astype(np.float64, copy=False)
    palette = np.empty((endpoint0.size, 8), dtype=np.float64)
    palette[:, 0] = endpoint0
    palette[:, 1] = endpoint1
    eight_value = endpoint0 > endpoint1
    for index in range(1, 7):
        palette[:, index + 1] = (
            (7 - index) * endpoint0 + index * endpoint1
        ) / 7.0
    for index in range(1, 5):
        palette[~eight_value, index + 1] = (
            (5 - index) * endpoint0[~eight_value] + index * endpoint1[~eight_value]
        ) / 5.0
    palette[~eight_value, 6] = 0.0
    palette[~eight_value, 7] = 255.0
    return palette


def bc4_layer_categories(palette: np.ndarray) -> np.ndarray:
    return np.floor(np.clip(palette / 255.0, 0.0, 0.9999) * 10.0).astype(np.uint8)


def exact_layer_encoding(
    layers: np.ndarray,
    candidate_endpoints0: np.ndarray,
    candidate_endpoints1: np.ndarray,
    candidate_palettes: np.ndarray,
    candidate_categories: np.ndarray,
) -> tuple[int, int, np.ndarray]:
    desired_layers, counts = np.unique(layers, return_counts=True)
    valid = np.ones(candidate_palettes.shape[0], dtype=bool)
    for layer in desired_layers:
        valid &= np.any(candidate_categories == layer, axis=1)
    candidate_indices = np.flatnonzero(valid)
    if candidate_indices.size == 0:
        raise ValueError(
            f"BC4 cannot encode tint layers {desired_layers.tolist()} exactly in one block"
        )

    palettes = candidate_palettes[candidate_indices]
    categories = candidate_categories[candidate_indices]
    score = np.zeros(candidate_indices.size, dtype=np.float64)
    centers = encode_layer_ids(desired_layers)
    for layer, count, center in zip(desired_layers, counts, centers):
        distance = np.where(categories == layer, np.abs(palettes - center), np.inf)
        score += distance.min(axis=1) * count
    best = candidate_indices[int(score.argmin())]

    palette = candidate_palettes[best]
    categories = candidate_categories[best]
    indices = np.empty(16, dtype=np.uint64)
    for layer in desired_layers:
        pixels = layers == layer
        center = encode_layer_ids(np.array([layer], dtype=np.uint8))[0]
        choices = np.flatnonzero(categories == layer)
        selected = choices[int(np.abs(palette[choices] - center).argmin())]
        indices[pixels] = selected

    return int(candidate_endpoints0[best]), int(candidate_endpoints1[best]), indices


def bc4_layer_candidates() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    global _BC4_LAYER_CANDIDATES
    if _BC4_LAYER_CANDIDATES is None:
        endpoint_pairs = np.indices((256, 256), dtype=np.uint16).reshape(2, -1)
        endpoint0 = endpoint_pairs[0]
        endpoint1 = endpoint_pairs[1]
        palettes = bc4_palette(endpoint0, endpoint1)
        _BC4_LAYER_CANDIDATES = (
            endpoint0,
            endpoint1,
            palettes,
            bc4_layer_categories(palettes),
        )
    return _BC4_LAYER_CANDIDATES


def compress_bc4_layers(layer: np.ndarray, batch_size: int = 16_384) -> bytes:
    blocks = iter_blocks(layer)
    output = bytearray(blocks.shape[0] * 8)
    shifts = np.arange(16, dtype=np.uint64) * 3

    (
        candidate_endpoints0,
        candidate_endpoints1,
        candidate_palettes,
        candidate_categories,
    ) = bc4_layer_candidates()

    for start in range(0, blocks.shape[0], batch_size):
        desired = blocks[start : start + batch_size]
        values = encode_layer_ids(desired).astype(np.int16)
        endpoint0 = values.max(axis=1)
        endpoint1 = values.min(axis=1)
        same = endpoint0 == endpoint1
        endpoint1 = np.where(same & (endpoint0 > 0), endpoint0 - 1, endpoint1)
        endpoint0 = np.where(same & (endpoint0 == 0), 1, endpoint0)

        palette = bc4_palette(endpoint0, endpoint1)
        indices = np.abs(values[:, :, None] - palette[:, None, :]).argmin(axis=2).astype(np.uint64)
        decoded_categories = np.take_along_axis(
            bc4_layer_categories(palette), indices.astype(np.int64), axis=1
        )
        invalid_blocks = np.flatnonzero(np.any(decoded_categories != desired, axis=1))
        for block_index in invalid_blocks:
            block_layers = desired[block_index]
            cache_key = tuple(int(value) for value in block_layers)
            if cache_key not in _EXACT_LAYER_ENCODINGS:
                _EXACT_LAYER_ENCODINGS[cache_key] = exact_layer_encoding(
                    block_layers,
                    candidate_endpoints0,
                    candidate_endpoints1,
                    candidate_palettes,
                    candidate_categories,
                )
            exact_endpoint0, exact_endpoint1, exact_indices = _EXACT_LAYER_ENCODINGS[cache_key]
            endpoint0[block_index] = exact_endpoint0
            endpoint1[block_index] = exact_endpoint1
            indices[block_index] = exact_indices

        packed_indices = np.bitwise_or.reduce(indices << shifts[None, :], axis=1)
        batch = bytearray(values.shape[0] * 8)
        batch[0::8] = endpoint0.astype(np.uint8).tobytes()
        batch[1::8] = endpoint1.astype(np.uint8).tobytes()
        packed_bytes = packed_indices.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
        for byte_index in range(6):
            batch[2 + byte_index :: 8] = packed_bytes[:, byte_index].tobytes()

        offset = start * 8
        output[offset : offset + len(batch)] = batch

    return bytes(output)


def dds_header(width: int, height: int, data_length: int) -> bytes:
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x00081007)  # CAPS, HEIGHT, WIDTH, PIXELFORMAT, LINEARSIZE
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, data_length)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 0x00000004)  # DDPF_FOURCC
    header[84:88] = b"ATI2"
    struct.pack_into("<I", header, 108, 0x00001000)  # DDSCAPS_TEXTURE
    return bytes(header)


def write_packed_dds(path: Path, width: int, height: int, shade: np.ndarray, layer: np.ndarray) -> None:
    red = compress_bc4(shade)
    green = compress_bc4_layers(layer)
    block_count = len(red) // 8
    payload = bytearray(block_count * 16)
    for index in range(block_count):
        payload[index * 16 : index * 16 + 8] = red[index * 8 : index * 8 + 8]
        payload[index * 16 + 8 : index * 16 + 16] = green[index * 8 : index * 8 + 8]

    path.write_bytes(dds_header(width, height, len(payload)) + payload)


def mtr_directive_key(line: str) -> tuple[str, ...] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("//"):
        return None
    tokens = stripped.split()
    if tokens[0].lower() == "parameter" and len(tokens) >= 3:
        return ("parameter", tokens[1].lower(), tokens[2].lower())
    return (tokens[0].lower(),)


def merge_mtr_lines(preferred: list[str], fallback: list[str]) -> list[str]:
    merged: list[str] = []
    directive_keys: set[tuple[str, ...]] = set()
    nondirective_lines: set[str] = set()
    for line in preferred + fallback:
        key = mtr_directive_key(line)
        if key is None:
            normalized = line.strip().lower()
            if normalized in nondirective_lines:
                continue
            nondirective_lines.add(normalized)
        elif key in directive_keys:
            continue
        else:
            directive_keys.add(key)
        merged.append(line)
    return merged


def update_mtr(
    path: Path,
    material: str,
    texture: str,
    width: int,
    height: int,
    source_material: str | None = None,
) -> None:
    source_material = source_material or material
    generated_lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    source_paths = source_mtr_paths(source_material)
    source_lines = (
        source_paths[-1].read_text(encoding="utf-8-sig").splitlines()
        if source_paths
        else []
    )
    if not source_lines and source_material != material:
        generated_source = mtr_path(source_material)
        if generated_source.exists():
            source_lines = generated_source.read_text(encoding="utf-8-sig").splitlines()
    original_lines = merge_mtr_lines(source_lines, generated_lines)
    original_fragment_shaders = {
        line.split(maxsplit=1)[1].strip().lower()
        for line in original_lines
        if re.match(r"^\s*customshaderFS\s+\S+", line, re.IGNORECASE)
    }
    uses_texture1_alpha = (
        source_material.lower() in TEXTURE1_ALPHA_MATERIALS
        or bool(original_fragment_shaders & TEXTURE1_ALPHA_SHADERS)
    )
    texture9_alpha = TEXTURE9_ALPHA_MATERIALS.get(source_material.lower())
    replaced = re.compile(
        r"^\s*(?:customshaderFS|texture0|texture7|texture9|texture10|"
        r"parameter\s+float\s+(?:tintMapWidth|tintMapHeight|useTexture1Alpha|useTexture9Alpha))\b",
        re.IGNORECASE,
    )
    lines = [line for line in original_lines if not replaced.match(line)]
    if texture9_alpha:
        lines = [line for line in lines if not re.match(r"^\s*texture3\b", line, re.IGNORECASE)]

    if not any(re.match(r"^\s*customshaderVS\b", line, re.IGNORECASE) for line in lines):
        lines.append("customshaderVS vslit_sm_nm")

    lines.extend(
        (
            "customshaderFS fs_plt_tinter",
            "texture0 plt_white",
            f"texture7 {texture}",
            "texture10 plt_palette",
            f"parameter float tintMapWidth {float(width):.1f}",
            f"parameter float tintMapHeight {float(height):.1f}",
        )
    )
    if uses_texture1_alpha:
        lines.append("parameter float useTexture1Alpha 1.0")
    if texture9_alpha:
        lines.append(f"texture9 {texture9_alpha}")
        lines.append("parameter float useTexture9Alpha 1.0")
    normalized = "\n".join(line.rstrip() for line in lines).strip() + "\n"
    path.write_text(normalized, encoding="utf-8", newline="\n")


def write_white_texture() -> None:
    # Uncompressed one-pixel, 24-bit TGA. BGR pixel order: white.
    WHITE_TEXTURE.write_bytes(white_texture_bytes())


def white_texture_bytes() -> bytes:
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 1, 24, 0x20)
    return header + b"\xff\xff\xff"


def is_generated_pair(model: str, texture: str, width: int, height: int, dds_path: Path) -> bool:
    material_path = mtr_path(model)
    if check_dds(dds_path, width, height) is not None or not material_path.exists():
        return False

    mtr = material_path.read_text(encoding="utf-8-sig").lower()
    return (
        "customshaderfs fs_plt_tinter" in mtr
        and f"texture7 {texture}" in mtr
        and f"parameter float tintmapwidth {float(width):.1f}" in mtr
        and f"parameter float tintmapheight {float(height):.1f}" in mtr
    )


def load_source_manifest() -> dict[str, dict[str, object]]:
    if not SOURCE_MANIFEST.exists():
        return {}
    data = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    return {entry["model"].lower(): entry for entry in data}


def write_source_manifest(entries: dict[str, dict[str, object]]) -> None:
    ordered = [entries[key] for key in sorted(entries)]
    SOURCE_MANIFEST.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8", newline="\n")


def render_2da(entries: dict[str, dict[str, object]]) -> str:
    lines = ["2DA V2.0", "", "   MODEL             MATERIAL          LAYERS"]
    for row, (model, material, layer_values) in enumerate(build_model_material_rows(entries)):
        layers = ",".join(str(value) for value in layer_values)
        lines.append(f"{row:<4} {model:<17} {material:<17} {layers}")
    return "\n".join(lines) + "\n"


def write_2da(entries: dict[str, dict[str, object]]) -> None:
    OUTPUT_2DA.write_text(render_2da(entries), encoding="utf-8", newline="\n")


def remove_duplicate_inventory_icon_plts() -> int:
    active, all_paths = find_inventory_icon_plts()
    retained = {path.resolve() for path in active.values()}
    duplicates = [path for path in all_paths if path.resolve() not in retained]
    for path in duplicates:
        resolved = path.resolve()
        if REPOSITORY_ROOT.resolve() not in resolved.parents or not is_inventory_icon_plt(resolved):
            raise RuntimeError(f"Refusing to delete unexpected inventory icon path: {resolved}")
        resolved.unlink()
    return len(duplicates)


def generate() -> None:
    active, all_paths = find_tint_material_plts()
    outside_source_plts = find_tint_material_plts_outside_sources()
    if outside_source_plts:
        raise RuntimeError(
            f"3D tint material PLTs exist outside configured source directories: {outside_source_plts[:10]}"
        )

    manifest = load_source_manifest()
    candidate_entries = dict(manifest)
    for material in active:
        candidate_entries.setdefault(material, {"aliases": []})
    used_materials = find_used_tint_materials(candidate_entries)
    entries = {
        model: entry
        for model, entry in manifest.items()
        if model in used_materials
    }
    active_materials = {
        model: source_path
        for model, source_path in active.items()
        if model in used_materials
    }
    total = len(active_materials)
    for number, (model, source_path) in enumerate(sorted(active_materials.items()), start=1):
        width, height, shade, layer, source_hash = read_plt(source_path)
        layers = [int(value) for value in np.unique(layer)]
        relative_source = source_path.relative_to(REPOSITORY_ROOT).as_posix()

        dds_path = packed_dds_path(model)
        dds_path.parent.mkdir(exist_ok=True)
        existing_entry = entries.get(model)
        source_changed = (
            existing_entry is None
            or str(existing_entry.get("sourceSha256", "")) != source_hash
        )
        if source_changed or not is_generated_pair(model, model, width, height, dds_path):
            write_packed_dds(dds_path, width, height, shade, layer)
            update_mtr(mtr_path(model), model, model, width, height)
        entries[model] = {
            "model": model,
            "material": model,
            "aliases": list(existing_entry.get("aliases", [])) if existing_entry else [],
            "layers": layers,
            "width": width,
            "height": height,
            "source": relative_source,
            "sourceSha256": source_hash,
            "layerSha256": hashlib.sha256(layer.tobytes()).hexdigest(),
            "output": dds_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "texture": model,
        }

        if number == 1 or number % 100 == 0 or number == total:
            print(f"Converted {number}/{total}: {model}", flush=True)

    deduplicate_assets(entries)
    changed_models, material_aliases = synchronize_model_material_aliases(entries)
    orphaned_outputs = remove_orphaned_outputs(entries)
    orphaned_materials = remove_orphaned_materials(entries, set(material_aliases))
    overridden_materials = remove_overridden_materials(entries)
    write_white_texture()
    write_source_manifest(entries)
    write_2da(entries)

    # Delete only exact, validated 3D material PLTs under known HAK roots. Dynamic
    # inventory icon PLTs are an engine requirement and are handled separately.
    for path in all_paths:
        resolved = path.resolve()
        if REPOSITORY_ROOT.resolve() not in resolved.parents or not is_tint_material_plt(resolved):
            raise RuntimeError(f"Refusing to delete unexpected path: {resolved}")
        resolved.unlink()

    duplicate_icons = remove_duplicate_inventory_icon_plts()
    print(
        f"Generated {len(active_materials)} referenced materials, discarded "
        f"{len(active) - len(active_materials)} unreferenced masks, and removed "
        f"{len(all_paths)} 3D material PLTs plus {duplicate_icons} lower-priority "
        f"inventory icon duplicates, {orphaned_outputs} orphaned packed textures, "
        f"{orphaned_materials} orphaned materials, and {overridden_materials} "
        f"superseded source materials; isolated materials in {changed_models} models.",
        flush=True,
    )


def deduplicate_assets(entries: dict[str, dict[str, object]]) -> None:
    groups: dict[tuple[str, int, int], list[dict[str, object]]] = {}
    for entry in entries.values():
        key = (str(entry["sourceSha256"]), int(entry["width"]), int(entry["height"]))
        groups.setdefault(key, []).append(entry)

    old_outputs = {
        (REPOSITORY_ROOT / str(entry["output"])).resolve()
        for entry in entries.values()
        if entry.get("output")
    }
    retained_outputs: set[Path] = set()

    for group_entries in groups.values():
        group_entries.sort(key=lambda value: str(value["model"]))
        canonical = str(group_entries[0]["model"])
        current_path = packed_dds_path(canonical, group_entries[0])
        target_path = tint_directory(canonical) / f"{canonical}.dds"
        target_path.parent.mkdir(exist_ok=True)
        if current_path.resolve() != target_path.resolve():
            if not current_path.exists():
                raise RuntimeError(f"Missing canonical packed DDS: {current_path}")
            current_path.replace(target_path)

        retained_outputs.add(target_path.resolve())
        relative_output = target_path.relative_to(REPOSITORY_ROOT).as_posix()
        for entry in group_entries:
            model = str(entry["model"])
            entry["texture"] = canonical
            entry["output"] = relative_output
            update_mtr(
                mtr_path(model),
                model,
                canonical,
                int(entry["width"]),
                int(entry["height"]),
            )

    for old_output in old_outputs - retained_outputs:
        if old_output.exists():
            if not any(directory.resolve() in old_output.parents for directory in TINT_DIRECTORIES):
                raise RuntimeError(f"Refusing to delete non-tint output: {old_output}")
            old_output.unlink()


def synchronize_model_material_aliases(
    entries: dict[str, dict[str, object]],
) -> tuple[int, dict[str, str]]:
    _, replacements, planned_aliases = build_model_material_plan(entries)
    changed_models = 0
    for path, path_replacements in sorted(replacements.items(), key=lambda value: str(value[0])):
        try:
            if replace_model_materials(path, path_replacements):
                changed_models += 1
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"Cannot isolate tint materials in {path}: {exc}") from exc

    aliases_by_source: dict[str, list[str]] = {source: [] for source in entries}
    for alias, source in planned_aliases.items():
        aliases_by_source[source].append(alias)
    for source, entry in entries.items():
        aliases = sorted(aliases_by_source[source])
        if aliases:
            entry["aliases"] = aliases
        else:
            entry.pop("aliases", None)

    _, remaining_replacements, active_aliases = build_model_material_plan(entries)
    if remaining_replacements:
        examples = [str(path) for path in list(remaining_replacements)[:10]]
        raise RuntimeError(f"Tint material aliases did not synchronize for: {examples}")

    aliases_by_source = {source: [] for source in entries}
    for alias, source in active_aliases.items():
        aliases_by_source[source].append(alias)
    for source, entry in entries.items():
        aliases = sorted(aliases_by_source[source])
        if aliases:
            entry["aliases"] = aliases
        else:
            entry.pop("aliases", None)

    for alias, source in sorted(active_aliases.items()):
        entry = entries[source]
        update_mtr(
            mtr_path(alias),
            alias,
            str(entry.get("texture") or source),
            int(entry["width"]),
            int(entry["height"]),
            source_material=source,
        )

    return changed_models, active_aliases


def remove_orphaned_outputs(entries: dict[str, dict[str, object]]) -> int:
    expected_outputs = {
        packed_dds_path(material, entry).resolve()
        for material, entry in entries.items()
    }
    removed_outputs = 0
    for directory in TINT_DIRECTORIES:
        for path in directory.glob("*.dds"):
            if path.resolve() not in expected_outputs:
                path.unlink()
                removed_outputs += 1
    return removed_outputs


def remove_orphaned_materials(
    entries: dict[str, dict[str, object]],
    aliases: set[str] | None = None,
) -> int:
    expected_materials = {
        mtr_path(material).resolve()
        for material in set(entries) | (aliases or set())
    }
    removed_materials = 0
    for path in OUTPUT_MTR_DIRECTORY.glob("*.mtr"):
        if path.resolve() not in expected_materials:
            path.unlink()
            removed_materials += 1
    return removed_materials


def remove_overridden_materials(entries: dict[str, dict[str, object]]) -> int:
    removed_materials = 0
    for material in entries:
        for path in source_mtr_paths(material):
            resolved = path.resolve()
            if REPOSITORY_ROOT.resolve() not in resolved.parents or resolved.suffix.lower() != ".mtr":
                raise RuntimeError(f"Refusing to delete unexpected material path: {resolved}")
            resolved.unlink()
            removed_materials += 1
    return removed_materials


def deduplicate() -> None:
    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to deduplicate")

    before = len({str(entry.get("output")) for entry in entries.values()})
    deduplicate_assets(entries)
    changed_models, material_aliases = synchronize_model_material_aliases(entries)
    remove_orphaned_materials(entries, set(material_aliases))
    write_source_manifest(entries)
    write_2da(entries)
    after = len({str(entry["output"]) for entry in entries.values()})
    print(
        f"Deduplicated {len(entries)} materials from {before} to {after} packed textures "
        f"and isolated materials in {changed_models} models."
    )


def prune() -> None:
    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to prune")

    used_materials = find_used_tint_materials(entries)
    removed_materials = set(entries) - used_materials
    if not removed_materials:
        deduplicate_assets(entries)
        changed_models, material_aliases = synchronize_model_material_aliases(entries)
        remove_orphaned_materials(entries, set(material_aliases))
        write_source_manifest(entries)
        write_2da(entries)
        print(
            "No additional unreferenced tint materials were found; generated assets were "
            f"synchronized and {changed_models} models received isolated materials."
        )
        return

    retained_entries = {
        material: entry
        for material, entry in entries.items()
        if material in used_materials
    }
    deduplicate_assets(retained_entries)
    changed_models, material_aliases = synchronize_model_material_aliases(retained_entries)
    removed_outputs = remove_orphaned_outputs(retained_entries)
    removed_mtrs = remove_orphaned_materials(retained_entries, set(material_aliases))
    removed_source_mtrs = remove_overridden_materials(retained_entries)

    write_source_manifest(retained_entries)
    write_2da(retained_entries)
    print(
        f"Pruned {len(removed_materials)} unreferenced materials and "
        f"{removed_outputs} packed textures plus {removed_mtrs} material files and "
        f"{removed_source_mtrs} superseded source materials; isolated materials in "
        f"{changed_models} models."
    )


def relocate() -> None:
    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to relocate")

    for directory in TINT_DIRECTORIES:
        directory.mkdir(exist_ok=True)

    relocated: set[Path] = set()
    for number, (model, entry) in enumerate(sorted(entries.items()), start=1):
        texture = str(entry.get("texture") or model)
        current_path = REPOSITORY_ROOT / str(
            entry.get("output") or f"sw_tint0/{texture}.dds"
        )
        target_path = tint_directory(texture) / f"{texture}.dds"
        if target_path.resolve() not in relocated and current_path.resolve() != target_path.resolve():
            if not current_path.exists():
                raise RuntimeError(f"Missing packed DDS for relocation: {current_path}")
            current_path.replace(target_path)

        relocated.add(target_path.resolve())
        entry["output"] = target_path.relative_to(REPOSITORY_ROOT).as_posix()
        if number % 500 == 0 or number == len(entries):
            print(f"Relocated {number}/{len(entries)}", flush=True)

    synchronize_model_material_aliases(entries)
    write_source_manifest(entries)
    write_2da(entries)
    print("Packed tint maps were split across dedicated tint HAK directories.", flush=True)


def check_dds(path: Path, width: int, height: int) -> str | None:
    if not path.exists():
        return "missing DDS"
    raw = path.read_bytes()
    blocks_wide = (width + 3) // 4
    blocks_high = (height + 3) // 4
    expected = 128 + blocks_wide * blocks_high * 16
    if len(raw) != expected:
        return f"DDS length {len(raw)} != {expected}"
    if raw[:4] != b"DDS " or raw[84:88] != b"ATI2":
        return "DDS is not ATI2/BC5"
    actual_height, actual_width = struct.unpack_from("<II", raw, 12)
    if (actual_width, actual_height) != (width, height):
        return f"DDS dimensions {(actual_width, actual_height)} != {(width, height)}"
    return None


def decode_dds_layers(path: Path, width: int, height: int) -> np.ndarray:
    raw = path.read_bytes()
    blocks_wide = (width + 3) // 4
    blocks_high = (height + 3) // 4
    block_count = blocks_wide * blocks_high
    packed = np.frombuffer(raw, dtype=np.uint8, offset=128).reshape(block_count, 16)[:, 8:16]
    endpoint0 = packed[:, 0]
    endpoint1 = packed[:, 1]
    palette = bc4_palette(endpoint0, endpoint1)
    bits = np.zeros(block_count, dtype=np.uint64)
    for byte_index in range(6):
        bits |= packed[:, byte_index + 2].astype(np.uint64) << (byte_index * 8)
    indices = ((bits[:, None] >> (np.arange(16, dtype=np.uint64) * 3)) & 7).astype(np.int64)
    decoded = np.take_along_axis(palette, indices, axis=1)
    categories = bc4_layer_categories(decoded)
    image = categories.reshape(blocks_high, blocks_wide, 4, 4).transpose(0, 2, 1, 3)
    return image.reshape(blocks_high * 4, blocks_wide * 4)[:height, :width]


def check_tga_header(path: Path, width: int, height: int) -> str | None:
    if not path.exists():
        return "missing TGA"
    raw = path.read_bytes()
    if len(raw) < 18:
        return "truncated TGA"
    image_type = raw[2]
    actual_width, actual_height = struct.unpack_from("<HH", raw, 12)
    if image_type not in (2, 10) or raw[1] != 0 or raw[16] != 24:
        return "TGA must be 24-bit true-color without a color map"
    if (actual_width, actual_height) != (width, height):
        return f"TGA dimensions {(actual_width, actual_height)} != {(width, height)}"
    return None


def audit() -> None:
    entries = load_source_manifest()
    errors: list[str] = []
    if not entries:
        errors.append("tint source manifest is empty")

    model_material_rows: list[tuple[str, str, list[int]]] = []
    pending_model_replacements: dict[Path, dict[str, str]] = {}
    active_aliases: dict[str, str] = {}
    if entries:
        model_material_rows, pending_model_replacements, active_aliases = build_model_material_plan(entries)
        manifest_aliases = build_alias_source_lookup(entries)
        if manifest_aliases != active_aliases:
            errors.append(
                "tint source manifest aliases do not exactly match the scoped materials used by active models"
            )
        if pending_model_replacements:
            examples = ", ".join(
                str(path.relative_to(REPOSITORY_ROOT))
                for path in list(pending_model_replacements)[:10]
            )
            errors.append(
                f"{len(pending_model_replacements)} models do not isolate shared tint materials: {examples}"
            )

    _, remaining_material_plts = find_tint_material_plts()
    if remaining_material_plts:
        errors.append(f"{len(remaining_material_plts)} 3D tint material PLTs remain")
    outside_source_plts = find_tint_material_plts_outside_sources()
    if outside_source_plts:
        errors.append(f"{len(outside_source_plts)} 3D tint material PLTs exist outside configured sources")
    active_icon_plts, all_icon_plts = find_inventory_icon_plts()
    duplicate_icon_count = len(all_icon_plts) - len(active_icon_plts)
    if duplicate_icon_count:
        errors.append(f"{duplicate_icon_count} lower-priority inventory icon PLT duplicates remain")

    decoded_layer_hashes: dict[tuple[Path, int, int], str] = {}
    for model, entry in sorted(entries.items()):
        if model != str(entry.get("model", "")).lower() or str(entry.get("material", "")).lower() != model:
            errors.append(f"{model}: model/material manifest keys disagree")
        if len(model) > 16 or not model.isascii():
            errors.append(f"{model}: invalid NWN resref")
        layers = entry.get("layers")
        if not isinstance(layers, list) or not layers or layers != sorted(set(layers)):
            errors.append(f"{model}: layers must be a non-empty sorted unique list")
        elif any(not isinstance(layer, int) or layer < 0 or layer >= len(LAYER_NAMES) for layer in layers):
            errors.append(f"{model}: invalid tint layer id")

        width = int(entry["width"])
        height = int(entry["height"])
        dds_path = packed_dds_path(model, entry)
        if not any(directory.resolve() in dds_path.resolve().parents for directory in TINT_DIRECTORIES):
            errors.append(f"{model}: packed DDS is outside tint HAK directories")
        dds_error = check_dds(dds_path, width, height)
        if dds_error:
            errors.append(f"{model}: {dds_error}")
        else:
            expected_layer_hash = entry.get("layerSha256")
            if not expected_layer_hash:
                errors.append(f"{model}: missing decoded layer checksum")
            else:
                layer_hash_key = (dds_path.resolve(), width, height)
                if layer_hash_key not in decoded_layer_hashes:
                    decoded_layer_hashes[layer_hash_key] = hashlib.sha256(
                        decode_dds_layers(dds_path, width, height).tobytes()
                    ).hexdigest()
                if decoded_layer_hashes[layer_hash_key] != expected_layer_hash:
                    errors.append(f"{model}: packed DDS changes one or more categorical tint layers")

        material_path = mtr_path(model)
        if not material_path.exists():
            errors.append(f"{model}: missing MTR")
        else:
            mtr = material_path.read_text(encoding="utf-8-sig").lower()
            texture = str(entry.get("texture") or model)
            required = (
                "customshadervs ",
                "customshaderfs fs_plt_tinter",
                "texture0 plt_white",
                f"texture7 {texture}",
                "texture10 plt_palette",
                f"parameter float tintmapwidth {float(width):.1f}",
                f"parameter float tintmapheight {float(height):.1f}",
            )
            for line in required:
                if line not in mtr:
                    errors.append(f"{model}: MTR missing '{line}'")
            if model in TEXTURE1_ALPHA_MATERIALS and "parameter float usetexture1alpha 1.0" not in mtr:
                errors.append(f"{model}: MTR lost required texture-alpha compatibility")
            if model in TEXTURE9_ALPHA_MATERIALS:
                alpha_texture = TEXTURE9_ALPHA_MATERIALS[model]
                if f"texture9 {alpha_texture}" not in mtr or "parameter float usetexture9alpha 1.0" not in mtr:
                    errors.append(f"{model}: MTR lost required dedicated alpha-map compatibility")

    for alias, source in sorted(active_aliases.items()):
        if len(alias) > 16 or not alias.isascii():
            errors.append(f"{source}: invalid scoped material alias '{alias}'")
            continue
        entry = entries[source]
        material_path = mtr_path(alias)
        if not material_path.exists():
            errors.append(f"{alias}: missing scoped MTR for '{source}'")
            continue
        mtr = material_path.read_text(encoding="utf-8-sig").lower()
        texture = str(entry.get("texture") or source)
        required = (
            "customshadervs ",
            "customshaderfs fs_plt_tinter",
            "texture0 plt_white",
            f"texture7 {texture}",
            "texture10 plt_palette",
            f"parameter float tintmapwidth {float(entry['width']):.1f}",
            f"parameter float tintmapheight {float(entry['height']):.1f}",
        )
        for line in required:
            if line not in mtr:
                errors.append(f"{alias}: scoped MTR missing '{line}'")
        if source in TEXTURE1_ALPHA_MATERIALS and "parameter float usetexture1alpha 1.0" not in mtr:
            errors.append(f"{alias}: scoped MTR lost required texture-alpha compatibility")
        if source in TEXTURE9_ALPHA_MATERIALS:
            alpha_texture = TEXTURE9_ALPHA_MATERIALS[source]
            if f"texture9 {alpha_texture}" not in mtr or "parameter float usetexture9alpha 1.0" not in mtr:
                errors.append(f"{alias}: scoped MTR lost required dedicated alpha-map compatibility")

    if not model_material_rows:
        errors.append("model/material catalog is empty")
    for model, material, layers in model_material_rows:
        if len(model) > 16 or not model.isascii():
            errors.append(f"{model}: invalid model resref in tint catalog")
        if len(material) > 16 or not material.isascii():
            errors.append(f"{model}: invalid material resref '{material}' in tint catalog")
        source = active_aliases.get(material, material)
        if source not in entries:
            errors.append(f"{model}: tint catalog references unknown material '{material}'")
        elif layers != entries[source]["layers"]:
            errors.append(f"{model}: tint catalog layer list disagrees with material '{material}'")
    if not OUTPUT_2DA.exists():
        errors.append("missing tintmap.2da")
    elif OUTPUT_2DA.read_text(encoding="utf-8") != render_2da(entries):
        errors.append("tintmap.2da does not exactly match the source manifest")

    if not WHITE_TEXTURE.exists() or WHITE_TEXTURE.read_bytes() != white_texture_bytes():
        errors.append("plt_white.tga is missing or invalid")
    palette_error = check_tga_header(PALETTE_TEXTURE, 256, 1024)
    if palette_error:
        errors.append(f"plt_palette.tga: {palette_error}")
    if not PALETTE_TXI.exists() or "mipmap 0" not in PALETTE_TXI.read_text(encoding="utf-8").lower():
        errors.append("plt_palette.txi must disable mipmaps")
    if not TINT_SHADER.exists():
        errors.append("missing tint fragment shader")
    else:
        shader = TINT_SHADER.read_text(encoding="utf-8")
        for token in (
            "#define SELF_ILLUMINATION_MAP 1",
            "uniform sampler2D texUnit7",
            "uniform sampler2D texUnit9",
            "uniform sampler2D texUnit10",
            "uniform float rowSkin",
            "float paletteU = (g * 255.0 + 0.5) / 256.0",
            "float outputAlpha = materialFrontDiffuse.a",
        ):
            if token not in shader:
                errors.append(f"tint fragment shader missing '{token}'")

    expected_outputs = {packed_dds_path(model, entry).resolve() for model, entry in entries.items()}
    actual_outputs = {
        path.resolve()
        for directory in TINT_DIRECTORIES
        for path in directory.glob("*.dds")
    }
    unexpected_outputs = actual_outputs - expected_outputs
    if unexpected_outputs:
        errors.append(f"{len(unexpected_outputs)} orphaned packed DDS resources remain")

    expected_materials = {
        mtr_path(material).resolve()
        for material in set(entries) | set(active_aliases)
    }
    actual_materials = {path.resolve() for path in OUTPUT_MTR_DIRECTORY.glob("*.mtr")}
    unexpected_materials = actual_materials - expected_materials
    if unexpected_materials:
        errors.append(f"{len(unexpected_materials)} orphaned MTR resources remain")

    overridden_materials = sum(len(source_mtr_paths(material)) for material in entries)
    if overridden_materials:
        errors.append(f"{overridden_materials} superseded source MTR resources remain")

    if errors:
        for error in errors[:100]:
            print(f"ERROR: {error}", file=sys.stderr)
        if len(errors) > 100:
            print(f"ERROR: {len(errors) - 100} additional errors", file=sys.stderr)
        raise SystemExit(1)

    print(
        f"Tint map audit passed: {len(entries)} materials, {len(model_material_rows)} model/material rows, "
        f"no 3D material PLTs, and {len(active_icon_plts)} required dynamic inventory icon PLTs remain."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--generate", action="store_true", help="convert and remove 3D material PLTs")
    action.add_argument("--relocate", action="store_true", help="split existing maps across tint HAKs")
    action.add_argument("--deduplicate", action="store_true", help="share byte-identical packed maps")
    action.add_argument("--prune", action="store_true", help="remove masks not referenced by active models")
    action.add_argument("--check", action="store_true", help="audit generated assets and PLT coverage")
    arguments = parser.parse_args()

    require_repository_root()
    if arguments.generate:
        generate()
    elif arguments.relocate:
        relocate()
    elif arguments.deduplicate:
        deduplicate()
    elif arguments.prune:
        prune()
    else:
        audit()


if __name__ == "__main__":
    main()
