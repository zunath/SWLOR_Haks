#!/usr/bin/env python3
"""Convert 3D model PLTs to compact, shader-driven BC5 tint maps.

The packed DDS keeps the PLT shade in red and its layer id in green. It uses a
content-addressed internal resref so the shader mask cannot collide with a
legacy diffuse texture that shares the original PLT resref. The generated
tintmap.2da is the authoritative
model/material/layer catalog consumed by the game server and appearance
editor; material names are read from the binary MDLs rather than inferred
from model names. Palette-driven inventory icons and cloakmodel.2da's dynamic
texture choices remain PLTs because neither path exposes an addressable model
material for the replacement shader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
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
DYNAMIC_CLOAK_PLT_PATTERN = re.compile(r"^cloak_[0-9]{3}$", re.IGNORECASE)
STOCK_CLOAK_TEXTURE_MAX = 16
TINT_DIRECTORIES = tuple(REPOSITORY_ROOT / f"sw_tint{index}" for index in range(3))
OUTPUT_MTR_DIRECTORY = REPOSITORY_ROOT / "sw_tint_mtr"
OUTPUT_2DA = REPOSITORY_ROOT / "sw_2da" / "tintmap.2da"
CLOAK_MODEL_2DA = REPOSITORY_ROOT / "sw_2da" / "cloakmodel.2da"
HAK_CONFIG = REPOSITORY_ROOT / "hakbuilder.json"
SOURCE_MANIFEST = Path(__file__).with_name("TintMapSources.json")
MODULAR_FALLBACKS = Path(__file__).with_name("TintMapFallbacks.json")
STOCK_PALETTE_RESOURCES = Path(__file__).with_name("TintMapStockPalettes.json")
MATERIAL_SOURCES = Path(__file__).with_name("TintMapMaterialSources.json")
WHITE_TEXTURE = REPOSITORY_ROOT / "sw_item" / "plt_white.tga"
PALETTE_TEXTURE = REPOSITORY_ROOT / "sw_item" / "plt_palette.tga"
PALETTE_TXI = REPOSITORY_ROOT / "sw_item" / "plt_palette.txi"
TINT_SHADER = REPOSITORY_ROOT / "sw_shader" / "fs_plt_tinter.shd"
TINT_MAPPED_SHADER = REPOSITORY_ROOT / "sw_shader" / "fs_plt_tinter_nm.shd"
TINT_HAIR_MAPPED_SHADER = REPOSITORY_ROOT / "sw_shader" / "fs_plt_hair_nm.shd"
TINT_FRAGMENT_SHADER = "fs_plt_tinter"
TINT_MAPPED_FRAGMENT_SHADER = "fs_plt_tinter_nm"
TINT_HAIR_MAPPED_FRAGMENT_SHADER = "fs_plt_hair_nm"
AUTHORED_HAIR_FRAGMENT_SHADERS = {"fslit_aniso_nm"}
_MTR_PATHS_BY_RESREF: dict[str, Path] | None = None
_SOURCE_MTR_PATHS_BY_RESREF: dict[str, list[Path]] | None = None
_BC4_LAYER_CANDIDATES: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
_EXACT_LAYER_ENCODINGS: dict[tuple[int, ...], tuple[int, int, np.ndarray]] = {}
_ACTIVE_MODELS: dict[str, Path] | None = None
_ACTIVE_RENDER_SURFACES: set[str] | None = None
_TABLE_REFERENCED_RESREFS: set[str] | None = None
_HAK_DIRECTORIES: tuple[Path, ...] | None = None
_NATIVE_MODULAR_PALETTES: set[str] | None = None
_MATERIAL_SOURCES: dict[str, dict[str, object]] | None = None
_MATERIAL_BITMAP_ALIASES: dict[str, str] = {}
_PROFILE_ALIASES: dict[str, tuple[str, list[str]]] = {}
_PROFILE_SIGNATURES: dict[tuple[str, str], tuple[tuple[tuple[str, ...], str], ...]] = {}
_PRESERVED_MATERIALS: dict[str, tuple[str, list[str]]] = {}
_NATIVE_ROBE_MATERIALS: set[str] = set()
_NATIVE_ROBE_SOURCES: set[str] = set()

STOCK_MODEL_RESOURCE_TYPE = 2002
STOCK_KEY_ARCHIVES = (
    "nwn_base.key",
    "nwn_base_loc.key",
    "nwn_retail.key",
    "nwn_retail_loc.key",
    "xp1.key",
    "xp1_loc.key",
    "xp1patch.key",
    "xp1patch_loc.key",
    "xp2.key",
    "xp2_loc.key",
    "xp2patch.key",
    "xp2patch_loc.key",
    "xp3.key",
    "xp3_loc.key",
    "xp3patch.key",
    "xp3patch_loc.key",
)
STOCK_MODEL_PART_DIRECTORIES = {
    "belt": "sw_pt_belt",
    "bicepl": "sw_pt_lbicep",
    "bicepr": "sw_pt_rbicep",
    "chest": "sw_pt_chest",
    "footl": "sw_pt_lfoot",
    "footr": "sw_pt_rfoot",
    "forel": "sw_pt_lfore",
    "forer": "sw_pt_rfore",
    "handl": "sw_pt_lhand",
    "handr": "sw_pt_rhand",
    "head": "sw_pt_head",
    "legl": "sw_pt_lthigh",
    "legr": "sw_pt_rthigh",
    "neck": "sw_pt_neck",
    "pelvis": "sw_pt_pelvis",
    "robe": "sw_pt_robe",
    "shinl": "sw_pt_lshin",
    "shinr": "sw_pt_rshin",
    "shol": "sw_pt_lshoul",
    "shor": "sw_pt_rshoul",
}

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
# MTR value count selects the GL upload width. Rows are scalar uniforms even
# though the runtime override command transports its value in a native Vec4.
TINT_ROW_PARAMETER_LINES = tuple(
    f"parameter float {uniform_name} "
    f"{(base_row + 0.5) / PALETTE_TEXTURE_HEIGHT:.6f}"
    for uniform_name, base_row in TINT_ROW_PARAMETERS
)
# Obsolete transports are recognized only so regeneration removes them. The
# shaders expose palette rows; retaining these declarations causes a failed
# uniform lookup and log entry for every parameter on every new material.
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
TINT_COLOR_PARAMETERS = tuple(
    f"{uniform_name}{component}"
    for uniform_name in TINT_COLOR_PARAMETER_BASES
    for component in ("R", "G", "B")
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
OBSOLETE_TINT_PARAMETERS = frozenset(
    name.lower()
    for name in TINT_LEGACY_COLOR_PARAMETERS + TINT_COLOR_PARAMETERS + TINT_CUSTOM_MODE_PARAMETERS
)
TEXTURE1_ALPHA_SHADERS = {"fs_plt_hair", "pfh0_neck199", "pmh0_neck199"}
TEXTURE1_ALPHA_MATERIALS = {"pfh0_neck199", "pmh0_head248", "pmh0_neck199"}
TEXTURE9_ALPHA_MATERIALS = {
    "pfh0_head232": "pfh0_head232_a",
    "pmh0_head231": "pmh0_head231_a",
}
AUTHORED_HAIR_MAPS = {
    "pfh0_head232": {1: "pfh0_head232_n", 2: "pfh0_head232_s"},
    "pmh0_head231": {1: "pmh0_head231_n", 2: "pmh0_head231_s"},
}
# Both resources were authored with fslit_aniso_nm. The female declaration was
# a source MTR removed by the conversion, while the male declaration remains a
# .shd, so keep the material set explicit after source cleanup.
AUTHORED_HAIR_MATERIALS = frozenset(TEXTURE9_ALPHA_MATERIALS)
# Aurora's modular body-part path selects a same-name PLT when one exists even
# when the compiled mesh carries a stale or placeholder bitmap. Treating only
# the embedded name as authoritative discarded live armor masks such as the
# pmh0_leg[lr]243 pair (whose meshes say ``spodnie``).
MODULAR_PART_DIRECTORY_PREFIX = "sw_pt_"
MODULAR_MESH_NODE_TYPES = {"aabb", "animmesh", "danglymesh", "skin", "trimesh"}
MODULAR_MODEL_PATTERN = re.compile(
    r"^p(?P<gender>[fm])(?P<race>[a-z])(?P<phenotype>[0-9]+)_(?P<part>[a-z]+[0-9]{3})$",
    re.IGNORECASE,
)

FILE_HEADER_SIZE = 12
NODE_HEADER_SIZE = 112
LIGHT_HEADER_SIZE = 92
EMITTER_HEADER_SIZE = 212
REFERENCE_HEADER_SIZE = 68
MESH_TEXTURE0_OFFSET = 120
MESH_MATERIAL_NAME_OFFSET = 312


def stock_palette_inventory(data_directory: Path) -> dict[str, object]:
    """Record stock PLT lookup names so audits preserve native fallback order."""
    palettes: set[str] = set()
    keys: list[dict[str, str]] = []
    for path in sorted(data_directory.glob("*.key")):
        data = path.read_bytes()
        if len(data) < 64 or data[:8] != b"KEY V1  ":
            raise RuntimeError(f"Invalid NWN KEY archive: {path}")
        _, count, _, table = struct.unpack_from("<IIII", data, 8)
        if table + count * 22 > len(data):
            raise RuntimeError(f"Truncated resource table in {path}")
        keys.append({"name": path.name, "sha256": hashlib.sha256(data).hexdigest()})
        for index in range(count):
            offset = table + index * 22
            if struct.unpack_from("<H", data, offset + 16)[0] == 6:
                palettes.add(data[offset:offset + 16].split(b"\0", 1)[0].decode("ascii").lower())
    if not keys or not palettes:
        raise RuntimeError(f"No stock palette resources indexed under {data_directory}")
    return {
        "formatVersion": 1,
        "description": "Stock NWN PLT resource names from the listed KEY files; resource payloads are not redistributed.",
        "keys": keys,
        "palettes": sorted(palettes),
    }


def native_modular_palettes() -> set[str]:
    global _NATIVE_MODULAR_PALETTES
    if _NATIVE_MODULAR_PALETTES is None:
        if not STOCK_PALETTE_RESOURCES.is_file():
            raise RuntimeError(
                "Missing stock PLT inventory. Run python tools/GenerateTintMapAssets.py "
                '--refresh-stock-palettes --game-data "<NWN install>/data".'
            )
        data = json.loads(STOCK_PALETTE_RESOURCES.read_text(encoding="utf-8"))
        names = data.get("palettes", [])
        if data.get("formatVersion") != 1 or not names or names != sorted(set(names)):
            raise RuntimeError(f"Invalid stock PLT inventory: {STOCK_PALETTE_RESOURCES}")
        _NATIVE_MODULAR_PALETTES = set(names)
        _NATIVE_MODULAR_PALETTES.update(
            path.stem.lower() for directory in hak_directories() for path in directory.glob("*.plt")
            if not is_native_robe_control_plt(path)
        )
    return _NATIVE_MODULAR_PALETTES


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


def read_stock_key_models(data_directory: Path) -> dict[str, tuple[Path, int]]:
    """Index stock MDLs from the game's KEY/BIF layer in engine precedence order."""
    resources: dict[str, tuple[Path, int]] = {}
    install_root = data_directory.parent

    for archive_name in STOCK_KEY_ARCHIVES:
        key_path = data_directory / archive_name
        if not key_path.is_file():
            continue

        data = key_path.read_bytes()
        if len(data) < 64 or data[:4] != b"KEY ":
            raise RuntimeError(f"Invalid NWN KEY archive: {key_path}")

        bif_count, resource_count, bif_offset, resource_offset = struct.unpack_from(
            "<IIII", data, 8
        )
        if bif_offset + bif_count * 12 > len(data):
            raise RuntimeError(f"Truncated BIF table in {key_path}")
        if resource_offset + resource_count * 22 > len(data):
            raise RuntimeError(f"Truncated resource table in {key_path}")

        bif_paths: list[Path] = []
        for index in range(bif_count):
            _, filename_offset, filename_size, _ = struct.unpack_from(
                "<IIHH", data, bif_offset + index * 12
            )
            if filename_offset + filename_size > len(data):
                raise RuntimeError(f"Truncated BIF filename in {key_path}")
            filename = data[filename_offset : filename_offset + filename_size]
            filename = filename.split(b"\0", 1)[0].decode("ascii", errors="strict")
            normalized = Path(filename.replace("\\", "/"))
            candidate = install_root.joinpath(*normalized.parts)
            if not candidate.is_file():
                candidate = data_directory / normalized.name
            bif_paths.append(candidate)

        for index in range(resource_count):
            offset = resource_offset + index * 22
            resref = data[offset : offset + 16].split(b"\0", 1)[0].decode(
                "ascii", errors="strict"
            ).lower()
            resource_type, resource_id = struct.unpack_from("<HI", data, offset + 16)
            if resource_type != STOCK_MODEL_RESOURCE_TYPE:
                continue
            bif_index = resource_id >> 20
            variable_index = resource_id & 0x000F_FFFF
            if bif_index >= len(bif_paths):
                raise RuntimeError(
                    f"Stock model '{resref}' references missing BIF {bif_index} in {key_path}"
                )
            resources[resref] = (bif_paths[bif_index], variable_index)

    if not resources:
        raise RuntimeError(
            f"No stock MDLs were indexed from KEY archives under {data_directory}"
        )
    return resources


