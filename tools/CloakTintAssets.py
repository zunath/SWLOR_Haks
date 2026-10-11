"""Author checked-in cloak tint atlases without duplicating cloak geometry.

Native cloak geometry and texture selection are independent. A stable material
receives RGB and an atlas tile index; native PLTs remain available for palettes
and inventory icons. Normal builds only validate these checked-in resources.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import tempfile

import numpy as np

import GenerateTintMapAssets as tint
import ModelLodAssets as lod
from GenerateRobeCloakModels import rename_model

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/CloakTintAssets.json"
TILE = 512
COLUMNS, ROWS = 4, 8
ATLAS_COUNT = 4
MATERIAL = "cloaktint"


def file_digest(path):
    """Git may check out TXI and ASCII MDL text with CRLF; other resources stay byte-exact."""
    data = path.read_bytes()
    if path.suffix.lower() == ".txi" or (path.suffix.lower() == ".mdl" and data[:4] != bytes(4)):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def replace_once(source, old, new):
    """A reworded base shader must fail authoring instead of keeping a whole-atlas sample."""
    if source.count(old) != 1:
        raise ValueError(f"fs_plt_tinter.shd no longer matches the cloak tint rewrite: {old}")
    return source.replace(old, new)


def geometry_content(path, data):
    """Exclude only mesh material references from the geometry preservation check."""
    if data[:4] == bytes(4):
        result = bytearray(data)
        for _, _, offset, _ in tint.read_binary_model_material_fields(path, data):
            result[offset:offset + 64] = bytes(64)
        return bytes(result)
    return re.sub(rb"(?im)^\s*materialname\s+[^\r\n]+[\r\n]*", b"", data).replace(b"\r\n", b"\n")


def texture_ids():
    return sorted({int(row[3])
                   for line in (ROOT / "sw_2da/cloakmodel.2da").read_text().splitlines()[3:]
                   if len(row := shlex.split(line)) > 3 and row[2].isdigit()
                   and int(row[2]) > 0 and row[3].isdigit() and int(row[3]) > 0})


def shader_text():
    source = (ROOT / "sw_shader/fs_plt_tinter.shd").read_text()
    source = replace_once(source, "uniform sampler2D texUnit9;", "")
    declarations = """uniform sampler2D texUnit6;
uniform sampler2D texUnit7;
uniform sampler2D texUnit8;
uniform sampler2D texUnit9;
uniform float cloakTexture;

