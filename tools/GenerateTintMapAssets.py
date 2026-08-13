#!/usr/bin/env python3
"""Convert 3D model PLTs to compact, shader-driven BC5 tint maps.

The packed DDS keeps the PLT shade in red and its layer id in green. It uses a
content-addressed internal resref so the shader mask cannot collide with a
legacy diffuse texture that shares the original PLT resref. The generated
tintmap.2da is the authoritative
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
MODULAR_FALLBACKS = Path(__file__).with_name("TintMapFallbacks.json")
WHITE_TEXTURE = REPOSITORY_ROOT / "sw_item" / "plt_white.tga"
PALETTE_TEXTURE = REPOSITORY_ROOT / "sw_item" / "plt_palette.tga"
PALETTE_TXI = REPOSITORY_ROOT / "sw_item" / "plt_palette.txi"
TINT_SHADER = REPOSITORY_ROOT / "sw_shader" / "fs_plt_tinter.shd"
TINT_MAPPED_SHADER = REPOSITORY_ROOT / "sw_shader" / "fs_plt_tinter_nm.shd"
TINT_FRAGMENT_SHADER = "fs_plt_tinter"
TINT_MAPPED_FRAGMENT_SHADER = "fs_plt_tinter_nm"
_MTR_PATHS_BY_RESREF: dict[str, Path] | None = None
_SOURCE_MTR_PATHS_BY_RESREF: dict[str, list[Path]] | None = None
_BC4_LAYER_CANDIDATES: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
_EXACT_LAYER_ENCODINGS: dict[tuple[int, ...], tuple[int, int, np.ndarray]] = {}
_ACTIVE_MODELS: dict[str, Path] | None = None
_ACTIVE_RENDER_SURFACES: set[str] | None = None
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
PALETTE_TEXTURE_HEIGHT = 2048
TINT_ROW_PARAMETERS = (
    ("rowSkin", 0),
    ("rowHair", 176),
    ("rowMetal1", 352),
    ("rowMetal2", 528),
    ("rowCloth1", 704),
    ("rowCloth2", 704),
    ("rowLeath1", 880),
    ("rowLeath2", 880),
    ("rowTat1", 1056),
    ("rowTat2", 1056),
)
TINT_ROW_PARAMETER_LINES = tuple(
    f"parameter float {uniform_name} "
    f"{(base_row + 0.5) / PALETTE_TEXTURE_HEIGHT:.6f} 0.0 0.0 0.0"
    for uniform_name, base_row in TINT_ROW_PARAMETERS
)
TINT_COLOR_PARAMETER_BASES = (
    "tintSkin",
    "tintHair",
    "tintMetal1",
    "tintMetal2",
    "tintCloth1",
    "tintCloth2",
    "tintLeath1",
    "tintLeath2",
    "tintTat1",
    "tintTat2",
)
TINT_LEGACY_COLOR_PARAMETERS = TINT_COLOR_PARAMETER_BASES
TINT_LEGACY_COLOR_PARAMETER_LINES = tuple(
    f"parameter float {uniform_name} 0.0 0.0 0.0 0.0"
    for uniform_name in TINT_LEGACY_COLOR_PARAMETERS
)
TINT_COLOR_PARAMETERS = tuple(
    f"{uniform_name}{component}"
    for uniform_name in TINT_COLOR_PARAMETER_BASES
    for component in ("R", "G", "B")
)
TINT_COLOR_PARAMETER_LINES = tuple(
    f"parameter float {uniform_name} 0.0"
    for uniform_name in TINT_COLOR_PARAMETERS
)
TINT_CUSTOM_MODE_PARAMETERS = (
    "useCustomSkin",
    "useCustomHair",
    "useCustomMetal1",
    "useCustomMetal2",
    "useCustomCloth1",
    "useCustomCloth2",
    "useCustomLeath1",
    "useCustomLeath2",
    "useCustomTat1",
    "useCustomTat2",
)
TINT_CUSTOM_MODE_PARAMETER_LINES = tuple(
    f"parameter float {uniform_name} 0.0"
    for uniform_name in TINT_CUSTOM_MODE_PARAMETERS
)
TEXTURE1_ALPHA_SHADERS = {"fs_plt_hair", "pfh0_neck199", "pmh0_neck199"}
TEXTURE1_ALPHA_MATERIALS = {"pfh0_neck199", "pmh0_head248", "pmh0_neck199"}
TEXTURE9_ALPHA_MATERIALS = {
    "pfh0_head232": "pfh0_head232_a",
    "pmh0_head231": "pmh0_head231_a",
}
# Aurora's modular body-part path selects a same-name PLT when one exists even
# when the compiled mesh carries a stale or placeholder bitmap. Treating only
# the embedded name as authoritative discarded live armor masks such as the
# pmh0_leg[lr]243 pair (whose meshes say ``spodnie``).
MODULAR_PART_DIRECTORY_PREFIX = "sw_pt_"
MODULAR_MESH_NODE_TYPES = {"animmesh", "danglymesh", "skin", "trimesh"}
MODULAR_MODEL_PATTERN = re.compile(
    r"^p(?P<gender>[fm])(?P<race>[a-z])(?P<phenotype>[0-9])_(?P<part>[a-z]+[0-9]{3})$",
    re.IGNORECASE,
)

FILE_HEADER_SIZE = 12
NODE_HEADER_SIZE = 112
LIGHT_HEADER_SIZE = 92
EMITTER_HEADER_SIZE = 212
REFERENCE_HEADER_SIZE = 68
MESH_TEXTURE0_OFFSET = 120
MESH_MATERIAL_NAME_OFFSET = 312


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


def find_active_render_surfaces() -> set[str]:
    """Return texture/material resrefs that can render without a tint fallback."""
    global _ACTIVE_RENDER_SURFACES
    if _ACTIVE_RENDER_SURFACES is not None:
        return _ACTIVE_RENDER_SURFACES

    surfaces: set[str] = set()
    generated_mtr_directory = OUTPUT_MTR_DIRECTORY.resolve()
    for directory in hak_directories():
        for suffix in ("*.dds", "*.tga"):
            surfaces.update(path.stem.lower() for path in directory.glob(suffix))
        # Material PLTs are generator inputs, not authored surfaces that remain
        # available at runtime. Counting them here prevents the generator from
        # binding the MTR that replaces the PLT before deleting the source.
        surfaces.update(
            path.stem.lower()
            for path in directory.glob("*.plt")
            if not is_tint_material_plt(path)
        )
        if directory.resolve() != generated_mtr_directory:
            surfaces.update(path.stem.lower() for path in directory.glob("*.mtr"))

    _ACTIVE_RENDER_SURFACES = surfaces
    return _ACTIVE_RENDER_SURFACES


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


def read_binary_model_material_fields(
    path: Path,
    data: bytes,
) -> list[tuple[int, str, int, str | None]]:
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
    materials: list[tuple[int, str, int, str | None]] = []
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
        texture_offset = mesh + MESH_TEXTURE0_OFFSET
        texture = read_resref(texture_offset, 64)
        material_offset = mesh + MESH_MATERIAL_NAME_OFFSET
        material = read_resref(material_offset, 64)
        # Segmented body-part models conventionally leave bitmap NULL and let the
        # engine bind the same-resref PLT selected for that part. Treat the model
        # resref as the logical surface in that case so conversion keeps the PLT
        # and writes the generated MTR into the otherwise-empty material slot.
        logical_texture = texture if texture and texture != "null" else path.stem.lower()
        materials.append(
            (
                texture_offset,
                logical_texture,
                material_offset,
                material if material and material != "null" else None,
            )
        )

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
        if re.search(r"(?im)^\s*(?:bitmap|texture0)\s+null(?:\s|$)", text):
            materials.add(path.stem.lower())
        return sorted(materials)

    return sorted(
        {
            texture
            for _, texture, _, _ in read_binary_model_material_fields(path, data)
        }
    )


def read_model_material_bindings(
    path: Path,
    include_implicit_modular_surface: bool = False,
) -> list[tuple[str, str | None]]:
    data = path.read_bytes()
    is_binary = len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == 0
    if is_binary:
        return [
            (texture, material)
            for _, texture, _, material in read_binary_model_material_fields(path, data)
        ]

    text = data.decode("ascii", errors="strict")
    bindings: list[tuple[str, str | None]] = []
    for node_match in re.finditer(
        r"(?ims)^\s*node\s+(?P<type>[^\s]+)[^\r\n]*\r?\n.*?^\s*endnode\b[^\r\n]*(?:\r?\n|$)",
        text,
    ):
        node = node_match.group(0)
        texture_match = re.search(
            r"(?im)^\s*(?:bitmap|texture0)\s+([^\s#]+)",
            node,
        )
        if texture_match is None:
            if (
                not include_implicit_modular_surface
                or not path.parent.name.lower().startswith(MODULAR_PART_DIRECTORY_PREFIX)
                or node_match.group("type").lower() not in MODULAR_MESH_NODE_TYPES
            ):
                continue
            logical_texture = path.stem.lower()
        else:
            texture = texture_match.group(1).lower()
            logical_texture = texture if texture != "null" else path.stem.lower()
        material_match = re.search(
            r"(?im)^\s*materialname\s+([^\s#]+)",
            node,
        )
        material = material_match.group(1).lower() if material_match else None
        bindings.append((logical_texture, material if material != "null" else None))
    return bindings


def pending_model_material_bindings(
    path: Path,
    desired: dict[str, str],
    include_implicit_modular_surface: bool = False,
) -> dict[str, str]:
    pending: dict[str, str] = {}
    try:
        bindings = read_model_material_bindings(
            path,
            include_implicit_modular_surface,
        )
    except (UnicodeDecodeError, ValueError):
        # Match the conservative discovery fallback used above. A handful of
        # legacy robe helpers carry malformed or nonstandard compiled headers;
        # their raw candidate strings keep source masks alive, but they cannot
        # be rewritten safely without a trustworthy mesh boundary.
        return pending
    for texture, material in bindings:
        target = desired.get(texture)
        if target is not None and material != target:
            pending[texture] = target
    return pending


def synchronize_model_material_bindings(path: Path, bindings: dict[str, str]) -> bool:
    normalized = {
        source.lower(): target.lower()
        for source, target in bindings.items()
    }
    if not normalized:
        return False

    data = path.read_bytes()
    is_binary = len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == 0
    if not is_binary:
        # Decode the bytes directly rather than using Path.read_text(). Python's
        # universal-newline handling otherwise rewrites LF-only legacy models as
        # CRLF on Windows. The stock Toolset accepts both forms, but changing
        # every line in a model makes the generated patch needlessly invasive
        # and prevents a byte-level audit from proving that only material
        # references changed.
        text = data.decode("ascii", errors="strict")
        changed = False

        def synchronize_node(match: re.Match[str]) -> str:
            nonlocal changed
            node = match.group(0)
            texture_match = re.search(
                r"(?im)^(?P<indent>\s*)(?:bitmap|texture0)\s+"
                r"(?P<texture>[^\s#]+)[^\r\n]*(?P<newline>\r?\n|$)",
                node,
            )
            if texture_match is None:
                node_type_match = re.match(
                    r"(?im)^\s*node\s+(?P<type>[^\s]+)[^\r\n]*(?P<newline>\r?\n|$)",
                    node,
                )
                if (
                    node_type_match is None
                    or not path.parent.name.lower().startswith(MODULAR_PART_DIRECTORY_PREFIX)
                    or node_type_match.group("type").lower() not in MODULAR_MESH_NODE_TYPES
                ):
                    return node
                source = path.stem.lower()
            else:
                texture = texture_match.group("texture").lower()
                source = texture if texture != "null" else path.stem.lower()
            target = normalized.get(source)
            if target is None:
                return node

            material_match = re.search(
                r"(?im)^(?P<prefix>\s*materialname\s+)(?P<material>[^\s#]+)",
                node,
            )
            if material_match is not None:
                if material_match.group("material").lower() == target:
                    return node
                changed = True
                return (
                    node[: material_match.start("material")]
                    + target
                    + node[material_match.end("material") :]
                )

            if texture_match is not None:
                newline = texture_match.group("newline") or "\n"
                insertion_point = texture_match.end()
                indent = texture_match.group("indent")
            else:
                assert node_type_match is not None
                newline = node_type_match.group("newline") or "\n"
                insertion_point = node_type_match.end()
                indent = "  "
            material_line = f"{indent}materialname {target}{newline}"
            changed = True
            return node[:insertion_point] + material_line + node[insertion_point:]

        updated = re.sub(
            r"(?ims)^\s*node\s+[^\r\n]+\r?\n.*?^\s*endnode\b[^\r\n]*(?:\r?\n|$)",
            synchronize_node,
            text,
        )
        if changed:
            path.write_bytes(updated.encode("ascii"))
        return changed

    updated = bytearray(data)
    changed = False
    for _, source, material_offset, material in read_binary_model_material_fields(path, data):
        target = normalized.get(source)
        if target is None or material == target:
            continue
        encoded = target.encode("ascii")
        if len(encoded) > 16:
            raise ValueError(f"{path}: generated material resref is too long: {target}")
        updated[material_offset : material_offset + 64] = encoded.ljust(64, b"\0")
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
        same_name_material = path.stem.lower()
        has_same_name_modular_material = (
            path.parent.name.lower().startswith(MODULAR_PART_DIRECTORY_PREFIX)
            and same_name_material in materials
        )
        current_bindings = read_model_material_bindings(
            path,
            include_implicit_modular_surface=has_same_name_modular_material,
        )
        if has_same_name_modular_material:
            render_surfaces = find_active_render_surfaces()
            references: dict[str, str] = {}
            for texture, current_material in current_bindings:
                if current_material in materials or current_material in alias_sources:
                    references[texture] = alias_sources.get(
                        current_material,
                        current_material,
                    )
                elif current_material is None and texture not in render_surfaces:
                    # Aurora's segmented-body loader falls back to the part's
                    # same-resref PLT when an embedded bitmap is a stale export
                    # label. Do not apply that fallback when the bitmap resolves
                    # to a real authored DDS/TGA/PLT/MTR: doing so replaces valid
                    # equipment textures with the tint mask.
                    references[texture] = same_name_material
            return references

        current_materials = {texture for texture, _ in current_bindings}
        return {
            material: alias_sources.get(material, material)
            for material in current_materials
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


def find_generated_materials_shadowing_authored_surfaces(
    entries: dict[str, dict[str, object]],
) -> list[tuple[str, str, str]]:
    generated_materials = set(entries) | set(build_alias_source_lookup(entries))
    render_surfaces = find_active_render_surfaces()
    explicit_overrides = load_authored_texture_overrides()
    shadowed: list[tuple[str, str, str]] = []
    for model, path in sorted(find_active_models().items()):
        if not path.parent.name.lower().startswith(MODULAR_PART_DIRECTORY_PREFIX):
            continue
        try:
            for texture, material in read_model_material_bindings(path):
                if (
                    material in generated_materials
                    and texture in render_surfaces
                    and texture != material
                    and explicit_overrides.get(model) != material
                ):
                    shadowed.append((model, texture, material))
        except (UnicodeDecodeError, ValueError):
            continue
    return shadowed


def load_modular_tint_fallbacks() -> tuple[dict[str, list[str]], set[str]]:
    if not MODULAR_FALLBACKS.exists():
        raise RuntimeError(f"Missing modular tint fallback catalog: {MODULAR_FALLBACKS}")
    raw_fallbacks = json.loads(MODULAR_FALLBACKS.read_text(encoding="utf-8"))
    if (
        not isinstance(raw_fallbacks, dict)
        or not isinstance(raw_fallbacks.get("fallbacks"), dict)
        or not isinstance(raw_fallbacks.get("appendSourceRows"), list)
    ):
        raise RuntimeError(
            "Modular tint fallback catalog must contain fallback and appendSourceRows collections"
        )

    fallbacks: dict[str, list[str]] = {}
    for raw_source, raw_candidates in sorted(raw_fallbacks["fallbacks"].items()):
        source = str(raw_source).lower()
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise RuntimeError(
                f"Modular tint fallback source '{source}' must list at least one model"
            )
        fallbacks[source] = [str(candidate).lower() for candidate in raw_candidates]
    append_source_rows = {
        str(source).lower()
        for source in raw_fallbacks["appendSourceRows"]
    }
    if not append_source_rows.issubset(fallbacks):
        raise RuntimeError(
            "Every appended modular tint source row must name a configured fallback source"
        )
    return fallbacks, append_source_rows


def load_authored_texture_overrides() -> dict[str, str]:
    if not MODULAR_FALLBACKS.exists():
        raise RuntimeError(f"Missing modular tint fallback catalog: {MODULAR_FALLBACKS}")
    catalog = json.loads(MODULAR_FALLBACKS.read_text(encoding="utf-8"))
    raw_overrides = catalog.get("authoredTextureOverrides")
    if not isinstance(raw_overrides, dict):
        raise RuntimeError(
            "Modular tint fallback catalog must contain authoredTextureOverrides"
        )

    return {
        str(model).lower(): str(source).lower()
        for model, source in raw_overrides.items()
    }


def find_authored_texture_overrides(
    models: dict[str, Path],
    entries: dict[str, dict[str, object]],
    alias_sources: dict[str, str],
) -> dict[str, str]:
    """Validate the narrow set of authored bitmaps that must use same-name PLTs.

    These segmented hand models embed a stock soldier texture name even though
    their same-name PLT contains the skin and equipment dye masks used by the
    original part. Keeping this exception explicit prevents the general model
    scan from replacing other valid authored textures.
    """
    overrides = load_authored_texture_overrides()
    render_surfaces = find_active_render_surfaces()
    for model, source in overrides.items():
        path = models.get(model)
        if (
            path is None
            or not path.parent.name.lower().startswith(MODULAR_PART_DIRECTORY_PREFIX)
            or source != model
            or source not in entries
        ):
            raise RuntimeError(
                f"Authored texture override '{model}' must name an active same-name modular PLT"
            )

        try:
            bindings = read_model_material_bindings(path)
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Cannot inspect authored texture override '{model}': {exc}"
            ) from exc
        if not bindings or not any(texture in render_surfaces for texture, _ in bindings):
            raise RuntimeError(
                f"Authored texture override '{model}' does not replace an active bitmap"
            )
        if any(
            material is not None
            and alias_sources.get(material, material) != source
            for _, material in bindings
        ):
            raise RuntimeError(
                f"Authored texture override '{model}' would replace another material"
            )

    return overrides


def find_modular_human_material_fallbacks(
    models: dict[str, Path],
    entries: dict[str, dict[str, object]],
    alias_sources: dict[str, str],
) -> dict[str, str]:
    """Map untextured racial body variants to their human PLT material.

    Aurora falls back from missing race-specific segmented-body PLTs to the
    matching human PLT. Once that PLT is converted to an MTR, the implicit
    fallback no longer exists, so each compatible local model must bind the
    generated human material explicitly.
    """
    configured_fallbacks, _ = load_modular_tint_fallbacks()

    materials = set(entries)
    render_surfaces = find_active_render_surfaces()
    fallbacks: dict[str, str] = {}

    for source, candidates in configured_fallbacks.items():
        source_path = models.get(source)
        source_match = MODULAR_MODEL_PATTERN.fullmatch(source)
        if (
            source not in materials
            or source_path is None
            or source_match is None
            or source_match.group("race").lower() != "h"
            or not source_path.parent.name.lower().startswith(MODULAR_PART_DIRECTORY_PREFIX)
        ):
            raise RuntimeError(
                f"Modular tint fallback source '{source}' is not an active human part material"
            )
        for candidate in candidates:
            candidate_path = models.get(candidate)
            candidate_match = MODULAR_MODEL_PATTERN.fullmatch(candidate)
            if (
                candidate == source
                or candidate in materials
                or candidate in render_surfaces
                or candidate_path is None
                or candidate_match is None
                or candidate_match.group("race").lower() == "h"
            ):
                raise RuntimeError(
                    f"Modular tint fallback target '{candidate}' is not an untextured racial part model"
                )
            if (
                candidate_path.parent.resolve() != source_path.parent.resolve()
                or candidate_match.group("gender").lower()
                != source_match.group("gender").lower()
                or candidate_match.group("phenotype") != source_match.group("phenotype")
                or candidate_match.group("part").lower() != source_match.group("part").lower()
            ):
                raise RuntimeError(
                    f"Modular tint fallback target '{candidate}' is incompatible with '{source}'"
                )

            try:
                bindings = read_model_material_bindings(
                    candidate_path,
                    include_implicit_modular_surface=True,
                )
            except (UnicodeDecodeError, ValueError) as exc:
                raise RuntimeError(
                    f"Cannot inspect modular tint fallback target '{candidate}': {exc}"
                ) from exc
            if not bindings:
                raise RuntimeError(
                    f"Modular tint fallback target '{candidate}' has no renderable mesh surface"
                )

            # Only replace the implicit same-name modular surface. Authored
            # textures and authored/generated material bindings remain
            # authoritative for the racial variant.
            if any(texture != candidate for texture, _ in bindings):
                raise RuntimeError(
                    f"Modular tint fallback target '{candidate}' has an authored texture"
                )
            if any(
                material is not None
                and material not in materials
                and material not in alias_sources
                for _, material in bindings
            ):
                raise RuntimeError(
                    f"Modular tint fallback target '{candidate}' has an authored material"
                )

            existing = fallbacks.get(candidate)
            if existing is not None and existing != source:
                raise RuntimeError(
                    f"Modular model '{candidate}' has conflicting human tint fallbacks "
                    f"'{existing}' and '{source}'"
                )
            fallbacks[candidate] = source

    return fallbacks


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
    human_fallbacks = find_modular_human_material_fallbacks(
        models,
        entries,
        alias_sources,
    )
    authored_texture_overrides = find_authored_texture_overrides(
        models,
        entries,
        alias_sources,
    )
    human_fallback_sources = set(human_fallbacks.values())
    records: dict[tuple[str, str, str], dict[str, object]] = {}

    for model, path in sorted(models.items()):
        scope = model_material_scope(model, path)
        references = find_model_tint_material_references(path, materials, alias_sources)
        if model in authored_texture_overrides:
            references.update({
                texture: authored_texture_overrides[model]
                for texture, _ in read_model_material_bindings(path)
            })
        if not references:
            if model in human_fallbacks:
                references[path.stem.lower()] = human_fallbacks[model]
            elif model in human_fallback_sources:
                # Some stock human body parts omit ``bitmap`` entirely while
                # still selecting their same-name PLT through the modular-part
                # loader. Only configured fallback sources receive that
                # interpretation; applying it to every no-bitmap mesh would
                # rewrite unrelated helper geometry and discard authored
                # material behavior.
                references[path.stem.lower()] = model
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
        model = resolve_stock_model_resref(source, models)
        if any(record["model"] == model and record["source"] == source for record in records.values()):
            continue
        scope = f"stock:{source}"
        records[(model, source, scope)] = {
            "model": model,
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
    desired_bindings: dict[Path, dict[str, str]] = {}
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

        if path is not None:
            current_materials = record["current"]
            assert isinstance(current_materials, set)
            for current in current_materials:
                desired_bindings.setdefault(path, {})[str(current)] = material

        layers = tuple(int(layer) for layer in entries[source]["layers"])
        rows.add((model, material, layers))

    pending_bindings = {
        path: pending
        for path, desired in desired_bindings.items()
        if (
            pending := pending_model_material_bindings(
                path,
                desired,
                include_implicit_modular_surface=(
                    path.stem.lower() in human_fallbacks
                    or path.stem.lower() in human_fallback_sources
                    or path.stem.lower() in desired
                ),
            )
        )
    }

    return (
        [(model, material, list(layers)) for model, material, layers in sorted(rows)],
        pending_bindings,
        active_aliases,
    )


def resolve_stock_model_resref(material: str, models: dict[str, Path]) -> str:
    modular_match = re.fullmatch(r"(p[a-z][a-z][0-9]_[a-z]+)([0-9]{1,2})", material)
    if modular_match is None:
        return material

    model = f"{modular_match.group(1)}{int(modular_match.group(2)):03d}"
    return model if model in models else material


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
    used.update(
        find_modular_human_material_fallbacks(models, entries, alias_sources).values()
    )

    # Stock resources missing from the HAK source tree conventionally use the
    # same model/material resref; keep their converted source masks available.
    used.update(material for material in materials if material not in models)
    used.update(materials & find_table_referenced_resrefs())
    used.update(read_preserved_2da_material_sources(entries).values())
    return used


def tint_directory(model: str) -> Path:
    digest = hashlib.sha256(model.encode("ascii")).digest()
    return TINT_DIRECTORIES[int.from_bytes(digest[:4], "little") % len(TINT_DIRECTORIES)]


def tint_texture_resref(source_hash: str, width: int, height: int) -> str:
    """Return a stable, collision-resistant NWN resref for one packed tint mask."""
    digest = hashlib.sha256(f"{source_hash}:{width}x{height}".encode("ascii")).hexdigest()
    return f"tm_{digest[:13]}"


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


def uses_mapped_shader(lines: list[str]) -> bool:
    """Whether an authored/generated material still carries real mapped inputs.

    The first tint-map generator put ``vslit_sm_nm`` plus ``NormalTangents`` on
    every PLT-only body part. Those two generated defaults cannot be used as
    evidence here: doing so permanently keeps plain legacy PLTs on NWN's
    normal-mapped, per-pixel lighting path. Real mapped materials retain either
    a map texture, the authored NormalAndSpecMapped hint, or a non-default
    normal-mapped vertex shader.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if re.match(r"^texture[1-5]\b", stripped, re.IGNORECASE):
            return True
        if re.match(r"^renderhint\s+NormalAndSpecMapped\b", stripped, re.IGNORECASE):
            return True
        vertex_match = re.match(r"^customshaderVS\s+(\S+)", stripped, re.IGNORECASE)
        if vertex_match and vertex_match.group(1).lower() not in {"vslit_sm", "vslit_sm_nm"}:
            return True
    return False


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
    mapped_shader = uses_mapped_shader(original_lines)
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
    tint_row_parameter_pattern = "|".join(
        re.escape(uniform_name) for uniform_name, _ in TINT_ROW_PARAMETERS
    )
    tint_color_parameter_pattern = "|".join(
        re.escape(uniform_name)
        for uniform_name in TINT_LEGACY_COLOR_PARAMETERS + TINT_COLOR_PARAMETERS
    )
    tint_custom_mode_parameter_pattern = "|".join(
        re.escape(uniform_name) for uniform_name in TINT_CUSTOM_MODE_PARAMETERS
    )
    replaced = re.compile(
        r"^\s*(?:customshaderFS|texture0|texture7|texture9|texture10|"
        r"parameter\s+float\s+(?:tintMapWidth|tintMapHeight|useTexture1Alpha|useTexture9Alpha|"
        + tint_row_parameter_pattern
        + "|"
        + tint_color_parameter_pattern
        + "|"
        + tint_custom_mode_parameter_pattern
        + r"))\b",
        re.IGNORECASE,
    )
    lines = [line for line in original_lines if not replaced.match(line)]
    if texture9_alpha:
        lines = [line for line in lines if not re.match(r"^\s*texture3\b", line, re.IGNORECASE)]

    if not mapped_shader:
        # Remove the old generator defaults that incorrectly promoted every
        # plain PLT body part to the normal-mapped/per-pixel-lighting path.
        lines = [
            line
            for line in lines
            if not re.match(r"^\s*customshaderVS\s+vslit_sm_nm\s*$", line, re.IGNORECASE)
        ]

    if not any(re.match(r"^\s*customshaderVS\b", line, re.IGNORECASE) for line in lines):
        lines.append("customshaderVS vslit_sm_nm" if mapped_shader else "customshaderVS vslit_sm")
    if not any(re.match(r"^\s*renderhint\b", line, re.IGNORECASE) for line in lines):
        lines.append("renderhint NormalTangents")

    lines.extend(
        (
            "customshaderFS "
            + (TINT_MAPPED_FRAGMENT_SHADER if mapped_shader else TINT_FRAGMENT_SHADER),
            "texture0 plt_white",
            f"texture7 {texture}",
            "texture10 plt_palette",
            f"parameter float tintMapWidth {float(width):.1f}",
            f"parameter float tintMapHeight {float(height):.1f}",
        )
        + TINT_ROW_PARAMETER_LINES
        + TINT_LEGACY_COLOR_PARAMETER_LINES
        + TINT_COLOR_PARAMETER_LINES
        + TINT_CUSTOM_MODE_PARAMETER_LINES
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
        and all(
            line.lower() in mtr
            for line in TINT_ROW_PARAMETER_LINES
            + TINT_LEGACY_COLOR_PARAMETER_LINES
            + TINT_COLOR_PARAMETER_LINES
            + TINT_CUSTOM_MODE_PARAMETER_LINES
        )
    )