def extract_stock_bif_resource(path: Path, variable_index: int) -> bytes:
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20 or header[:4] != b"BIFF":
            raise RuntimeError(f"Invalid NWN BIF archive: {path}")
        variable_count, _, variable_offset = struct.unpack_from("<III", header, 8)
        if variable_index >= variable_count:
            raise RuntimeError(
                f"BIF resource index {variable_index} is outside {path}"
            )

        stream.seek(variable_offset + variable_index * 16)
        entry = stream.read(16)
        if len(entry) != 16:
            raise RuntimeError(f"Truncated BIF resource table in {path}")
        _, data_offset, data_size, resource_type = struct.unpack("<IIII", entry)
        if resource_type != STOCK_MODEL_RESOURCE_TYPE:
            raise RuntimeError(
                f"BIF resource {variable_index} in {path} is not an MDL"
            )
        stream.seek(data_offset)
        payload = stream.read(data_size)
        if len(payload) != data_size:
            raise RuntimeError(f"Truncated BIF model payload in {path}")
        return payload


def stock_model_output_directory(model: str) -> Path:
    if model.startswith("cloak_"):
        return REPOSITORY_ROOT / "sw_pt_cloak"
    if model.startswith("helm_"):
        return REPOSITORY_ROOT / "sw_pt_helm"

    match = MODULAR_MODEL_PATTERN.fullmatch(model)
    if match is None:
        raise RuntimeError(f"No HAK part directory is configured for stock model '{model}'")
    part = re.sub(r"[0-9]+$", "", match.group("part").lower())
    directory = STOCK_MODEL_PART_DIRECTORIES.get(part)
    if directory is None:
        raise RuntimeError(
            f"No HAK part directory is configured for stock model '{model}' ({part})"
        )
    return REPOSITORY_ROOT / directory


def import_stock_models(data_directory: Path) -> None:
    global _ACTIVE_MODELS

    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to import stock models for")
    entry_order = list(entries)

    stock_resources = read_stock_key_models(data_directory.resolve())
    active_models = find_active_models()
    missing_models = sorted(
        model
        for model in entries
        if model not in active_models and model in stock_resources
    )

    for model in missing_models:
        bif_path, variable_index = stock_resources[model]
        output_directory = stock_model_output_directory(model)
        output_directory.mkdir(parents=True, exist_ok=True)
        (output_directory / f"{model}.mdl").write_bytes(
            extract_stock_bif_resource(bif_path, variable_index)
        )

    _ACTIVE_MODELS = None
    # Rebuild the catalog before reading compatibility aliases so a stale row
    # from an older inferred model mapping cannot block its own cleanup.
    write_2da(entries)
    changed_models, material_aliases = synchronize_model_material_aliases(entries)
    removed_outputs = remove_orphaned_outputs(entries)
    removed_materials = remove_orphaned_materials(entries, set(material_aliases))
    write_source_manifest(entries, entry_order)
    write_2da(entries)
    print(
        f"Imported {len(missing_models)} stock MDLs and bound generated tint materials "
        f"in {changed_models} models; removed {removed_outputs} orphaned packed textures "
        f"and {removed_materials} orphaned materials."
    )


def is_inventory_icon_plt(path: Path) -> bool:
    return (
        path.suffix.lower() == ".plt"
        and INVENTORY_ICON_PLT_PATTERN.match(path.stem) is not None
    )


def is_dynamic_cloak_plt(path: Path) -> bool:
    """Whether a PLT is selected as a texture by cloakmodel.2da at runtime.

    These resources are not stable model materials. Multiple cloak rows reuse a
    generic model while changing only the TEXTURE column, so there is no
    per-texture materialname that NWN's material setter can address. Keep them
    native until the generic model/texture combinations are replaced by explicit
    material-bound models.
    """
    return (
        path.suffix.lower() == ".plt"
        and path.parent.name.lower() == "sw_pt_cloak"
        and DYNAMIC_CLOAK_PLT_PATTERN.fullmatch(path.stem) is not None
    )


def is_dynamic_cloak_material(material: str) -> bool:
    return DYNAMIC_CLOAK_PLT_PATTERN.fullmatch(material) is not None


def required_dynamic_cloak_resrefs() -> set[str]:
    """Return every non-stock cloak texture selected by cloakmodel.2da."""
    if not CLOAK_MODEL_2DA.exists():
        raise RuntimeError(f"Missing cloak appearance table: {CLOAK_MODEL_2DA}")

    lines = CLOAK_MODEL_2DA.read_text(encoding="utf-8-sig").splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if {"MODEL", "TEXTURE"}.issubset(set(shlex.split(line)))
        ),
        None,
    )
    if header_index is None:
        raise RuntimeError("cloakmodel.2da has no MODEL/TEXTURE header")

    columns = shlex.split(lines[header_index])
    texture_index = columns.index("TEXTURE") + 1  # Data rows start with their row number.
    resrefs: set[str] = set()
    for line in lines[header_index + 1 :]:
        tokens = shlex.split(line)
        if len(tokens) <= texture_index:
            continue
        texture = tokens[texture_index]
        if texture.isdigit() and int(texture) > STOCK_CLOAK_TEXTURE_MAX:
            resrefs.add(f"cloak_{int(texture):03d}")

    if not resrefs:
        raise RuntimeError("cloakmodel.2da has no non-stock dynamic cloak textures")
    return resrefs


def is_tint_material_plt(path: Path) -> bool:
    return (
        path.suffix.lower() == ".plt"
        and not is_inventory_icon_plt(path)
        and not is_dynamic_cloak_plt(path)
        and not is_native_robe_control_plt(path)
    )


def is_native_robe_control_plt(path: Path) -> bool:
    """Generated controls are runtime metadata, not conversion inputs or artwork.

    Classify by their dedicated location even when malformed, so regeneration
    cannot silently turn a damaged control into a new authoritative tint mask.
    The control audit validates every filename and byte in this location.
    """
    return path.suffix.lower() == ".plt" and path.parent.resolve() == OUTPUT_MTR_DIRECTORY.resolve()


def is_modular_robe(model: str) -> bool:
    match = MODULAR_MODEL_PATTERN.fullmatch(model)
    return match is not None and match.group("part").startswith("robe")