vec4 SampleCloakTint(vec2 uv)
{
    float tile = mod(floor(cloakTexture + 0.5), 32.0);
    vec2 origin = vec2(mod(tile, 4.0), floor(tile / 4.0));
    // Clamp within the selected tile: linear filtering must not blend another cloak.
    vec2 localUV = clamp(fract(uv), vec2(0.5 / 512.0), vec2(511.5 / 512.0));
    vec2 atlasUV = (origin + localUV) / vec2(4.0, 8.0);
    if (cloakTexture < 31.5) return texture2D(texUnit6, atlasUV, -10.0);
    if (cloakTexture < 63.5) return texture2D(texUnit7, atlasUV, -10.0);
    if (cloakTexture < 95.5) return texture2D(texUnit8, atlasUV, -10.0);
    return texture2D(texUnit9, atlasUV, -10.0);
}
"""
    source = replace_once(source, "uniform sampler2D texUnit7;", declarations)
    for uv in ("uv", "wrappedUV", "nearestUV"):
        source = replace_once(source, f"texture2D(texUnit7, {uv}, -10.0)", f"SampleCloakTint({uv})")
    if re.search(r"texture2D\s*\(\s*texUnit7\s*,", source.split("vec4 SampleCloakTint", 1)[1].split("}", 1)[1]):
        raise ValueError("fs_cloaktint.shd would still sample a whole cloak atlas")
    return "// Authored by CloakTintAssets.py from fs_plt_tinter.shd.\n" + source


def material_text():
    source = tint.tint_material_text(Path("cloaktint.mtr"), MATERIAL,
                                    "cloak_tints1", TILE, TILE, profile=(MATERIAL, []),
                                    native_palette=True)
    source = source.replace("fs_plt_tinter", "fs_cloaktint")
    source = source.replace("texture7 cloak_tints1", "\n".join(
        f"texture{6 + index} cloak_tints{index}" for index in range(ATLAS_COUNT)))
    return source + "parameter float cloakTexture 0.0\n"


def table_text(textures):
    lines = ["2DA V2.0", "", "   MODEL MATERIAL LAYERS INDEX"]
    for number in range(max(map(int, textures)) + 1):
        entry = textures.get(str(number))
        if entry:
            layers = ",".join(map(str, entry["layers"]))
            lines.append(f"{number} cloak_{number:03} {MATERIAL} {layers} {entry['index']}")
        else:
            lines.append(f"{number} **** **** **** ****")
    return "\n".join(lines) + "\n"


def bind_models():
    """Change material references only, retaining every model and LOD identity."""
    previous = {}
    for path in sorted((ROOT / "sw_pt_cloak").glob("*.mdl")):
        data = path.read_bytes()
        if data[:4] == bytes(4):
            fields = tint.read_binary_model_material_fields(path, data)
            bindings = {f"@field:{offset}": MATERIAL for _, texture, offset, _ in fields
                        if texture and texture.lower() != "null"}
        else:
            bindings = {name.decode().lower(): MATERIAL for name in
                        re.findall(rb"(?im)^\s*(?:bitmap|texture0)\s+(\S+)", data)
                        if name.lower() != b"null"}
        if tint.synchronize_model_material_bindings(path, bindings):
            if geometry_content(path, data) != geometry_content(path, path.read_bytes()):
                raise ValueError(f"Cloak geometry changed while binding a material: {path}")
            previous[path.relative_to(ROOT).as_posix()] = data

    manifest_path = ROOT / lod.MANIFEST
    manifest = json.loads(manifest_path.read_text())
    for record in manifest["aliases"].values():
        if record["canonical"] not in previous:
            continue
        before = lod.content(previous[record["canonical"]])
        if lod.digest(lod.restore_identity(before, record)) != record["source_sha256"]:
            raise ValueError("Unvalidated model redirect before material authoring")
        after = lod.content((ROOT / record["canonical"]).read_bytes())
        restored = lod.restore_identity(after, record)
        record.update(canonical_sha256=lod.digest(after), source_sha256=lod.digest(restored),
                      source_bytes=len(restored))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    # The robe audit checks the same canonical cloak materials across every phenotype.
    path = ROOT / "tools/RobeCloakModels.json"
    manifest = json.loads(path.read_text())
    for name, source in manifest["models"].items():
        data = lod.ModelPath(ROOT / f"sw_pt_cloak/{name}.mdl").read_bytes()
        manifest["files"][name] = hashlib.sha256(data).hexdigest()
        manifest["sources"][source] = hashlib.sha256(rename_model(data, name, source)).hexdigest()
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return len(previous)


def generate(game_data):
    numbers = texture_ids()
    if len(numbers) > ATLAS_COUNT * COLUMNS * ROWS:
        raise ValueError("Cloak tint atlases have no free tiles")
    stock = tint.read_stock_key_resources(game_data, 6)
    textures = {}
    blocks = [np.zeros((ROWS * TILE // 4, COLUMNS * TILE // 4, 16), dtype=np.uint8)
              for _ in range(ATLAS_COUNT)]
    with tempfile.TemporaryDirectory(prefix="swlor-cloak-tints-") as directory:
        stage = Path(directory)
        for index, number in enumerate(numbers):
            name = f"cloak_{number:03}"
            source = ROOT / f"sw_pt_cloak/{name}.plt"
            authored = source.is_file()
            if not authored:
                source = stage / f"{name}.plt"
                source.write_bytes(tint.extract_stock_bif_resource(*stock[name], expected_type=6))
            width, height, shade, layer, sha = tint.read_plt(source)
            if width != height or TILE % width:
                raise ValueError(f"Unsupported cloak PLT dimensions: {name}")
            scale = TILE // width
            shade = np.repeat(np.repeat(shade, scale, axis=0), scale, axis=1)
            layer = np.repeat(np.repeat(layer, scale, axis=0), scale, axis=1)
            packed = stage / f"{name}.dds"
            tint.write_packed_dds(packed, TILE, TILE, shade, layer)
            tile = np.frombuffer(packed.read_bytes(), dtype=np.uint8, offset=128).reshape(TILE // 4, TILE // 4, 16)
            slot = index % (COLUMNS * ROWS)
            x, y = slot % COLUMNS * TILE // 4, slot // COLUMNS * TILE // 4
            blocks[index // (COLUMNS * ROWS)][y:y + TILE // 4, x:x + TILE // 4] = tile
            textures[str(number)] = {"index": index, "sha256": sha, "width": width,
                                     "height": height, "layers": sorted(map(int, np.unique(layer))),
                                     "source": f"sw_pt_cloak/{name}.plt" if authored else f"stock:{name}.plt"}
    files = {}
    for index, atlas in enumerate(blocks):
        path = ROOT / f"sw_pt_cloak/cloak_tints{index}.dds"
        payload = atlas.tobytes()
        path.write_bytes(tint.dds_header(COLUMNS * TILE, ROWS * TILE, len(payload)) + payload)
        tint.write_packed_texture_settings(path)
        for output in (path, path.with_suffix(".txi")):
            files[output.relative_to(ROOT).as_posix()] = file_digest(output)
    (ROOT / "sw_pt_cloak/cloaktint.mtr").write_text(material_text())
    (ROOT / "sw_shader/fs_cloaktint.shd").write_text(shader_text())
    (ROOT / "sw_2da/cloaktint.2da").write_text(table_text(textures))
    changed = bind_models()
    models = {path.relative_to(ROOT).as_posix(): file_digest(path)
              for path in sorted((ROOT / "sw_pt_cloak").glob("*.mdl"))}
    MANIFEST.write_text(json.dumps({"textures": textures, "files": files, "models": models}, indent=2) + "\n")
    print(f"Authored {len(numbers)} cloak tint tiles; bound {changed} shared models; added no MDLs.")


def check():
    if not MANIFEST.exists():
        return ["Missing cloak tint manifest"]
    manifest = json.loads(MANIFEST.read_text())
    errors = []
    if sorted(map(int, manifest["textures"])) != texture_ids():
        errors.append("Cloak tint coverage disagrees with cloakmodel.2da")
    for relative, sha in manifest["files"].items():
        path = ROOT / relative
        if not path.is_file() or file_digest(path) != sha:
            errors.append(f"Missing or changed cloak atlas: {relative}")
    for relative, sha in manifest["models"].items():
        path = ROOT / relative
        if not path.is_file() or file_digest(path) != sha:
            errors.append(f"Missing or changed cloak material binding: {relative}")
    for entry in manifest["textures"].values():
        if not entry["source"].startswith("stock:"):
            path = ROOT / entry["source"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                errors.append(f"Cloak palette source changed: {entry['source']}")
    expected = {"sw_pt_cloak/cloaktint.mtr": material_text(),
                "sw_shader/fs_cloaktint.shd": shader_text(),
                "sw_2da/cloaktint.2da": table_text(manifest["textures"])}
    for relative, source in expected.items():
        path = ROOT / relative
        if not path.is_file() or path.read_text() != source:
            errors.append(f"Cloak tint asset is stale: {relative}")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--game-data", type=Path)
    args = parser.parse_args()
    if args.check:
        errors = check()
        if errors:
            raise SystemExit("\n".join(errors[:20]))
        print("Checked-in cloak RGB atlases are current.")
    elif args.game_data:
        generate(args.game_data)
    else:
        parser.error("--game-data is required for explicit authoring")
