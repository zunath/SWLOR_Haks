"""Author checked-in native cloak geometry for each generated robe phenotype.

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
from ModelLodAssets import ModelPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/RobeCloakModels.json"


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


def check() -> list[str]:
    if not MANIFEST.is_file():
        return ["Missing robe cloak manifest"]
    manifest = json.loads(MANIFEST.read_text())
    errors = []
    expected = requirements()
    available = {name: source for name, source in expected.items()
                 if source not in manifest.get("unavailable_sources", [])}
    if manifest["models"] != available:
        errors.append("Robe cloak models do not cover the current robe/cloak tables")
    for name in available:
        path = ModelPath(ROOT / "sw_pt_cloak" / f"{name}.mdl")
        if not path.is_file() or digest(path.read_bytes()) != manifest["files"].get(name):
            errors.append(f"Missing or changed robe cloak model: {name}")
            continue
        source = available[name]
        canonical = rename_model(path.read_bytes(), name, source)
        if digest(canonical) != manifest["sources"].get(source):
            errors.append(f"Cloak geometry or bindings differ from the validated source: {name}")
    return errors


def generate(game_data: Path):
    expected = requirements()
    active = tint.find_active_models()
    stock = tint.read_stock_key_models(game_data)
    unavailable = sorted(set(expected.values()) - active.keys() - stock.keys())
    # Preserve canonical support: do not invent a different species' geometry
    # for cloak models that already lack a model on the normal phenotype.
    expected = {name: source for name, source in expected.items() if source not in unavailable}
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.is_file() else {"files": {}}
    # Reject every collision before writing any output.
    for name in expected:
        if name in active and digest(active[name].read_bytes()) != previous["files"].get(name):
            raise ValueError(f"Refusing to replace an unowned cloak model: {name}")
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
        # Prepare and validate the entire set before installing any resource.
        outputs = {name: rename_model(sources[source], source, name) for name, source in expected.items()}
        for name, data in outputs.items():
            path = ROOT / "sw_pt_cloak" / f"{name}.mdl"
            if not path.is_file() or path.read_bytes() != data:
                path.write_bytes(data)
        manifest = {"models": dict(sorted(expected.items())),
                    "unavailable_sources": unavailable,
                    "sources": {name: digest(rename_model(data, name, name)) for name, data in sources.items()},
                    "files": {name: digest(data) for name, data in sorted(outputs.items())}}
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Published {len(expected)} cloak models for generated robe phenotypes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--game-data", type=Path)
    args = parser.parse_args()
    if args.check:
        errors = check()
        if errors:
            raise SystemExit("\n".join(errors[:20]))
        print("Checked-in robe cloak models are current.")
    else:
        if args.game_data is None:
            parser.error("--game-data is required for generation")
        generate(args.game_data)


if __name__ == "__main__":
    main()