def native_robe_control_bytes() -> bytes:
    # Original PLT V1 header, one legal Skin/shade texel. ReplaceTexturePLT
    # copies the complete appearance scheme independently of the image pixels.
    return PLT_HEADER + struct.pack("<IIII", 8, 0, 1, 1) + bytes((128, 0))


def required_native_robe_controls() -> set[str]:
    # Stock and retained authored PLTs already provide this native metadata.
    # Controls are omitted from native_modular_palettes: their names are always
    # represented by the source manifest during virtual native lookup.
    return _NATIVE_ROBE_SOURCES - native_modular_palettes()


def native_robe_control_errors() -> list[str]:
    expected = required_native_robe_controls()
    paths = list(OUTPUT_MTR_DIRECTORY.glob("*.plt"))
    actual = {path.stem.lower(): path for path in paths}
    errors = []
    if len(actual) != len(paths):
        errors.append("duplicate native robe control PLT names")
    for name in sorted(expected - actual.keys()):
        errors.append(f"{name}: missing native robe control PLT")
    for name in sorted(actual.keys() - expected):
        errors.append(f"{name}: unexpected native robe control PLT")
    for name, path in sorted(actual.items()):
        if path.read_bytes() != native_robe_control_bytes():
            errors.append(f"{name}: native robe control PLT must contain the exact generated 1x1 metadata image")
    return errors


def synchronize_native_robe_controls() -> None:
    expected = required_native_robe_controls()
    payload = native_robe_control_bytes()
    for path in OUTPUT_MTR_DIRECTORY.glob("*.plt"):
        if path.read_bytes() != payload or not is_modular_robe(path.stem):
            raise RuntimeError(f"Refusing to replace an unrecognized native robe control: {path}")
        if path.stem.lower() not in expected:
            path.unlink()
    for name in sorted(expected):
        path = OUTPUT_MTR_DIRECTORY / f"{name}.plt"
        if not path.exists():
            path.write_bytes(payload)


def native_robe_surface_errors(
    models: dict[str, Path],
    entries: dict[str, dict[str, object]],
    rows: list[tuple[str, str, list[int]]],
) -> list[str]:
    """Every flagged material instance must receive a native robe scheme."""
    materials_by_model: dict[str, set[str]] = {}
    for model, material, _ in rows:
        if material in _NATIVE_ROBE_MATERIALS:
            materials_by_model.setdefault(model, set()).add(material)
    errors = []
    for model, materials in materials_by_model.items():
        path = models.get(model)
        if path is None:
            continue  # The model-presence audit reports this separately.
        choices = native_modular_material_choices(path, entries) if is_modular_robe(model) else None
        for selector, _, material in read_model_material_surfaces(path, True):
            if material not in materials:
                continue
            choice = (choices or {}).get(selector)
            if choice is None or choice[0] is None:
                errors.append(f"{model}/{material}: native palette fallback has no proven native robe subtree at {selector}")
    return errors


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


def source_shader_config_lines(material: str) -> list[str]:
    """Read an authored per-material shader declaration stored as a .shd.

    Dafena's mapped hair heads use material-shaped ``.shd`` resources rather
    than ``.mtr`` files. Their directives still describe the surface behavior
    that the tint replacement must preserve.
    """
    if material.lower() not in AUTHORED_HAIR_MATERIALS:
        return []
    path = REPOSITORY_ROOT / "sw_shader" / f"{material.lower()}.shd"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8-sig").splitlines()


def fragment_shaders(lines: list[str]) -> set[str]:
    return {
        line.split(maxsplit=1)[1].strip().lower()
        for line in lines
        if re.match(r"^\s*customshaderFS\s+\S+", line, re.IGNORECASE)
    }


def tint_fragment_shader(
    source_material: str,
    lines: list[str],
) -> str:
    authored_lines = source_shader_config_lines(source_material)
    if (
        source_material.lower() in AUTHORED_HAIR_MATERIALS
        or fragment_shaders(authored_lines + lines) & AUTHORED_HAIR_FRAGMENT_SHADERS
    ):
        return TINT_HAIR_MAPPED_FRAGMENT_SHADER
    return TINT_MAPPED_FRAGMENT_SHADER if uses_mapped_shader(lines) else TINT_FRAGMENT_SHADER


def find_plts(predicate: Callable[[Path], bool]) -> tuple[dict[str, Path], list[Path]]:
    active: dict[str, Path] = {}
    all_paths: list[Path] = []

    # Builder directory order is not the module's runtime HAK priority. Current
    # palette resources are unique; reject future conflicting duplicates rather
    # than silently selecting a different palette from the client.
    for directory in reversed(hak_directories()):
        for path in sorted(directory.glob("*.plt"), key=lambda value: value.name.lower()):
            if not predicate(path):
                continue
            all_paths.append(path)
            existing = active.get(path.stem.lower())
            if existing is not None and existing.read_bytes() != path.read_bytes():
                raise RuntimeError(
                    f"Conflicting PLT resref '{path.stem.lower()}' in {existing} and {path}; "
                    "resolve the duplicate using the module's HAK priority before tint conversion"
                )
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
            if not is_tint_material_plt(path) and not is_native_robe_control_plt(path)
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
    subtree_name: str | None = None,
) -> list[tuple[int, str, int, str | None]]:
    if len(data) < FILE_HEADER_SIZE + 76:
        raise ValueError(f"{path}: expected an NWN1 binary MDL")
    marker, model_size, raw_size = struct.unpack_from("<III", data)
    if marker != 0 or model_size < 76:
        raise ValueError(f"{path}: expected an NWN1 binary MDL")
    model_end = FILE_HEADER_SIZE + model_size
    if model_end + raw_size != len(data):
        raise ValueError(
            f"{path}: declared model/raw section lengths require {model_end + raw_size} bytes, "
            f"but the file has {len(data)} bytes"
        )

    def read_uint32(offset: int) -> int:
        if offset < FILE_HEADER_SIZE or offset + 4 > model_end:
            raise ValueError(f"{path}: truncated uint32 at 0x{offset:x}")
        return struct.unpack_from("<I", data, offset)[0]

    def read_resref(offset: int, length: int) -> str:
        if offset < FILE_HEADER_SIZE or offset + length > model_end:
            raise ValueError(f"{path}: truncated resref at 0x{offset:x}")
        return data[offset : offset + length].split(b"\0", 1)[0].decode(
            "ascii", errors="strict"
        ).lower()

    pending = [(read_uint32(FILE_HEADER_SIZE + 72), subtree_name is None)]
    visited: set[int] = set()
    materials: list[tuple[int, str, int, str | None]] = []
    while pending:
        pointer, in_subtree = pending.pop()
        if pointer in visited:
            continue
        visited.add(pointer)

        node = FILE_HEADER_SIZE + pointer
        if node < FILE_HEADER_SIZE or node + NODE_HEADER_SIZE > model_end:
            raise ValueError(f"{path}: invalid node pointer 0x{pointer:x}")
        in_subtree = in_subtree or read_resref(node + 32, 32) == subtree_name

        child_array_pointer = read_uint32(node + 72)
        child_count = read_uint32(node + 76)
        if child_count > 100_000:
            raise ValueError(f"{path}: invalid child count {child_count}")
        child_array = FILE_HEADER_SIZE + child_array_pointer
        if child_count and child_array + child_count * 4 > model_end:
            raise ValueError(f"{path}: invalid child array")
        pending.extend((read_uint32(child_array + index * 4), in_subtree) for index in range(child_count))

        content = read_uint32(node + 108)
        if content & 0x20 == 0 or not in_subtree:
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


def read_model_material_surfaces(
    path: Path,
    include_implicit_modular_surface: bool = False,
    subtree_name: str | None = None,
) -> list[tuple[str, str, str | None]]:
    """Return per-mesh selectors so native subtree writes cannot affect siblings."""
    data = path.read_bytes()
    if data[:4] == bytes(4):
        return [
            (f"@field:{offset}", texture, material)
            for _, texture, offset, material in read_binary_model_material_fields(path, data, subtree_name)
        ]
    text = data.decode("ascii", errors="strict")
    nodes = list(re.finditer(
        r"(?ims)^\s*node\s+(?P<type>\S+)\s+(?P<name>[^\s]+)[^\r\n]*\r?\n.*?^\s*endnode\b[^\r\n]*(?:\r?\n|$)",
        text,
    ))
    node_names = [match["name"].lower() for match in nodes]
    if len(node_names) != len(set(node_names)):
        raise ValueError(
            f"{path}: repeated ASCII node names cannot be addressed independently; "
            "compile this model with tools/CompileModels.py --apply before binding tint materials"
        )
    selected = {match["name"].lower() for match in nodes} if subtree_name is None else {
        match["name"].lower() for match in nodes if match["name"].lower() == subtree_name
    }
    if subtree_name is not None:
        while True:
            descendants = {
                match["name"].lower() for match in nodes
                if (parent := re.search(r"(?im)^\s*parent\s+(\S+)", match[0]))
                and parent[1].lower() in selected
            }
            if descendants.issubset(selected):
                break
            selected.update(descendants)
    surfaces = []
    for match in nodes:
        name = match["name"].lower()
        if name not in selected:
            continue
        if subtree_name is not None and match["type"].lower() not in MODULAR_MESH_NODE_TYPES:
            continue  # Native subtree replacement collects mesh nodes only.
        texture_match = re.search(r"(?im)^\s*(?:bitmap|texture0)\s+([^\s#]+)", match[0])
        if texture_match is None:
            if not include_implicit_modular_surface or match["type"].lower() not in MODULAR_MESH_NODE_TYPES:
                continue
            texture = path.stem.lower()
        else:
            texture = texture_match[1].lower()
            if texture == "null":
                texture = path.stem.lower()
        material_match = re.search(r"(?im)^\s*materialname\s+([^\s#]+)", match[0])
        material = material_match[1].lower() if material_match else None
        surfaces.append((f"@node:{name}", texture, None if material == "null" else material))
    return surfaces