def load_source_manifest() -> dict[str, dict[str, object]]:
    if not SOURCE_MANIFEST.exists():
        return {}
    data = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    return {entry["model"].lower(): entry for entry in data}


def read_preserved_2da_material_sources(
    entries: dict[str, dict[str, object]],
) -> dict[str, str]:
    """Return every material alias/source retained by the stable 2DA rows."""
    if not OUTPUT_2DA.exists():
        return {}

    alias_sources = build_alias_source_lookup(entries)
    preserved: dict[str, str] = {}
    for physical_index, line in enumerate(
        OUTPUT_2DA.read_text(encoding="utf-8").splitlines()[3:]
    ):
        columns = line.split()
        if len(columns) < 4 or not columns[0].isdigit():
            continue
        material = columns[2].lower()
        source = alias_sources.get(material, material)
        if source not in entries:
            raise RuntimeError(
                "tintmap.2da compatibility row "
                f"{physical_index} references unknown material '{material}'"
            )
        preserved[material] = source
    return preserved


def write_source_manifest(
    entries: dict[str, dict[str, object]],
    preserved_order: list[str] | None = None,
) -> None:
    if preserved_order is None:
        ordered_keys = sorted(entries)
    else:
        ordered_keys = [key for key in preserved_order if key in entries]
        ordered_keys.extend(sorted(set(entries) - set(ordered_keys)))
    ordered = [entries[key] for key in ordered_keys]
    SOURCE_MANIFEST.write_text(
        json.dumps(ordered, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def render_2da(entries: dict[str, dict[str, object]]) -> str:
    lines = ["2DA V2.0", "", "   MODEL             MATERIAL          LAYERS"]
    rows = build_model_material_rows(entries)
    configured_fallbacks, append_source_rows = load_modular_tint_fallbacks()
    deferred_models = set(append_source_rows)
    deferred_models.update(
        target
        for targets in configured_fallbacks.values()
        for target in targets
    )
    new_row_order = (
        [row for row in rows if row[0] not in deferred_models]
        + [row for row in rows if row[0] in deferred_models]
    )
    rows_by_pair = {
        (model, material): (model, material, layer_values)
        for model, material, layer_values in rows
    }
    existing_rows: list[tuple[int, str, str, list[int]]] = []
    existing_pairs: set[tuple[str, str]] = set()
    next_label = 0
    if OUTPUT_2DA.exists():
        for line in OUTPUT_2DA.read_text(encoding="utf-8").splitlines()[3:]:
            columns = line.split()
            if len(columns) < 4 or not columns[0].isdigit():
                continue
            label = int(columns[0])
            model = columns[1].lower()
            material = columns[2].lower()
            pair = (model, material)
            if pair in existing_pairs:
                raise RuntimeError(
                    f"tintmap.2da contains duplicate model/material row {model}/{material}"
                )
            existing_pairs.add(pair)
            existing_rows.append(
                (label, model, material, [int(value) for value in columns[3].split(",")])
            )
            next_label = max(next_label, label + 1)

    # A 2DA row's runtime id is its physical position, not the numeric label in
    # the first column. Keep every established row in place (including retired
    # but still valid compatibility mappings), update its layer metadata when
    # it remains active, and append only genuinely new pairs.
    for label, model, material, old_layers in existing_rows:
        current = rows_by_pair.pop((model, material), None)
        layer_values = current[2] if current is not None else old_layers
        layers = ",".join(str(value) for value in layer_values)
        lines.append(f"{label:<4} {model:<17} {material:<17} {layers}")

    for model, material, layer_values in new_row_order:
        if (model, material) not in rows_by_pair:
            continue
        layers = ",".join(str(value) for value in layer_values)
        lines.append(f"{next_label:<4} {model:<17} {material:<17} {layers}")
        next_label += 1
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
    if not active:
        raise RuntimeError("No 3D tint material PLTs were found to convert")

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

        texture = tint_texture_resref(source_hash, width, height)
        dds_path = tint_directory(texture) / f"{texture}.dds"
        dds_path.parent.mkdir(exist_ok=True)
        existing_entry = entries.get(model)
        source_changed = (
            existing_entry is None
            or str(existing_entry.get("sourceSha256", "")) != source_hash
        )
        if source_changed or not is_generated_pair(model, texture, width, height, dds_path):
            write_packed_dds(dds_path, width, height, shade, layer)
            update_mtr(mtr_path(model), model, texture, width, height)
        shade_hash = hashlib.sha256(
            decode_dds_shades(dds_path, width, height).tobytes()
        ).hexdigest()
        entries[model] = {
            "model": model,
            "material": model,
            "aliases": list(existing_entry.get("aliases", [])) if existing_entry else [],
            "layers": layers,
            "width": width,
            "height": height,
            "source": relative_source,
            "sourceSha256": source_hash,
            "shadeSha256": shade_hash,
            "layerSha256": hashlib.sha256(layer.tobytes()).hexdigest(),
            "output": dds_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "texture": texture,
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


def synchronize_selected_model_material_aliases(
    entries: dict[str, dict[str, object]],
    selected_sources: set[str],
) -> tuple[int, dict[str, str]]:
    """Bind only models that consume the selected source materials.

    A full synchronization intentionally rewrites every generated alias MTR.
    That is appropriate for a corpus rebuild, but not for replacing a small set
    of source masks in an otherwise audited manifest.
    """
    _, pending_bindings, planned_aliases = build_model_material_plan(entries)

    def source_for(material: str) -> str:
        return planned_aliases.get(material, material)

    selected_bindings = {
        path: {
            texture: material
            for texture, material in bindings.items()
            if source_for(material) in selected_sources
        }
        for path, bindings in pending_bindings.items()
    }
    selected_bindings = {
        path: bindings for path, bindings in selected_bindings.items() if bindings
    }

    changed_models = 0
    for path, path_bindings in sorted(selected_bindings.items(), key=lambda value: str(value[0])):
        try:
            if synchronize_model_material_bindings(path, path_bindings):
                changed_models += 1
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"Cannot bind tint materials in {path}: {exc}") from exc

    _, remaining_bindings, active_aliases = build_model_material_plan(entries)
    remaining_selected = {
        path: {
            texture: material
            for texture, material in bindings.items()
            if active_aliases.get(material, material) in selected_sources
        }
        for path, bindings in remaining_bindings.items()
    }
    remaining_selected = {
        path: bindings for path, bindings in remaining_selected.items() if bindings
    }
    if remaining_selected:
        examples = [str(path) for path in list(remaining_selected)[:10]]
        raise RuntimeError(f"Selected tint material bindings did not synchronize for: {examples}")

    aliases_by_source: dict[str, list[str]] = {source: [] for source in selected_sources}
    for alias, source in active_aliases.items():
        if source in aliases_by_source:
            aliases_by_source[source].append(alias)
    for source, aliases in aliases_by_source.items():
        entry = entries[source]
        if aliases:
            entry["aliases"] = sorted(aliases)
        else:
            entry.pop("aliases", None)

    if build_alias_source_lookup(entries) != active_aliases:
        raise RuntimeError(
            "Preserving selected tint materials would change aliases outside the selected sources"
        )

    for source in sorted(selected_sources):
        entry = entries[source]
        update_mtr(
            mtr_path(source),
            source,
            str(entry.get("texture") or source),
            int(entry["width"]),
            int(entry["height"]),
        )
    for alias, source in sorted(active_aliases.items()):
        if source not in selected_sources:
            continue
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


def generate_preserving_manifest() -> None:
    """Convert active source PLTs without pruning unrelated generated assets."""
    global _ACTIVE_RENDER_SURFACES

    active, all_paths = find_tint_material_plts()
    outside_source_plts = find_tint_material_plts_outside_sources()
    if outside_source_plts:
        raise RuntimeError(
            f"3D tint material PLTs exist outside configured source directories: {outside_source_plts[:10]}"
        )
    if not active:
        raise RuntimeError("No 3D tint material PLTs were found to convert")

    manifest_order = [
        entry["model"].lower()
        for entry in json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    ]
    entries = load_source_manifest()
    candidate_entries = dict(entries)
    for material in active:
        candidate_entries.setdefault(material, {"aliases": []})
    used_materials = find_used_tint_materials(candidate_entries)
    active_materials = {
        material: source_path
        for material, source_path in active.items()
        if material in used_materials
    }
    if not active_materials:
        raise RuntimeError("None of the active source PLTs are referenced by active models")

    previous_outputs = {
        packed_dds_path(material, entries[material]).resolve()
        for material in active_materials
        if material in entries and entries[material].get("output")
    }
    signatures = {
        (
            str(entry["sourceSha256"]),
            int(entry["width"]),
            int(entry["height"]),
        ): entry
        for material, entry in entries.items()
        if material not in active_materials
    }

    for material, source_path in sorted(active_materials.items()):
        width, height, shade, layer, source_hash = read_plt(source_path)
        signature = (source_hash, width, height)
        matching_entry = signatures.get(signature)
        if matching_entry is None:
            texture = tint_texture_resref(source_hash, width, height)
            dds_path = tint_directory(texture) / f"{texture}.dds"
            dds_path.parent.mkdir(exist_ok=True)
            if not is_generated_pair(material, texture, width, height, dds_path):
                write_packed_dds(dds_path, width, height, shade, layer)
        else:
            texture = str(matching_entry["texture"])
            dds_path = packed_dds_path(str(matching_entry["model"]), matching_entry)

        existing_entry = entries.get(material)
        shade_hash = hashlib.sha256(
            decode_dds_shades(dds_path, width, height).tobytes()
        ).hexdigest()
        entries[material] = {
            "model": material,
            "material": material,
            "aliases": list(existing_entry.get("aliases", [])) if existing_entry else [],
            "layers": [int(value) for value in np.unique(layer)],
            "width": width,
            "height": height,
            "source": source_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "sourceSha256": source_hash,
            "shadeSha256": shade_hash,
            "layerSha256": hashlib.sha256(layer.tobytes()).hexdigest(),
            "output": dds_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "texture": texture,
        }
        signatures.setdefault(signature, entries[material])

    # The model plan must observe runtime resources, after the input PLTs have
    # gone, otherwise a same-name PLT incorrectly suppresses its replacement MTR.
    for path in all_paths:
        resolved = path.resolve()
        if REPOSITORY_ROOT.resolve() not in resolved.parents or not is_tint_material_plt(resolved):
            raise RuntimeError(f"Refusing to delete unexpected path: {resolved}")
        resolved.unlink()
    _ACTIVE_RENDER_SURFACES = None

    selected_sources = set(active_materials)
    changed_models, _ = synchronize_selected_model_material_aliases(entries, selected_sources)

    retained_outputs = {
        packed_dds_path(material, entry).resolve()
        for material, entry in entries.items()
    }
    for old_output in previous_outputs - retained_outputs:
        if old_output.exists():
            old_output.unlink()

    for material in selected_sources:
        for path in source_mtr_paths(material):
            path.unlink()

    write_source_manifest(entries, manifest_order)
    write_2da(entries)
    print(
        f"Generated {len(active_materials)} selected materials while preserving "
        f"{len(entries) - len(active_materials)} manifest entries; discarded "
        f"{len(active) - len(active_materials)} unreferenced masks and isolated "
        f"materials in {changed_models} models."
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
        texture = tint_texture_resref(
            str(group_entries[0]["sourceSha256"]),
            int(group_entries[0]["width"]),
            int(group_entries[0]["height"]),
        )
        current_path = packed_dds_path(canonical, group_entries[0])
        target_path = tint_directory(texture) / f"{texture}.dds"
        target_path.parent.mkdir(exist_ok=True)
        if current_path.resolve() != target_path.resolve():
            if not current_path.exists():
                raise RuntimeError(f"Missing canonical packed DDS: {current_path}")
            current_path.replace(target_path)

        retained_outputs.add(target_path.resolve())
        relative_output = target_path.relative_to(REPOSITORY_ROOT).as_posix()
        for entry in group_entries:
            model = str(entry["model"])
            entry["texture"] = texture
            entry["output"] = relative_output
            update_mtr(
                mtr_path(model),
                model,
                texture,
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
    preserved_aliases = read_preserved_2da_material_sources(entries)
    _, pending_bindings, planned_aliases = build_model_material_plan(entries)
    changed_models = 0
    for path, path_bindings in sorted(pending_bindings.items(), key=lambda value: str(value[0])):
        try:
            if synchronize_model_material_bindings(path, path_bindings):
                changed_models += 1
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"Cannot bind tint materials in {path}: {exc}") from exc

    aliases_by_source: dict[str, list[str]] = {source: [] for source in entries}
    for alias, source in planned_aliases.items():
        aliases_by_source[source].append(alias)
    for source, entry in entries.items():
        aliases = sorted(aliases_by_source[source])
        if aliases:
            entry["aliases"] = aliases
        else:
            entry.pop("aliases", None)

    _, remaining_bindings, active_aliases = build_model_material_plan(entries)
    if remaining_bindings:
        examples = [str(path) for path in list(remaining_bindings)[:10]]
        raise RuntimeError(f"Tint material bindings did not synchronize for: {examples}")

    for alias, source in preserved_aliases.items():
        if alias == source:
            continue
        active_source = active_aliases.get(alias)
        if active_source is not None and active_source != source:
            raise RuntimeError(
                f"Preserved tint material alias '{alias}' collides for "
                f"'{active_source}' and '{source}'"
            )
        active_aliases[alias] = source

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


def refresh_materials() -> None:
    """Regenerate MTR declarations without rewriting meshes or packed maps."""
    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to refresh")

    _, _, active_aliases = build_model_material_plan(entries)
    for source, entry in sorted(entries.items()):
        update_mtr(
            mtr_path(source),
            source,
            str(entry.get("texture") or source),
            int(entry["width"]),
            int(entry["height"]),
        )
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

    print(
        f"Refreshed {len(entries)} source and {len(active_aliases)} alias tint materials.",
        flush=True,
    )


def refresh_packed_checksums() -> None:
    """Record decoded packed-channel checksums without rewriting DDS resources."""
    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to refresh")

    manifest_order = list(entries)
    decoded_shade_hashes: dict[tuple[Path, int, int], str] = {}
    for model, entry in entries.items():
        width = int(entry["width"])
        height = int(entry["height"])
        dds_path = packed_dds_path(model, entry)
        dds_error = check_dds(dds_path, width, height)
        if dds_error:
            raise RuntimeError(f"{model}: {dds_error}")
        hash_key = (dds_path.resolve(), width, height)
        if hash_key not in decoded_shade_hashes:
            decoded_shade_hashes[hash_key] = hashlib.sha256(
                decode_dds_shades(dds_path, width, height).tobytes()
            ).hexdigest()
        entry["shadeSha256"] = decoded_shade_hashes[hash_key]

    write_source_manifest(entries, manifest_order)
    print(
        f"Refreshed decoded shade checksums for {len(entries)} tint materials.",
        flush=True,
    )


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


def decode_dds_channel(
    path: Path,
    width: int,
    height: int,
    channel_offset: int,
) -> np.ndarray:
    raw = path.read_bytes()
    blocks_wide = (width + 3) // 4
    blocks_high = (height + 3) // 4
    block_count = blocks_wide * blocks_high
    packed = np.frombuffer(raw, dtype=np.uint8, offset=128).reshape(block_count, 16)[
        :, channel_offset : channel_offset + 8
    ]
    endpoint0 = packed[:, 0]
    endpoint1 = packed[:, 1]
    palette = bc4_palette(endpoint0, endpoint1)
    bits = np.zeros(block_count, dtype=np.uint64)
    for byte_index in range(6):
        bits |= packed[:, byte_index + 2].astype(np.uint64) << (byte_index * 8)
    indices = ((bits[:, None] >> (np.arange(16, dtype=np.uint64) * 3)) & 7).astype(np.int64)
    decoded = np.take_along_axis(palette, indices, axis=1)
    image = decoded.reshape(blocks_high, blocks_wide, 4, 4).transpose(0, 2, 1, 3)
    return image.reshape(blocks_high * 4, blocks_wide * 4)[:height, :width]


def decode_dds_shades(path: Path, width: int, height: int) -> np.ndarray:
    return decode_dds_channel(path, width, height, 0)


def decode_dds_layers(path: Path, width: int, height: int) -> np.ndarray:
    return bc4_layer_categories(decode_dds_channel(path, width, height, 8))


def check_tga_header(
    path: Path,
    width: int,
    height: int,
    bits_per_pixel: int = 24,
) -> str | None:
    if not path.exists():
        return "missing TGA"
    raw = path.read_bytes()
    if len(raw) < 18:
        return "truncated TGA"
    image_type = raw[2]
    actual_width, actual_height = struct.unpack_from("<HH", raw, 12)
    if image_type not in (2, 10) or raw[1] != 0 or raw[16] != bits_per_pixel:
        return f"TGA must be {bits_per_pixel}-bit true-color without a color map"
    if (actual_width, actual_height) != (width, height):
        return f"TGA dimensions {(actual_width, actual_height)} != {(width, height)}"
    return None


def check_tint_mtr_structure(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    directives: dict[tuple[str, ...], list[str]] = {}
    for line in lines:
        key = mtr_directive_key(line)
        if key is not None:
            directives.setdefault(key, []).append(line.strip())

    errors: list[str] = []
    singleton_keys = (
        ("customshadervs",),
        ("customshaderfs",),
        ("texture0",),
        ("texture7",),
        ("texture10",),
        ("parameter", "float", "tintmapwidth"),
        ("parameter", "float", "tintmapheight"),
    ) + tuple(
        ("parameter", "float", uniform_name.lower())
        for uniform_name, _ in TINT_ROW_PARAMETERS
    ) + tuple(
        ("parameter", "float", uniform_name.lower())
        for uniform_name in TINT_LEGACY_COLOR_PARAMETERS
    ) + tuple(
        ("parameter", "float", uniform_name.lower())
        for uniform_name in TINT_COLOR_PARAMETERS
    ) + tuple(
        ("parameter", "float", uniform_name.lower())
        for uniform_name in TINT_CUSTOM_MODE_PARAMETERS
    )
    for key in singleton_keys:
        count = len(directives.get(key, []))
        if count != 1:
            errors.append(f"directive {' '.join(key)} occurs {count} times")

    if len(directives.get(("renderhint",), [])) != 1:
        errors.append("directive renderhint must occur exactly once")

    render_hints = directives.get(("renderhint",), [])
    if render_hints and render_hints[0].lower() not in {
        "renderhint normalandspecmapped",
        "renderhint normaltangents",
    }:
        errors.append(f"unsupported tint render hint '{render_hints[0]}'")

    vertex_shaders = directives.get(("customshadervs",), [])
    if vertex_shaders:
        tokens = vertex_shaders[0].split()
        if len(tokens) != 2 or len(tokens[1]) > 16 or not tokens[1].isascii():
            errors.append(f"invalid custom vertex shader directive '{vertex_shaders[0]}'")

    fragment_shaders = directives.get(("customshaderfs",), [])
    if fragment_shaders and fragment_shaders[0].lower() not in {
        f"customshaderfs {TINT_FRAGMENT_SHADER}",
        f"customshaderfs {TINT_MAPPED_FRAGMENT_SHADER}",
    }:
        errors.append(f"unexpected tint fragment shader '{fragment_shaders[0]}'")

    mapped_shader = uses_mapped_shader(lines)
    if fragment_shaders:
        fragment_shader = fragment_shaders[0].split(maxsplit=1)[1].lower()
        if mapped_shader and fragment_shader != TINT_MAPPED_FRAGMENT_SHADER:
            errors.append("mapped tint material does not use the mapped tint shader")
        elif not mapped_shader and fragment_shader != TINT_FRAGMENT_SHADER:
            errors.append("PLT-only tint material incorrectly uses the mapped tint shader")

    return errors


def audit() -> None:
    entries = load_source_manifest()
    errors: list[str] = []
    texture_sources: dict[str, set[tuple[str, int, int]]] = {}
    if not entries:
        errors.append("tint source manifest is empty")

    model_material_rows: list[tuple[str, str, list[int]]] = []
    pending_model_bindings: dict[Path, dict[str, str]] = {}
    active_aliases: dict[str, str] = {}
    if entries:
        model_material_rows, pending_model_bindings, active_aliases = build_model_material_plan(entries)
        manifest_aliases = build_alias_source_lookup(entries)
        if manifest_aliases != active_aliases:
            errors.append(
                "tint source manifest aliases do not exactly match the scoped materials used by active models"
            )
        if pending_model_bindings:
            examples = ", ".join(
                str(path.relative_to(REPOSITORY_ROOT))
                for path in list(pending_model_bindings)[:10]
            )
            errors.append(
                f"{len(pending_model_bindings)} models do not bind their generated tint materials: {examples}"
            )
        shadowed_surfaces = find_generated_materials_shadowing_authored_surfaces(entries)
        if shadowed_surfaces:
            examples = ", ".join(
                f"{model}:{texture}->{material}"
                for model, texture, material in shadowed_surfaces[:10]
            )
            errors.append(
                f"{len(shadowed_surfaces)} generated tint bindings replace authored model surfaces: {examples}"
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

    decoded_shade_hashes: dict[tuple[Path, int, int], str] = {}
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
        texture = str(entry.get("texture") or model)
        expected_texture = tint_texture_resref(str(entry["sourceSha256"]), width, height)
        if texture != expected_texture:
            errors.append(
                f"{model}: packed tint texture '{texture}' must use internal resref "
                f"'{expected_texture}'"
            )
        if len(texture) > 16 or not texture.isascii():
            errors.append(f"{model}: invalid packed tint texture resref '{texture}'")
        texture_sources.setdefault(texture, set()).add(
            (str(entry["sourceSha256"]), width, height)
        )
        dds_path = packed_dds_path(model, entry)
        if not any(directory.resolve() in dds_path.resolve().parents for directory in TINT_DIRECTORIES):
            errors.append(f"{model}: packed DDS is outside tint HAK directories")
        dds_error = check_dds(dds_path, width, height)
        if dds_error:
            errors.append(f"{model}: {dds_error}")
        else:
            packed_hash_key = (dds_path.resolve(), width, height)
            expected_shade_hash = entry.get("shadeSha256")
            if not expected_shade_hash:
                errors.append(f"{model}: missing decoded shade checksum")
            else:
                if packed_hash_key not in decoded_shade_hashes:
                    decoded_shade_hashes[packed_hash_key] = hashlib.sha256(
                        decode_dds_shades(dds_path, width, height).tobytes()
                    ).hexdigest()
                if decoded_shade_hashes[packed_hash_key] != expected_shade_hash:
                    errors.append(f"{model}: packed DDS changes the decoded shade channel")

            expected_layer_hash = entry.get("layerSha256")
            if not expected_layer_hash:
                errors.append(f"{model}: missing decoded layer checksum")
            else:
                if packed_hash_key not in decoded_layer_hashes:
                    decoded_layer_hashes[packed_hash_key] = hashlib.sha256(
                        decode_dds_layers(dds_path, width, height).tobytes()
                    ).hexdigest()
                if decoded_layer_hashes[packed_hash_key] != expected_layer_hash:
                    errors.append(f"{model}: packed DDS changes one or more categorical tint layers")

        material_path = mtr_path(model)
        if not material_path.exists():
            errors.append(f"{model}: missing MTR")
        else:
            errors.extend(
                f"{model}: {error}"
                for error in check_tint_mtr_structure(material_path)
            )
            mtr = material_path.read_text(encoding="utf-8-sig").lower()
            fragment_shader = (
                TINT_MAPPED_FRAGMENT_SHADER if uses_mapped_shader(mtr.splitlines())
                else TINT_FRAGMENT_SHADER
            )
            required = (
                "customshadervs ",
                f"customshaderfs {fragment_shader}",
                "texture0 plt_white",
                f"texture7 {texture}",
                "texture10 plt_palette",
                f"parameter float tintmapwidth {float(width):.1f}",
                f"parameter float tintmapheight {float(height):.1f}",
            ) + tuple(
                line.lower()
                for line in TINT_ROW_PARAMETER_LINES
                + TINT_LEGACY_COLOR_PARAMETER_LINES
                + TINT_COLOR_PARAMETER_LINES
                + TINT_CUSTOM_MODE_PARAMETER_LINES
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

    for texture, sources in texture_sources.items():
        if len(sources) > 1:
            errors.append(
                f"{texture}: internal tint resref aliases {len(sources)} different source masks"
            )

    internal_textures = set(texture_sources)
    tint_roots = {directory.resolve() for directory in TINT_DIRECTORIES}
    for directory in hak_directories():
        if directory.resolve() in tint_roots:
            continue
        for path in directory.iterdir():
            if (
                path.is_file()
                and path.suffix.lower() in {".dds", ".tga", ".plt"}
                and path.stem.lower() in internal_textures
            ):
                errors.append(
                    f"{path.stem.lower()}: internal tint resref collides with "
                    f"{path.relative_to(REPOSITORY_ROOT).as_posix()}"
                )

    for alias, source in sorted(active_aliases.items()):
        if len(alias) > 16 or not alias.isascii():
            errors.append(f"{source}: invalid scoped material alias '{alias}'")
            continue
        entry = entries[source]
        material_path = mtr_path(alias)
        if not material_path.exists():
            errors.append(f"{alias}: missing scoped MTR for '{source}'")
            continue
        errors.extend(
            f"{alias}: {error}"
            for error in check_tint_mtr_structure(material_path)
        )
        mtr = material_path.read_text(encoding="utf-8-sig").lower()
        texture = str(entry.get("texture") or source)
        fragment_shader = (
            TINT_MAPPED_FRAGMENT_SHADER if uses_mapped_shader(mtr.splitlines())
            else TINT_FRAGMENT_SHADER
        )
        required = (
            "customshadervs ",
            f"customshaderfs {fragment_shader}",
            "texture0 plt_white",
            f"texture7 {texture}",
            "texture10 plt_palette",
            f"parameter float tintmapwidth {float(entry['width']):.1f}",
            f"parameter float tintmapheight {float(entry['height']):.1f}",
        ) + tuple(
            line.lower()
            for line in TINT_ROW_PARAMETER_LINES
            + TINT_LEGACY_COLOR_PARAMETER_LINES
            + TINT_COLOR_PARAMETER_LINES
            + TINT_CUSTOM_MODE_PARAMETER_LINES
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
    else:
        output_2da_text = OUTPUT_2DA.read_text(encoding="utf-8")
        if output_2da_text != render_2da(entries):
            errors.append("tintmap.2da does not exactly match the source manifest")
        seen_output_pairs: set[tuple[str, str]] = set()
        for physical_index, line in enumerate(output_2da_text.splitlines()[3:]):
            columns = line.split()
            if len(columns) < 4 or not columns[0].isdigit():
                errors.append(f"tintmap.2da row {physical_index} is malformed")
                continue
            if int(columns[0]) != physical_index:
                errors.append(
                    "tintmap.2da numeric labels must match physical row positions; "
                    f"row {physical_index} uses label {columns[0]}"
                )
            model, material = columns[1:3]
            pair = (model.lower(), material.lower())
            if pair in seen_output_pairs:
                errors.append(f"tintmap.2da contains duplicate model/material row {model}/{material}")
            seen_output_pairs.add(pair)
            source = active_aliases.get(material, material)
            if source not in entries:
                errors.append(
                    f"tintmap.2da compatibility row {physical_index} references unknown material '{material}'"
                )
                continue
            try:
                layers = [int(value) for value in columns[3].split(",")]
            except ValueError:
                errors.append(f"tintmap.2da row {physical_index} has an invalid layer list '{columns[3]}'")
                continue
            if layers != entries[source]["layers"]:
                errors.append(
                    f"tintmap.2da row {physical_index} layer list disagrees with material '{material}'"
                )

    if not WHITE_TEXTURE.exists() or WHITE_TEXTURE.read_bytes() != white_texture_bytes():
        errors.append("plt_white.tga is missing or invalid")
    palette_error = check_tga_header(
        PALETTE_TEXTURE,
        256,
        PALETTE_TEXTURE_HEIGHT,
        bits_per_pixel=32,
    )
    if palette_error:
        errors.append(f"plt_palette.tga: {palette_error}")
    if not PALETTE_TXI.exists() or "mipmap 0" not in PALETTE_TXI.read_text(encoding="utf-8").lower():
        errors.append("plt_palette.txi must disable mipmaps")
    for shader_path, mapped_shader in (
        (TINT_SHADER, False),
        (TINT_MAPPED_SHADER, True),
    ):
        if not shader_path.exists():
            errors.append(f"missing tint fragment shader {shader_path.name}")
            continue
        shader = shader_path.read_text(encoding="utf-8")
        for token in (
            "uniform sampler2D texUnit7",
            "uniform sampler2D texUnit9",
            "uniform sampler2D texUnit10",
            "uniform vec4 rowSkin",
            "uniform vec4 tintSkin",
            "uniform float tintSkinR",
            "uniform float tintSkinG",
            "uniform float tintSkinB",
            "uniform float useCustomSkin",
            "float paletteU = (g * 255.0 + 0.5) / 256.0",
            "vec2(128.5 / 256.0, referenceV)",
            "bool useCustomTint = tintState.x < 0.0",
            "float packedColor = max(-tintState.x - 1.0, 0.0)",
            "clamp(customTint * shadeScale, 0.0, 1.0)",
            "fEnvMapLevel = useCustomTint ? 0.0 : 1.0 - paletteColor.a",
            "float outputAlpha = materialFrontDiffuse.a",
            "SetupStandardShaderInputs();",
            "ApplyStandardShader();",
        ):
            if token not in shader:
                errors.append(f"{shader_path.name} missing '{token}'")
        expected_map_value = "1" if mapped_shader else "0"
        for macro in (
            "NORMAL_MAP",
            "SPECULAR_MAP",
            "ROUGHNESS_MAP",
            "SELF_ILLUMINATION_MAP",
        ):
            token = f"#define {macro} {expected_map_value}"
            if token not in shader:
                errors.append(f"{shader_path.name} missing '{token}'")
        for token in ("computeAnisoSpecular", "fSpecularity = 0.0"):
            if token in shader:
                errors.append(
                    f"{shader_path.name} must not override standard material rendering with '{token}'"
                )

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
    action.add_argument(
        "--generate-preserving",
        action="store_true",
        help="convert active 3D material PLTs without pruning unrelated manifest entries",
    )
    action.add_argument("--relocate", action="store_true", help="split existing maps across tint HAKs")
    action.add_argument(
        "--refresh-materials",
        action="store_true",
        help="regenerate MTR declarations without changing packed maps or meshes",
    )
    action.add_argument(
        "--refresh-packed-checksums",
        action="store_true",
        help="record decoded packed-map checksums without rewriting DDS resources",
    )
    action.add_argument("--deduplicate", action="store_true", help="share byte-identical packed maps")
    action.add_argument("--prune", action="store_true", help="remove masks not referenced by active models")
    action.add_argument("--check", action="store_true", help="audit generated assets and PLT coverage")
    arguments = parser.parse_args()

    require_repository_root()
    if arguments.generate:
        generate()
    elif arguments.generate_preserving:
        generate_preserving_manifest()
    elif arguments.relocate:
        relocate()
    elif arguments.refresh_materials:
        refresh_materials()
    elif arguments.refresh_packed_checksums:
        refresh_packed_checksums()
    elif arguments.deduplicate:
        deduplicate()
    elif arguments.prune:
        prune()
    else:
        audit()


if __name__ == "__main__":
    main()
