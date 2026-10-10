"""Publish native cloak geometry for each generated robe phenotype.

Cloaks use a phenotype-specific model lookup. Body-part fallback alone does
not provide the cloak attachment when the wearer uses a robe body root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import tempfile

import CompileModels as mdl
import GenerateTintMapAssets as tint
import RobePoseAudit as poses
from RobeAnimationBanks import LIMIT_BYTES

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/RobeCloakModels.json"
SOURCES = ROOT / "tools/RobeCloakSources"


def required_models(mappings: str, cloaks: str) -> dict[str, str]:
    geometry = {int(row[2]) for line in cloaks.splitlines()[3:]
                if (row := shlex.split(line)) and len(row) > 2 and row[2].isdigit() and int(row[2]) > 0}
    result = {}
    for line in mappings.splitlines()[3:]:
        row = line.split()
        if not row:
            continue
        match = re.fullmatch(r"(p[fm][a-z])\d+_robe\d{3}", row[1])
        if not match or len(row) != 4 or not row[2].isdigit() or not row[3].isdigit():
            raise ValueError(f"Invalid robe mapping: {line}")
        for model in geometry:
            name = f"{match[1]}{int(row[2])}_cloak_{model:03d}"
            if len(name) > 16:
                raise ValueError(f"Cloak resource name exceeds 16 characters: {name}")
            result[name] = f"{match[1]}{int(row[3])}_cloak_{model:03d}"
    if not result:
        raise ValueError("No robe cloak models were selected")
    return result


def rename_model(data: bytes, original: str, name: str) -> bytes:
    """Rename only fixed-width model/root names; preserve all geometry and binds."""
    if not mdl.binary(data) or len(data) < 244 or len(name) > 16:
        raise ValueError("Expected a compiled cloak and a valid resource name")
    result = bytearray(data)
    root = struct.unpack_from("<I", data, 84)[0] + 12
    for offset, length in ((20, 64), (root + 32, 32)):
        if offset + length > len(data) or data[offset:offset + length].split(b"\0", 1)[0].decode("ascii").lower() != original:
            raise ValueError(f"Unexpected cloak model/root name: {original}")
        result[offset:offset + length] = name.encode("ascii").ljust(length, b"\0")
    return bytes(result)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def requirements():
    return required_models((ROOT / "sw_2da/roberender.2da").read_text(),
                           (ROOT / "sw_2da/cloakmodel.2da").read_text())


def model_map(manifest):
    return {f"{body[:3]}{phenotype}_cloak_{geometry:03d}": f"{body}_cloak_{geometry:03d}"
            for body, phenotypes in manifest["phenotypes"].items()
            for phenotype in phenotypes for geometry in manifest["geometry"]
            if f"{body}_cloak_{geometry:03d}" in manifest["sources"]}


def source_data(manifest):
    return {name: (SOURCES / f"{name}.mdl").read_bytes() for name in manifest["sources"]}


def check() -> list[str]:
    if not MANIFEST.is_file():
        return ["Missing robe cloak manifest"]
    manifest = json.loads(MANIFEST.read_text())
    errors = []
    expected = requirements()
    available = {name: source for name, source in expected.items()
                 if source not in manifest.get("unavailable_sources", [])}
    if model_map(manifest) != available:
        errors.append("Robe cloak models do not cover the current robe/cloak tables")
    if set(manifest["sources"]) != set(available.values()):
        errors.append("Canonical cloak sources do not match the current tables")
    for source, checksum in manifest["sources"].items():
        path = SOURCES / f"{source}.mdl"
        if not path.is_file() or digest(path.read_bytes()) != checksum:
            errors.append(f"Missing or changed canonical cloak model: {source}")
            continue
        try:
            rename_model(path.read_bytes(), source, source)
            if path.stat().st_size >= LIMIT_BYTES:
                errors.append(f"Cloak exceeds the NWSync resource limit: {source}")
        except ValueError as error:
            errors.append(str(error))
    stale = [name for name in expected if (ROOT / "sw_pt_cloak" / f"{name}.mdl").exists()]
    if stale:
        errors.append(f"{len(stale)} expanded cloak models remain in the HAK source folder")
    return errors


def write_sources(expected, sources, unavailable):
    phenotypes = {}
    geometry = set()
    for name, source in expected.items():
        body, model = source.split("_cloak_")
        phenotypes.setdefault(body, set()).add(int(name[3:].split("_", 1)[0]))
        geometry.add(int(model))
    SOURCES.mkdir(exist_ok=True)
    for name, data in sources.items():
        (SOURCES / f"{name}.mdl").write_bytes(data)
    manifest = {"phenotypes": {body: sorted(values) for body, values in sorted(phenotypes.items())},
                "geometry": sorted(geometry), "unavailable_sources": unavailable,
                "sources": {name: digest(data) for name, data in sorted(sources.items())}}
    if model_map(manifest) != expected:
        raise ValueError("Compact cloak matrix cannot represent the required models")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


def build_info():
    errors = check()
    if errors:
        raise ValueError("\n".join(errors[:20]))
    manifest = json.loads(MANIFEST.read_text())
    sources = source_data(manifest)
    checksum = digest(Path(__file__).read_bytes() + MANIFEST.read_bytes())
    return {"Checksum": checksum,
            "ResourceSizes": [len(sources[source]) for source in model_map(manifest).values()]}


def pack(archive):
    """Append generated resources directly to an ERF; never create loose copies.

    Each generated model is byte-identical to the previous expanded resource.
    Existing payloads and localized strings are copied unchanged; the archive's
    key/resource tables are rebuilt with absolute offsets for the new layout.
    """
    build_info()  # Validate every source and table before touching the archive.
    manifest = json.loads(MANIFEST.read_text())
    sources = source_data(manifest)
    models = model_map(manifest)
    with archive.open("rb") as stream:
        header = stream.read(160)
        if len(header) != 160 or header[:8] != b"HAK V1.0":
            raise ValueError("Expected a native HAK V1.0 archive")
        count, _, keys_offset, resources_offset = struct.unpack_from("<4I", header, 16)
        localized_size, = struct.unpack_from("<I", header, 12)
        localized_offset, = struct.unpack_from("<I", header, 20)
        stream.seek(localized_offset)
        localized = stream.read(localized_size)
        if len(localized) != localized_size:
            raise ValueError("Invalid source HAK localized string bounds")
        stream.seek(keys_offset)
        keys = [struct.unpack("<16sIHH", stream.read(24)) for _ in range(count)]
        stream.seek(resources_offset)
        resources = [struct.unpack("<II", stream.read(8)) for _ in range(count)]
        size = archive.stat().st_size
        if any(offset + length > size for offset, length in resources):
            raise ValueError("Invalid source HAK resource bounds")
        existing = {(name.rstrip(b"\0").decode("ascii").lower(), kind) for name, _, kind, _ in keys}
        if any((name, 2002) in existing for name in models):
            raise ValueError("Generated cloak collides with an existing HAK resource")
        total = 160 + len(localized) + sum(length for _, length in resources) + sum(
            len(sources[source]) for source in models.values()) + (count + len(models))*32
        if total >= 2*1024**3:
            raise ValueError("Generated cloak HAK reaches the 2 GiB client compatibility limit")
        with tempfile.NamedTemporaryFile(dir=archive.parent, prefix=archive.name, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(header)
                output.write(localized)
                rebuilt = []
                for offset, length in resources:
                    stream.seek(offset)
                    rebuilt.append((output.tell(), length))
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(remaining, 1024*1024))
                        if not chunk:
                            raise ValueError("Truncated source HAK resource")
                        output.write(chunk)
                        remaining -= len(chunk)
                resources = rebuilt
                for name, source in sorted(models.items()):
                    data = rename_model(sources[source], source, name)
                    keys.append((name.encode().ljust(16, b"\0"), len(resources), 2002, 0))
                    resources.append((output.tell(), len(data)))
                    output.write(data)
                keys_offset = output.tell()
                for key in keys:
                    output.write(struct.pack("<16sIHH", *key))
                resources_offset = output.tell()
                for resource in resources:
                    output.write(struct.pack("<II", *resource))
                output.seek(16)
                output.write(struct.pack("<I", len(keys)))
                output.write(struct.pack("<I", 160))
                output.seek(24)
                output.write(struct.pack("<II", keys_offset, resources_offset))
            except BaseException:
                output.close()
                temporary.unlink(missing_ok=True)
                raise
    try:
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)


def generate(game_data: Path):
    expected = requirements()
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(game_data)
    unavailable = sorted(set(expected.values()) - active.keys() - stock.keys())
    # Preserve canonical support: do not invent a different species' geometry
    # for cloak models that already lack a model on the normal phenotype.
    expected = {name: source for name, source in expected.items() if source not in unavailable}
    # Reject every collision before writing any output.
    for name in expected:
        if name in active:
            raise ValueError(f"Expanded cloak model must be removed before generation: {name}")
    with tempfile.TemporaryDirectory(prefix="swlor-robe-cloaks-") as directory:
        stage = Path(directory)
        compiler, _ = mdl.prepare_compiler(stage)
        loaded = {}

        def load(name):
            if name in loaded:
                return loaded[name]
            if name in active:
                data = active[name].read_bytes()
            elif name in stock:
                data = tint.extract_stock_bif_resource(*stock[name])
            else:
                raise ValueError(f"Missing canonical cloak dependency: {name}")
            loaded[name] = data
            parent = mdl.supermodel(data)
            if parent:
                load(parent)
            path = stage / f"{name}.mdl"
            path.write_bytes(data)
            if not mdl.binary(data):
                source = stage / "source"
                source.mkdir(exist_ok=True)
                source_path = source / path.name
                source_path.write_bytes(mdl.protect_vertex_identity(data))
                mdl.run_compiler(compiler, stage, ["-cne", str(source_path), str(stage) + "/"], "compile.log")
                data = poses.repair_compiler_skin_bindings(mdl.restore_vertex_attributes(data, path.read_bytes()))
                path.write_bytes(data)
                exported = stage / "exported"
                exported.mkdir(exist_ok=True)
                mdl.run_compiler(compiler, stage, ["-de", str(path), str(exported) + "/"], "decompile.log")
                mdl.validate_round_trip(loaded[name], data,
                                        (exported / path.name).read_text(encoding="latin1"))
            loaded[name] = data
            return data

        sources = {name: load(name) for name in sorted(set(expected.values()))}
        write_sources(expected, sources, unavailable)
    print(f"Stored {len(sources)} canonical cloaks for {len(expected)} generated resources.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--game-data", type=Path)
    parser.add_argument("--build-info", action="store_true")
    parser.add_argument("--pack", type=Path)
    args = parser.parse_args()
    if args.build_info:
        print(json.dumps(build_info()))
    elif args.pack:
        pack(args.pack)
    elif args.check:
        errors = check()
        if errors:
            raise SystemExit("\n".join(errors[:20]))
        print("Generated robe cloak models are current.")
    else:
        if args.game_data is None:
            parser.error("--game-data is required for generation")
        generate(args.game_data)


if __name__ == "__main__":
    main()