def pending_model_material_bindings(
    path: Path,
    desired: dict[str, str],
    include_implicit_modular_surface: bool = False,
) -> dict[str, str]:
    pending: dict[str, str] = {}
    try:
        bindings = read_model_material_surfaces(
            path,
            include_implicit_modular_surface,
        )
    except (UnicodeDecodeError, ValueError):
        # Match the conservative discovery fallback used above. A handful of
        # legacy robe helpers carry malformed or nonstandard compiled headers;
        # their raw candidate strings keep source masks alive, but they cannot
        # be rewritten safely without a trustworthy mesh boundary.
        return pending
    for selector, texture, material in bindings:
        key = selector if selector in desired else texture
        target = desired.get(key)
        if target is not None and material != target:
            pending[key] = target
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
            node_name = re.match(r"(?im)^\s*node\s+\S+\s+(\S+)", node).group(1).lower()
            target = normalized.get(f"@node:{node_name}", normalized.get(source))
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
        target = normalized.get(f"@field:{material_offset}", normalized.get(source))
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
            native_references = find_native_modular_tint_references(path, entries) or {}
            for selector, texture, material in read_model_material_surfaces(path):
                if selector in native_references:
                    # Native PLT replacement supersedes real surfaces only
                    # inside the named body-part subtree.
                    continue
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


def find_unpadded_model_material_source(
    model: str,
    entries: dict[str, dict[str, object]],
) -> str | None:
    """Retain historical unpadded catalog sources after native lookup fails.

    This compatibility recovery is not the client's body-part PLT algorithm:
    native lookup formats the index with three digits and takes precedence.
    """
    match = re.fullmatch(r"(.*?)([0-9]+)", model)
    if match is None:
        return None
    candidate = f"{match.group(1)}{int(match.group(2))}"
    if candidate == model or candidate not in entries:
        return None
    return candidate


def modular_palette_candidates(model: str) -> tuple[str, ...]:
    """Follow CreateBodyParts' PLT lookup order, including phenotype fallback."""
    match = MODULAR_MODEL_PATTERN.fullmatch(model)
    if match is None:
        return ()
    gender, race, part = match.group("gender", "race", "part")
    candidates = [model, f"p{gender}{race}0_{part}", f"p{gender}h0_{part}"]
    if gender == "f":
        candidates.append(f"pmh0_{part}")
    return tuple(dict.fromkeys(candidates))


def modular_palette_source(
    model: str,
    entries: dict[str, dict[str, object]],
    native_palettes: set[str],
) -> str | None:
    for candidate in modular_palette_candidates(model):
        if candidate in entries:
            return candidate
        if candidate in native_palettes:
            # A surviving higher-priority PLT stops the native fallback chain;
            # it must not be replaced by a lower-priority converted palette.
            return None
    return None


def find_native_modular_tint_references(
    path: Path,
    entries: dict[str, dict[str, object]],
) -> dict[str, str] | None:
    """Replace the same meshes as the native segmented-body loader.

    CreateBodyParts selects one PLT through the race/phenotype/human fallback
    chain. ReplaceTextureSubtree passes no old-texture filter, so even a valid
    embedded bitmap is superseded by this selected part palette. The native
    operation retains the shared material's other inputs and parameters.
    Models without a selected converted PLT keep their authored material path.
    """
    choices = native_modular_material_choices(path, entries)
    if choices is None:
        return None
    return {selector: source for selector, (source, _, _) in choices.items() if source is not None}


def authored_material_sources() -> dict[str, dict[str, object]]:
    """Original shared-material inputs, captured before PLT conversion.

    The native loader replaces the local diffuse, while an already loaded MTR
    and its texture slots remain in force. Current generated MTRs cannot serve
    as that provenance: they already contain our tint shader and placeholder.
    """
    global _MATERIAL_SOURCES
    if _MATERIAL_SOURCES is None:
        metadata = json.loads(MATERIAL_SOURCES.read_text(encoding="utf-8"))
        _MATERIAL_SOURCES = metadata["materials"]
        _MATERIAL_BITMAP_ALIASES.update(metadata.get("bitmapAliases", {}))
    return _MATERIAL_SOURCES


def raw_model_bitmaps(path: Path) -> dict[str, str]:
    """Read actual bitmap values without inventing a same-name implicit texture."""
    data = path.read_bytes()
    if data[:4] == bytes(4):
        return {
            f"@field:{material_offset}": data[texture_offset:texture_offset + 64].split(b"\0", 1)[0].decode("ascii").lower()
            for texture_offset, _, material_offset, _ in read_binary_model_material_fields(path, data)
        }
    result = {}
    for node in re.finditer(r"(?ims)^\s*node\s+\S+\s+(\S+)[^\r\n]*\r?\n(.*?)^\s*endnode\b", data.decode("ascii")):
        bitmap = re.search(r"(?im)^\s*(?:bitmap|texture0)\s+(\S+)", node[2])
        result[f"@node:{node[1].lower()}"] = bitmap[1].lower() if bitmap else ""
    return result


def native_modular_material_choices(
    path: Path,
    entries: dict[str, dict[str, object]],
) -> dict[str, tuple[str | None, str, list[str]]] | None:
    """Choose visible PLT and retained shared material independently per mesh.

    An explicit authored MTR slot zero takes priority over the replaced local
    PLT, even when its raster resource is missing. Such a surface keeps its
    original material and is not tint eligible.
    Other meshes keep the original MTR's maps and parameters while using the
    native-selected part palette; an absent MTR keeps the base profile.
    """
    if not path.parent.name.lower().startswith(MODULAR_PART_DIRECTORY_PREFIX):
        return None
    source = modular_palette_source(path.stem.lower(), entries, native_modular_palettes())
    if source is None:
        return None
    surfaces = read_model_material_surfaces(path, True, subtree_name=path.stem.lower())
    if not surfaces:
        return None
    bitmaps = raw_model_bitmaps(path)
    original_materials = authored_material_sources()
    choices = {}
    for selector, _, _ in surfaces:
        bitmap = _MATERIAL_BITMAP_ALIASES.get(bitmaps[selector], bitmaps[selector])
        profile = original_materials.get(bitmap, {})
        lines = list(profile.get("lines", []))
        texture0 = next((line.split()[1].lower() for line in lines if re.match(r"^\s*texture0\s+\S+", line, re.IGNORECASE)), "")
        fixed = texture0 not in {"", "null"}
        original_material = bitmap if profile else ""
        if fixed and (bitmap in entries or not source_mtr_paths(bitmap)):
            original_material = scoped_material_alias(bitmap, "authored:original")
            _PRESERVED_MATERIALS[original_material] = (bitmap, lines)
        choices[selector] = (None if fixed else source, original_material, lines)
    return choices


def material_profile_signature(source: str, profile: tuple[str, list[str]] | None = None):
    profile_name = profile[0] if profile is not None else "@canonical"
    key = (source, profile_name)
    if key not in _PROFILE_SIGNATURES:
        text = tint_material_text(mtr_path(source), source, "mask", 1, 1, source, profile)
        _PROFILE_SIGNATURES[key] = tuple(sorted(
            (directive, " ".join(line.split()).lower())
            for line in text.splitlines()
            if (directive := mtr_directive_key(line)) is not None
            and not re.match(r"^\s*texture\d+\s+null\s*$", line, re.IGNORECASE)
        ))
    return _PROFILE_SIGNATURES[key]


def build_model_material_plan(
    entries: dict[str, dict[str, object]],
) -> tuple[
    list[tuple[str, str, list[int]]],
    dict[Path, dict[str, str]],
    dict[str, str],
]:
    global _NATIVE_ROBE_MATERIALS, _NATIVE_ROBE_SOURCES
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
    records: dict[tuple[str, str, str, str], dict[str, object]] = {}
    restored_bindings: dict[Path, dict[str, str]] = {}

    for model, path in sorted(models.items()):
        scope = model_material_scope(model, path)
        references = find_model_tint_material_references(path, materials, alias_sources)
        native_choices = native_modular_material_choices(path, entries)
        profiles = {}
        if native_choices is not None:
            outside_textures = {
                texture for selector, texture, _ in read_model_material_surfaces(path, True)
                if selector not in native_choices
            }
            references = {
                texture: source for texture, source in references.items()
                if texture in outside_textures
            }
            for selector, (source, profile_name, profile_lines) in native_choices.items():
                if source is None:
                    restored_bindings.setdefault(path, {})[selector] = profile_name
                    continue
                references[selector] = source
                profile = (profile_name, profile_lines)
                if material_profile_signature(source, profile) != material_profile_signature(source):
                    profiles[selector] = profile
        if native_choices is None and model in authored_texture_overrides:
            references.update({
                texture: authored_texture_overrides[model]
                for texture, _ in read_model_material_bindings(path)
            })
        if not references and native_choices is None:
            unpadded_source = find_unpadded_model_material_source(model, entries)
            if unpadded_source is not None:
                references.update({
                    texture: unpadded_source
                    for texture, _ in read_model_material_bindings(path)
                })
            elif model in human_fallbacks:
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
            profile = profiles.get(current)
            profile_key = "" if profile is None else hashlib.sha256(
                repr(material_profile_signature(source, profile)).encode("utf-8")
            ).hexdigest()[:12]
            key = (model, source, scope, profile_key)
            record = records.setdefault(
                key,
                {"model": model, "source": source, "scope": scope, "path": path, "current": set(), "profile": profile, "profile_key": profile_key, "native_robe": False},
            )
            if is_modular_robe(model) and native_choices is not None and current in native_choices:
                record["native_robe"] = True
            current_materials = record["current"]
            assert isinstance(current_materials, set)
            current_materials.add(current)

    # A same-name MTR can still replace an implicit PLT at render time, but the
    # native material setter cannot address that fallback unless a real MDL has
    # an explicit materialname. Never advertise inferred model/material pairs in
    # tintmap.2da: the editor may only offer bindings proven by an active model.

    scopes_by_source: dict[str, set[str]] = {}
    native_robe_profiles = {
        (str(record["source"]), str(record["scope"]), str(record["profile_key"]))
        for record in records.values() if record["native_robe"]
    }
    for record in records.values():
        source = str(record["source"])
        scopes_by_source.setdefault(source, set()).add(str(record["scope"]))

    rows: set[tuple[str, str, tuple[int, ...]]] = set()
    desired_bindings: dict[Path, dict[str, str]] = restored_bindings
    active_aliases: dict[str, str] = {}
    native_robe_materials: set[str] = set()
    native_robe_sources: set[str] = set()
    for record in records.values():
        model = str(record["model"])
        source = str(record["source"])
        scope = str(record["scope"])
        path = record["path"]
        material = source
        isolate_scripted_consumer = not record["native_robe"] and (
            source, scope, str(record["profile_key"])
        ) in native_robe_profiles
        if record["profile"] is not None or isolate_scripted_consumer or (path is not None and scope.startswith("part:") and len(scopes_by_source[source]) > 1):
            alias_scope = scope if record["profile"] is None else f"{scope}:profile:{record['profile_key']}"
            if isolate_scripted_consumer:
                # Some legacy resources share a robe material but lack the
                # native named-subtree route. Preserve their scripted defaults
                # without changing the material identity of proven consumers.
                alias_scope += ":scripted"
            material = scoped_material_alias(source, alias_scope)
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
            if record["profile"] is not None:
                _PROFILE_ALIASES[material] = record["profile"]

        if path is not None:
            current_materials = record["current"]
            assert isinstance(current_materials, set)
            for current in current_materials:
                desired_bindings.setdefault(path, {})[str(current)] = material

        layers = tuple(int(layer) for layer in entries[source]["layers"])
        rows.add((model, material, layers))
        if record["native_robe"]:
            native_robe_materials.add(material)
            native_robe_sources.add(source)

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
                    or any(key.startswith("@") for key in desired)
                ),
            )
        )
    }

    _NATIVE_ROBE_MATERIALS = native_robe_materials
    _NATIVE_ROBE_SOURCES = native_robe_sources
    return (
        [(model, material, list(layers)) for model, material, layers in sorted(rows)],
        pending_bindings,
        active_aliases,
    )


def build_model_material_rows(
    entries: dict[str, dict[str, object]],
) -> list[tuple[str, str, list[int]]]:
    rows, _, _ = build_model_material_plan(entries)
    return rows


def find_uncompiled_tint_models(
    active_models: dict[str, Path],
    model_material_rows: list[tuple[str, str, list[int]]],
) -> list[Path]:
    """Require compiled resources only for active models owning tint bindings.

    The material plan identifies the resource owners; a separate strict binary
    audit validates their headers and mesh fields. This format check prevents
    converted meshes from relying on the client's runtime ASCII compiler,
    without imposing a new rule on unrelated models or lower-priority resources
    that the client will not load.
    """
    uncompiled: list[Path] = []
    for model in sorted({model for model, _, _ in model_material_rows}):
        path = active_models.get(model)
        if path is None:
            continue  # The catalog's missing-model audit reports this separately.
        with path.open("rb") as stream:
            if stream.read(4) != b"\0\0\0\0":
                uncompiled.append(path)
    return uncompiled


def find_invalid_binary_tint_models(
    active_models: dict[str, Path],
    model_material_rows: list[tuple[str, str, list[int]]],
) -> dict[Path, str]:
    """Do not let the reference scanner hide malformed active tint binaries."""
    invalid: dict[Path, str] = {}
    for model in sorted({model for model, _, _ in model_material_rows}):
        path = active_models.get(model)
        if path is None:
            continue
        data = path.read_bytes()
        if data[:4] != b"\0\0\0\0":
            continue  # The compilation-format audit reports ASCII separately.
        try:
            read_binary_model_material_fields(path, data)
        except (ValueError, UnicodeDecodeError) as error:
            invalid[path] = str(error)
    return invalid


def find_used_tint_materials(entries: dict[str, dict[str, object]]) -> set[str]:
    models = find_active_models()
    materials = set(entries)
    alias_sources = build_alias_source_lookup(entries)
    used: set[str] = set()
    for path in models.values():
        used.update(find_model_tint_material_references(path, materials, alias_sources).values())
        native_references = find_native_modular_tint_references(path, entries)
        if native_references is not None:
            used.update(native_references.values())
    used.update(
        find_modular_human_material_fallbacks(models, entries, alias_sources).values()
    )

    # Stock resources missing from the HAK source tree conventionally use the
    # same model/material resref; keep their converted source masks available.
    used.update(material for material in materials if material not in models)
    used.update(materials & find_table_referenced_resrefs())
    used.update(read_preserved_2da_material_sources(entries).values())
    # cloakmodel.2da chooses these resrefs as textures on shared generic models.
    # They cannot be addressed as independent materials and must stay PLTs.
    used.difference_update(
        material for material in materials if is_dynamic_cloak_material(material)
    )
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
    struct.pack_into("<I", header, 8, 0x000A1007)  # CAPS, HEIGHT, WIDTH, PIXELFORMAT, LINEARSIZE, MIPMAPCOUNT
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, data_length)
    struct.pack_into("<I", header, 28, 1)  # Only the base level is stored.
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
    write_packed_texture_settings(path)


def write_packed_texture_settings(path: Path) -> None:
    # NWN's compressed-texture loader uses TXI mipmap policy when uploading.
    # Without this, it reads an implied mip chain past our base-level payload.
    # Layer IDs are categorical, so generating filtered mipmaps is also wrong.
    path.with_suffix(".txi").write_text("mipmap 0\n", encoding="ascii")


def validate_packed_texture_path(path: Path) -> None:
    resolved = path.resolve()
    if resolved.suffix.lower() != ".dds" or resolved.parent not in {
        directory.resolve() for directory in TINT_DIRECTORIES
    }:
        raise RuntimeError(f"Refusing to modify non-tint texture: {resolved}")


def remove_packed_texture(path: Path) -> None:
    validate_packed_texture_path(path)
    path.unlink(missing_ok=True)
    path.with_suffix(".txi").unlink(missing_ok=True)


def move_packed_texture(source: Path, target: Path) -> None:
    validate_packed_texture_path(source)
    validate_packed_texture_path(target)
    source.replace(target)
    source.with_suffix(".txi").unlink(missing_ok=True)
    write_packed_texture_settings(target)


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
    profile = _PROFILE_ALIASES.get(material)
    text = tint_material_text(path, material, texture, width, height, source_material, profile,
                              native_palette=material in _NATIVE_ROBE_MATERIALS)
    path.write_text(text, encoding="utf-8", newline="\n")


def tint_material_text(
    path: Path,
    material: str,
    texture: str,
    width: int,
    height: int,
    source_material: str | None = None,
    profile: tuple[str, list[str]] | None = None,
    *,
    native_palette: bool = False,
) -> str:
    source_material = source_material or material
    generated_lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    source_paths = source_mtr_paths(source_material)
    source_mtr_lines = (
        source_paths[-1].read_text(encoding="utf-8-sig").splitlines()
        if source_paths
        else []
    )
    source_lines = merge_mtr_lines(
        source_shader_config_lines(source_material),
        source_mtr_lines,
    )
    if not source_lines and source_material != material:
        generated_source = mtr_path(source_material)
        if generated_source.exists():
            source_lines = generated_source.read_text(encoding="utf-8-sig").splitlines()
    original_lines = merge_mtr_lines(source_lines, generated_lines)
    if profile is not None:
        source_material, original_lines = profile
    mapped_shader = uses_mapped_shader(original_lines)
    original_fragment_shaders = fragment_shaders(original_lines)
    selected_fragment_shader = tint_fragment_shader(source_material, original_lines)
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
        r"parameter\s+(?:float|int)\s+(?:tintMapWidth|tintMapHeight|useTexture1Alpha|useTexture9Alpha|useNativePalette|"
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
            f"customshaderFS {selected_fragment_shader}",
            "texture0 plt_white",
            f"texture7 {texture}",
            "texture10 plt_palette",
            f"parameter float tintMapWidth {float(width):.1f}",
            f"parameter float tintMapHeight {float(height):.1f}",
        )
        + tint_palette_parameter_lines(native_palette)
    )
    if uses_texture1_alpha:
        lines.append("parameter float useTexture1Alpha 1.0")
    if texture9_alpha:
        lines.append(f"texture9 {texture9_alpha}")
        lines.append("parameter float useTexture9Alpha 1.0")
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"


def tint_palette_parameter_lines(native_palette: bool) -> tuple[str, ...]:
    if not native_palette:
        return TINT_ROW_PARAMETER_LINES
    # Negative means use the robe's native scheme. A received nonnegative row
    # remains an explicit override, including future custom material updates.
    return tuple(f"parameter float {name} -1.0" for name, _ in TINT_ROW_PARAMETERS) + (
        "parameter float useNativePalette 1.0",
    )


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
    material_lines = {line.strip() for line in mtr.splitlines()}
    return (
        any(
            f"customshaderfs {shader}" in mtr
            for shader in (
                TINT_FRAGMENT_SHADER,
                TINT_MAPPED_FRAGMENT_SHADER,
                TINT_HAIR_MAPPED_FRAGMENT_SHADER,
            )
        )
        and f"texture7 {texture}" in mtr
        and f"parameter float tintmapwidth {float(width):.1f}" in mtr
        and f"parameter float tintmapheight {float(height):.1f}" in mtr
        and all(
            line.lower() in material_lines
            for line in TINT_ROW_PARAMETER_LINES
        )
        and not any(
            key is not None and len(key) == 3 and key[0] == "parameter" and key[2] in OBSOLETE_TINT_PARAMETERS
            for key in (mtr_directive_key(line) for line in mtr.splitlines())
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
            # Generation is also the repair path for a stale compatibility row.
            # The audit still rejects a mismatched 2DA, but an obsolete row must
            # not prevent regeneration from the authoritative manifest.
            continue
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
            if columns[1:4] == ["****", "****", "****"]:
                existing_rows.append((label, model, material, []))
                next_label = max(next_label, label + 1)
                continue
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
    # the first column. Keep established physical positions stable, but blank a
    # retired mapping instead of exposing an inferred material that no active
    # MDL can address. New proven bindings are appended.
    for label, model, material, old_layers in existing_rows:
        current = rows_by_pair.pop((model, material), None)
        if current is None:
            lines.append(f"{label:<4} {'****':<17} {'****':<17} ****")
            continue
        layers = ",".join(str(value) for value in current[2])
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
    global _NATIVE_MODULAR_PALETTES
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
    # inventory icons and cloak textures are engine requirements and are excluded.
    for path in all_paths:
        resolved = path.resolve()
        if REPOSITORY_ROOT.resolve() not in resolved.parents or not is_tint_material_plt(resolved):
            raise RuntimeError(f"Refusing to delete unexpected path: {resolved}")
        resolved.unlink()

    _NATIVE_MODULAR_PALETTES = None
    refresh_native_robe_assets(entries, material_aliases)

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

    refresh_native_robe_assets(entries, active_aliases)
    return changed_models, active_aliases


def generate_preserving_manifest() -> None:
    """Convert active source PLTs without pruning unrelated generated assets."""
    global _ACTIVE_RENDER_SURFACES, _NATIVE_MODULAR_PALETTES

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
    _NATIVE_MODULAR_PALETTES = None

    selected_sources = set(active_materials)
    changed_models, _ = synchronize_selected_model_material_aliases(entries, selected_sources)

    retained_outputs = {
        packed_dds_path(material, entry).resolve()
        for material, entry in entries.items()
    }
    for old_output in previous_outputs - retained_outputs:
        if old_output.exists():
            remove_packed_texture(old_output)

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
            move_packed_texture(current_path, target_path)

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
            remove_packed_texture(old_output)


def synchronize_model_material_aliases(
    entries: dict[str, dict[str, object]],
    preserve_legacy_aliases: bool = True,
) -> tuple[int, dict[str, str]]:
    preserved_aliases = read_preserved_2da_material_sources(entries) if preserve_legacy_aliases else {}
    _, pending_bindings, planned_aliases = build_model_material_plan(entries)
    for alias, (_, lines) in sorted(_PRESERVED_MATERIALS.items()):
        path = REPOSITORY_ROOT / "sw_item" / f"{alias}.mtr"
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8", newline="\n")
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

    refresh_native_robe_assets(entries, active_aliases)
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
                remove_packed_texture(path)
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
            move_packed_texture(current_path, target_path)

        relocated.add(target_path.resolve())
        entry["output"] = target_path.relative_to(REPOSITORY_ROOT).as_posix()
        if number % 500 == 0 or number == len(entries):
            print(f"Relocated {number}/{len(entries)}", flush=True)

    synchronize_model_material_aliases(entries)
    write_source_manifest(entries)
    write_2da(entries)
    print("Packed tint maps were split across dedicated tint HAK directories.", flush=True)


def refresh_native_robe_assets(entries: dict[str, dict[str, object]], aliases: dict[str, str]) -> None:
    """Keep metadata controls and robe fallback parameters coherent with the plan."""
    synchronize_native_robe_controls()
    for material in sorted(set(entries) | set(aliases)):
        path = mtr_path(material)
        native = material in _NATIVE_ROBE_MATERIALS
        previous_native = path.exists() and re.search(
            r"(?im)^\s*parameter\s+float\s+useNativePalette\b", path.read_text(encoding="utf-8-sig")
        ) is not None
        if not native and not previous_native:
            continue
        source = aliases.get(material, material)
        entry = entries[source]
        update_mtr(path, material, str(entry.get("texture") or source),
                   int(entry["width"]), int(entry["height"]), source_material=source)


def refresh_materials() -> None:
    """Regenerate MTR declarations without rewriting meshes or packed maps."""
    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to refresh")

    _, _, active_aliases = build_model_material_plan(entries)
    synchronize_native_robe_controls()
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


def refresh_model_bindings() -> None:
    """Restore native part-palette coverage without rewriting packed pixels."""
    entries = load_source_manifest()
    order = list(entries)
    changed, aliases = synchronize_model_material_aliases(entries, preserve_legacy_aliases=False)
    retired = remove_orphaned_materials(entries, set(aliases))
    write_source_manifest(entries, order)
    write_2da(entries)
    print(
        f"Refreshed tint bindings in {changed} models and retired {retired} unused MTR aliases; "
        "packed maps are unchanged.", flush=True,
    )


def refresh_material(material: str) -> None:
    """Regenerate one source material and its recorded scoped aliases."""
    source = material.lower()
    entries = load_source_manifest()
    _, _, active_aliases = build_model_material_plan(entries)
    refresh_native_robe_assets(entries, active_aliases)
    entry = entries.get(source)
    if entry is None:
        raise RuntimeError(f"Unknown tint source material '{source}'")

    texture = str(entry.get("texture") or source)
    width = int(entry["width"])
    height = int(entry["height"])
    update_mtr(mtr_path(source), source, texture, width, height)
    aliases = [str(value).lower() for value in entry.get("aliases", [])]
    for alias in aliases:
        update_mtr(
            mtr_path(alias),
            alias,
            texture,
            width,
            height,
            source_material=source,
        )
    print(f"Refreshed tint material {source} and {len(aliases)} aliases.", flush=True)


def retain_dynamic_cloak_plts() -> None:
    """Remove only invalid generated cloak bindings after restoring native PLTs."""
    entries = load_source_manifest()
    manifest_order = list(entries)
    removed_sources = {
        source for source in entries if is_dynamic_cloak_material(source)
    }
    if not removed_sources:
        print("No generated dynamic cloak materials remain.", flush=True)
        return

    for source in removed_sources:
        source_path = REPOSITORY_ROOT / str(entries[source]["source"])
        if not source_path.exists() or not is_dynamic_cloak_plt(source_path):
            raise RuntimeError(
                f"Refusing to remove generated cloak material without its native PLT: {source}"
            )

    retained_entries = {
        source: entry for source, entry in entries.items() if source not in removed_sources
    }
    retained_materials = set(retained_entries)
    retained_outputs = {
        packed_dds_path(source, entry).resolve()
        for source, entry in retained_entries.items()
    }
    for entry in retained_entries.values():
        retained_materials.update(str(value).lower() for value in entry.get("aliases", []))

    removed_outputs = 0
    removed_materials = 0
    for source in sorted(removed_sources):
        entry = entries[source]
        output = packed_dds_path(source, entry).resolve()
        if output not in retained_outputs and output.exists():
            if not any(directory.resolve() in output.parents for directory in TINT_DIRECTORIES):
                raise RuntimeError(f"Refusing to delete non-tint output: {output}")
            remove_packed_texture(output)
            removed_outputs += 1

        generated_materials = {source} | {
            str(value).lower() for value in entry.get("aliases", [])
        }
        for material in generated_materials:
            if material in retained_materials:
                raise RuntimeError(
                    f"Dynamic cloak material '{material}' is still used by a retained entry"
                )
            path = mtr_path(material).resolve()
            if path.exists():
                if path.parent != OUTPUT_MTR_DIRECTORY.resolve():
                    raise RuntimeError(f"Refusing to delete non-tint material: {path}")
                path.unlink()
                removed_materials += 1

    write_source_manifest(
        retained_entries,
        [source for source in manifest_order if source in retained_entries],
    )
    write_2da(retained_entries)
    print(
        f"Retained {len(removed_sources)} native dynamic cloak PLTs; removed "
        f"{removed_outputs} unreferenced packed maps and {removed_materials} invalid materials.",
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


def refresh_packed_metadata() -> None:
    """Repair single-level DDS/TXI metadata without recompressing any pixels."""
    entries = load_source_manifest()
    if not entries:
        raise RuntimeError("No tint source manifest exists to refresh")
    outputs = {
        packed_dds_path(model, entry): (int(entry["width"]), int(entry["height"]))
        for model, entry in entries.items()
    }
    # Validate the complete input before changing any resource.
    for path, (width, height) in outputs.items():
        validate_packed_texture_path(path)
        error = check_dds_payload(path, width, height)
        if error:
            raise RuntimeError(f"{path.name}: {error}")
    changed_headers = 0
    for path, (width, height) in outputs.items():
        raw = path.read_bytes()
        header = dds_header(width, height, len(raw) - 128)
        if raw[:128] != header:
            path.write_bytes(header + raw[128:])
            changed_headers += 1
        write_packed_texture_settings(path)
    print(
        f"Refreshed {len(outputs)} single-level DDS/TXI pairs "
        f"({changed_headers} headers changed); compressed pixels are unchanged.",
        flush=True,
    )


def check_dds_payload(path: Path, width: int, height: int) -> str | None:
    if not path.exists():
        return "missing DDS"
    if width <= 0 or height <= 0:
        return "DDS dimensions must be positive"
    raw = path.read_bytes()
    blocks_wide = (width + 3) // 4
    blocks_high = (height + 3) // 4
    expected = 128 + blocks_wide * blocks_high * 16
    if len(raw) != expected:
        return f"DDS length {len(raw)} != {expected}"
    if raw[:4] != b"DDS " or raw[84:88] != b"ATI2":
        return "DDS is not ATI2/BC5"
    if struct.unpack_from("<I", raw, 4)[0] != 124 or struct.unpack_from("<I", raw, 76)[0] != 32:
        return "DDS header size is invalid"
    if struct.unpack_from("<I", raw, 20)[0] != expected - 128:
        return "DDS linear size disagrees with base-level payload"
    actual_height, actual_width = struct.unpack_from("<II", raw, 12)
    if (actual_width, actual_height) != (width, height):
        return f"DDS dimensions {(actual_width, actual_height)} != {(width, height)}"
    return None


def check_dds(path: Path, width: int, height: int) -> str | None:
    error = check_dds_payload(path, width, height)
    if error:
        return error
    raw = path.read_bytes()
    if not struct.unpack_from("<I", raw, 8)[0] & 0x20000 or struct.unpack_from("<I", raw, 28)[0] != 1:
        return "DDS must explicitly declare one mip level"
    txi_path = path.with_suffix(".txi")
    if not txi_path.exists():
        return "missing TXI mipmap 0; NWN would read past the base-level DDS payload"
    directives = [
        line.split("//", 1)[0].strip().lower().split()
        for line in txi_path.read_text(encoding="utf-8-sig").splitlines()
    ]
    if [tokens for tokens in directives if tokens[:1] == ["mipmap"]] != [["mipmap", "0"]]:
        return "TXI must declare mipmap 0 exactly once for a single-level DDS"
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


def native_metal_palette_errors(path: Path) -> list[str]:
    """Both metal categories use native bank 2 (pal_armor01), including alpha.

    Body parts, heads, helmets, tails and wings all select this same bank for
    Metal1 and Metal2. These are all converted sources that contain Metal2.
    Keep two atlas blocks so existing scripted row offsets remain compatible.
    """
    error = check_tga_header(path, 256, PALETTE_TEXTURE_HEIGHT, bits_per_pixel=32)
    if error:
        return [error]
    raw = path.read_bytes()
    start = 18 + raw[0]
    row_bytes = 256 * 4
    if raw[2] != 2 or len(raw) != start + row_bytes * PALETTE_TEXTURE_HEIGHT:
        return ["palette atlas must contain complete uncompressed RGBA rows"]
    metal1 = raw[start + 352 * row_bytes:start + 528 * row_bytes]
    metal2 = raw[start + 528 * row_bytes:start + 704 * row_bytes]
    if metal1 != metal2:
        return ["Metal2 must use the same pal_armor01 RGBA rows as native Metal1"]
    return []


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
    )
    for key in singleton_keys:
        count = len(directives.get(key, []))
        if count != 1:
            errors.append(f"directive {' '.join(key)} occurs {count} times")
    for uniform_name, _ in TINT_ROW_PARAMETERS:
        for line in directives.get(("parameter", "float", uniform_name.lower()), []):
            if len(line.split()) != 4:
                errors.append(
                    f"palette row {uniform_name} must declare exactly one float value; "
                    "multiple values select a vector upload that cannot update the scalar shader uniform"
                )
    native_flags = directives.get(("parameter", "float", "usenativepalette"), [])
    if len(native_flags) > 1 or any(len(line.split()) != 4 for line in native_flags):
        errors.append("useNativePalette must declare a single scalar float when present")
    for key in directives:
        if len(key) == 3 and key[0] == "parameter" and key[2] in OBSOLETE_TINT_PARAMETERS:
            errors.append(f"obsolete tint parameter {key[2]} has no shader uniform")

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
        f"customshaderfs {TINT_HAIR_MAPPED_FRAGMENT_SHADER}",
    }:
        errors.append(f"unexpected tint fragment shader '{fragment_shaders[0]}'")

    mapped_shader = uses_mapped_shader(lines)
    if fragment_shaders:
        fragment_shader = fragment_shaders[0].split(maxsplit=1)[1].lower()
        if mapped_shader and fragment_shader not in {
            TINT_MAPPED_FRAGMENT_SHADER,
            TINT_HAIR_MAPPED_FRAGMENT_SHADER,
        }:
            errors.append("mapped tint material does not use the mapped tint shader")
        elif not mapped_shader and fragment_shader != TINT_FRAGMENT_SHADER:
            errors.append("PLT-only tint material incorrectly uses the mapped tint shader")

    return errors


def tint_shader_material_errors(shader: str) -> list[str]:
    """Require specular setup to consume the final tint, not the placeholder.

    inc_standard caches specularity, metallicness, roughness and specular color
    during SetupStandardShaderInputs. Restoring palette alpha alone leaves its
    missing-texture metallic fallback intact. Ignore comments so a description
    of the required fix cannot satisfy the executable ordering check.
    """
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", shader, flags=re.DOTALL)
    if (
        re.search(r"(?m)^\s*#define\s+NORMAL_MAP\s+0\b", code)
        and re.search(r"\btexture2D\s*\(\s*texUnit1\b", code)
        and not re.search(
            r"#if\s+NORMAL_MAP\s*!=\s*1\s*"
            r"uniform\s+sampler2D\s+texUnit1\s*;\s*#endif",
            code,
        )
    ):
        return ["must declare the cutout alpha sampler when normal mapping is disabled"]
    statements = (
        r"\bSetupStandardShaderInputs\s*\(\s*\)\s*;",
        r"\bfEnvMapLevel\s*=\s*1\.0\s*-\s*paletteColor\.a\s*;",
        r"\bFragmentColor\s*=\s*vec4\s*\(\s*surfaceColor\s*,\s*1\.0\s*\)\s*;",
        r"#if\s+LIGHTING\s*==\s*1\s*&&\s*\(\s*FRAGMENT_LIGHTING\s*==\s*1\s*\|\|\s*NORMAL_MAP\s*==\s*1\s*\)\s*&&\s*SPECULAR_LIGHT\s*==\s*1\s*"
        r"SetupSpecularity\s*\(\s*FragmentColor\.rgb\s*\*\s*materialFrontDiffuse\.rgb\s*\)\s*;\s*#endif",
        r"\bApplyStandardShader\s*\(\s*\)\s*;",
    )
    cursor = 0
    for statement in statements:
        match = re.search(statement, code[cursor:])
        if match is None:
            return ["must rebuild standard specularity from final palette coverage and diffuse color before lighting"]
        cursor += match.end()
    return []


def native_robe_shader_errors(shader: str) -> list[str]:
    """Keep explicit material rows ahead of the native robe fallback."""
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", shader, flags=re.DOTALL)
    required = (
        r"uniform\s+float\s+PLTscheme\s*\[\s*15\s*\]",
        r"uniform\s+float\s+useNativePalette\b",
        r"if\s*\(\s*v\s*<\s*0\.0\s*&&\s*useNativePalette\s*>\s*0\.5\s*\)",
        r"mod\s*\(\s*floor\s*\(\s*PLTscheme\s*\[\s*int\s*\(\s*layer\s*\)\s*\]\s*\*\s*1792\.0\s*\+\s*0\.5\s*\)\s*,\s*256\.0\s*\)",
    )
    if any(re.search(pattern, code) is None for pattern in required):
        return ["native robe palette fallback must decode the 256-row native blocks and preserve nonnegative scripted rows"]
    return []


def preserved_material_errors() -> list[str]:
    errors = []
    for alias, (source, lines) in sorted(_PRESERVED_MATERIALS.items()):
        path = REPOSITORY_ROOT / "sw_item" / f"{alias}.mtr"
        expected = "\n".join(lines).strip() + "\n"
        if not path.is_file():
            errors.append(f"{alias}: missing preserved authored material '{source}'")
        elif path.read_text(encoding="utf-8-sig") != expected:
            errors.append(f"{alias}: preserved authored material '{source}' differs from its original shader/texture inputs")
    return errors


def audit() -> None:
    entries = load_source_manifest()
    errors: list[str] = []
    texture_sources: dict[str, set[tuple[str, int, int]]] = {}
    if not entries:
        errors.append("tint source manifest is empty")

    model_material_rows: list[tuple[str, str, list[int]]] = []
    compiled_tint_model_count = 0
    pending_model_bindings: dict[Path, dict[str, str]] = {}
    active_aliases: dict[str, str] = {}
    if entries:
        model_material_rows, pending_model_bindings, active_aliases = build_model_material_plan(entries)
        errors.extend(native_robe_control_errors())
        errors.extend(preserved_material_errors())
        active_models = find_active_models()
        errors.extend(native_robe_surface_errors(active_models, entries, model_material_rows))
        uncompiled_tint_models = find_uncompiled_tint_models(active_models, model_material_rows)
        compiled_tint_model_count = len({model for model, _, _ in model_material_rows}) - len(uncompiled_tint_models)
        if uncompiled_tint_models:
            examples = ", ".join(
                str(path.relative_to(REPOSITORY_ROOT))
                for path in uncompiled_tint_models[:10]
            )
            errors.append(
                f"{len(uncompiled_tint_models)} active tint-bound models are still ASCII and must be compiled: "
                f'{examples}. Run python tools/CompileModels.py --tint-bound --game-data "<NWN install>/data" '
                "--apply from the HAK repository "
                "and review its validation report before packaging."
            )
        for error in find_invalid_binary_tint_models(active_models, model_material_rows).values():
            errors.append(f"Invalid compiled tint model: {error}")
        unaddressable_rows = [
            (model, material)
            for model, material, _ in model_material_rows
            if model not in active_models
        ]
        if unaddressable_rows:
            examples = ", ".join(
                f"{model}/{material}"
                for model, material in unaddressable_rows[:10]
            )
            errors.append(
                f"{len(unaddressable_rows)} tintmap.2da rows have no explicit HAK model "
                f"whose material can be addressed at runtime: {examples}"
            )
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
    active_dynamic_cloak_plts, all_dynamic_cloak_plts = find_plts(is_dynamic_cloak_plt)
    generated_dynamic_cloaks = [
        material for material in entries if is_dynamic_cloak_material(material)
    ]
    if generated_dynamic_cloaks:
        errors.append(
            f"{len(generated_dynamic_cloaks)} runtime-selected cloak PLTs were converted to materials"
        )
    required_dynamic_cloaks = required_dynamic_cloak_resrefs()
    missing_dynamic_cloaks = required_dynamic_cloaks - set(active_dynamic_cloak_plts)
    if missing_dynamic_cloaks:
        errors.append(
            f"{len(missing_dynamic_cloaks)} runtime-selected native cloak PLTs are missing: "
            + ", ".join(sorted(missing_dynamic_cloaks)[:10])
        )
    unexpected_dynamic_cloaks = set(active_dynamic_cloak_plts) - required_dynamic_cloaks
    if unexpected_dynamic_cloaks:
        errors.append(
            f"{len(unexpected_dynamic_cloaks)} native cloak PLTs are not selected by cloakmodel.2da: "
            + ", ".join(sorted(unexpected_dynamic_cloaks)[:10])
        )
    if len(all_dynamic_cloak_plts) != len(active_dynamic_cloak_plts):
        errors.append("lower-priority runtime-selected cloak PLT duplicates remain")
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
            fragment_shader = tint_fragment_shader(model, mtr.splitlines())
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
                for line in tint_palette_parameter_lines(model in _NATIVE_ROBE_MATERIALS)
            )
            for line in required:
                if line not in mtr:
                    errors.append(f"{model}: MTR missing '{line}'")
            if model not in _NATIVE_ROBE_MATERIALS and re.search(r"\bparameter\s+float\s+usenativepalette\b", mtr):
                errors.append(f"{model}: native palette fallback is limited to native-selected robe consumers")
            if model in TEXTURE1_ALPHA_MATERIALS and "parameter float usetexture1alpha 1.0" not in mtr:
                errors.append(f"{model}: MTR lost required texture-alpha compatibility")
            if model in TEXTURE9_ALPHA_MATERIALS:
                alpha_texture = TEXTURE9_ALPHA_MATERIALS[model]
                if f"texture9 {alpha_texture}" not in mtr or "parameter float usetexture9alpha 1.0" not in mtr:
                    errors.append(f"{model}: MTR lost required dedicated alpha-map compatibility")
            if model in AUTHORED_HAIR_MAPS:
                for texture_slot, texture_resref in AUTHORED_HAIR_MAPS[model].items():
                    if f"texture{texture_slot} {texture_resref}" not in mtr:
                        errors.append(
                            f"{model}: MTR lost authored texture{texture_slot} binding '{texture_resref}'"
                        )

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
        profile = _PROFILE_ALIASES.get(alias)
        profile_material = profile[0] if profile is not None else source
        fragment_shader = tint_fragment_shader(profile_material, mtr.splitlines())
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
            for line in tint_palette_parameter_lines(alias in _NATIVE_ROBE_MATERIALS)
        )
        for line in required:
            if line not in mtr:
                errors.append(f"{alias}: scoped MTR missing '{line}'")
        if alias not in _NATIVE_ROBE_MATERIALS and re.search(r"\bparameter\s+float\s+usenativepalette\b", mtr):
            errors.append(f"{alias}: native palette fallback is limited to native-selected robe consumers")
        if profile_material in TEXTURE1_ALPHA_MATERIALS and "parameter float usetexture1alpha 1.0" not in mtr:
            errors.append(f"{alias}: scoped MTR lost required texture-alpha compatibility")
        if profile_material in TEXTURE9_ALPHA_MATERIALS:
            alpha_texture = TEXTURE9_ALPHA_MATERIALS[profile_material]
            if f"texture9 {alpha_texture}" not in mtr or "parameter float usetexture9alpha 1.0" not in mtr:
                errors.append(f"{alias}: scoped MTR lost required dedicated alpha-map compatibility")
        if profile is not None:
            expected = tint_material_text(material_path, alias, texture, int(entry["width"]), int(entry["height"]), source, profile,
                                          native_palette=alias in _NATIVE_ROBE_MATERIALS)
            if mtr != expected.lower():
                errors.append(f"{alias}: tint alias does not preserve its authored shared-material inputs")

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
            if columns[1:4] == ["****", "****", "****"]:
                continue
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
    else:
        errors.extend(f"plt_palette.tga: {error}" for error in native_metal_palette_errors(PALETTE_TEXTURE))
    if not PALETTE_TXI.exists() or "mipmap 0" not in PALETTE_TXI.read_text(encoding="utf-8").lower():
        errors.append("plt_palette.txi must disable mipmaps")
    for shader_path, expected_maps in (
        (
            TINT_SHADER,
            {"NORMAL_MAP": 0, "SPECULAR_MAP": 0, "ROUGHNESS_MAP": 0, "SELF_ILLUMINATION_MAP": 0},
        ),
        (
            TINT_MAPPED_SHADER,
            {"NORMAL_MAP": 1, "SPECULAR_MAP": 1, "ROUGHNESS_MAP": 1, "SELF_ILLUMINATION_MAP": 1},
        ),
        (
            TINT_HAIR_MAPPED_SHADER,
            {"NORMAL_MAP": 1, "SPECULAR_MAP": 1, "ROUGHNESS_MAP": 0, "SELF_ILLUMINATION_MAP": 0},
        ),
    ):
        if not shader_path.exists():
            errors.append(f"missing tint fragment shader {shader_path.name}")
            continue
        shader = shader_path.read_text(encoding="utf-8")
        errors.extend(f"{shader_path.name} {error}" for error in tint_shader_material_errors(shader))
        errors.extend(f"{shader_path.name} {error}" for error in native_robe_shader_errors(shader))
        for token in (
            "uniform sampler2D texUnit7",
            "uniform sampler2D texUnit9",
            "uniform sampler2D texUnit10",
            "uniform float rowSkin",
            "float paletteU = (g * 255.0 + 0.5) / 256.0",
            "float v = rowSkin",
            "vec3 vTint = paletteColor.rgb",
            "fEnvMapLevel = 1.0 - paletteColor.a",
            "float outputAlpha = materialFrontDiffuse.a",
            "SetupStandardShaderInputs();",
            "ApplyStandardShader();",
        ):
            if token not in shader:
                errors.append(f"{shader_path.name} missing '{token}'")
        for macro, expected_map_value in expected_maps.items():
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
    expected_settings = {path.with_suffix(".txi") for path in expected_outputs}
    actual_settings = {
        path.resolve()
        for directory in TINT_DIRECTORIES
        for path in directory.glob("*.txi")
    }
    if actual_settings - expected_settings:
        errors.append(f"{len(actual_settings - expected_settings)} orphaned packed TXI resources remain")

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
        f"{compiled_tint_model_count} compiled tint models, "
        f"{len(required_native_robe_controls())} native robe metadata controls, "
        f"no convertible 3D material PLTs, {len(active_dynamic_cloak_plts)} native dynamic cloak PLTs, "
        f"and {len(active_icon_plts)} required dynamic inventory icon PLTs remain."
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
        "--refresh-model-bindings",
        action="store_true",
        help="bind every native modular PLT fallback and refresh its catalog without changing packed pixels",
    )
    action.add_argument(
        "--refresh-stock-palettes",
        action="store_true",
        help="record stock PLT lookup names and KEY provenance for portable native-fallback audits",
    )
    action.add_argument(
        "--refresh-material",
        metavar="RESREF",
        help="regenerate one MTR declaration and its recorded scoped aliases",
    )
    action.add_argument(
        "--retain-dynamic-cloaks",
        action="store_true",
        help="remove invalid generated cloak bindings after restoring native cloak PLTs",
    )
    action.add_argument(
        "--refresh-packed-metadata",
        action="store_true",
        help="declare single-level DDS textures and disable implied mip uploads without changing pixels",
    )
    action.add_argument(
        "--refresh-packed-checksums",
        action="store_true",
        help="record decoded packed-map checksums without rewriting DDS resources",
    )
    action.add_argument("--deduplicate", action="store_true", help="share byte-identical packed maps")
    action.add_argument("--prune", action="store_true", help="remove masks not referenced by active models")
    action.add_argument(
        "--import-stock-models",
        action="store_true",
        help="import missing stock MDLs and bind their generated tint materials",
    )
    action.add_argument("--check", action="store_true", help="audit generated assets and PLT coverage")
    parser.add_argument(
        "--game-data",
        type=Path,
        help="NWN installation data directory containing nwn_base.key",
    )
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
    elif arguments.refresh_model_bindings:
        refresh_model_bindings()
    elif arguments.refresh_stock_palettes:
        if arguments.game_data is None:
            parser.error("--refresh-stock-palettes requires --game-data")
        STOCK_PALETTE_RESOURCES.write_text(
            json.dumps(stock_palette_inventory(arguments.game_data), indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
    elif arguments.refresh_material:
        refresh_material(arguments.refresh_material)
    elif arguments.retain_dynamic_cloaks:
        retain_dynamic_cloak_plts()
    elif arguments.refresh_packed_checksums:
        refresh_packed_checksums()
    elif arguments.refresh_packed_metadata:
        refresh_packed_metadata()
    elif arguments.deduplicate:
        deduplicate()
    elif arguments.prune:
        prune()
    elif arguments.import_stock_models:
        if arguments.game_data is None:
            parser.error("--import-stock-models requires --game-data")
        import_stock_models(arguments.game_data)
    else:
        if arguments.game_data is not None:
            expected = stock_palette_inventory(arguments.game_data)
            recorded = json.loads(STOCK_PALETTE_RESOURCES.read_text(encoding="utf-8"))
            if recorded != expected:
                raise RuntimeError("Stock PLT inventory differs from installed KEY files; refresh with --refresh-stock-palettes")
        audit()


if __name__ == "__main__":
    main()
