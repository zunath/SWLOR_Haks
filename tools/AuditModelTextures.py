#!/usr/bin/env python3
"""Audit active HAK model texture dependencies against HAKs and installed NWN.

Includes binary/ASCII meshes, emitters, MTR texture slots and TXI maps. Reports
unresolved references rather than treating an existing MTR as a valid texture.
Does not assert visual correctness or inspect client overrides/deployed HAKs.
"""
import argparse
from collections import Counter
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
EMPTY = {"", "null", "****"}
TEXTURES = ("dds", "tga", "plt", "ktx")


@dataclass
class ArchiveText:
    name: str
    data: bytes

    def read_text(self, **kwargs):
        return self.data.decode("latin1")


def model_surfaces(data):
    """Yield (node, bitmap, material, lightmap, emitter) for visible geometry."""
    if data[:4] != bytes(4):
        text = data.decode("latin1")
        for match in re.finditer(r"(?ims)^\s*node\s+(\S+)\s+(\S+)[^\r\n]*\r?\n(.*?)^\s*endnode\b", text):
            kind, name, body = match.groups()
            fields = dict(re.findall(r"(?im)^\s*(bitmap|texture0|materialname|lightmap|texture|chunkname|render)\s+(\S+)", body.lower()))
            if fields.get("render") == "0" or kind.lower() in {"dummy", "aabb", "light", "reference"}:
                continue
            emitter = fields.get("texture", "") if fields.get("chunkname", "") in EMPTY else ""
            yield name, fields.get("bitmap", fields.get("texture0", "")), fields.get("materialname", ""), fields.get("lightmap", ""), emitter
        return
    if len(data) < 244:
        raise ValueError("truncated model header")
    _, size, raw = struct.unpack_from("<III", data)
    end = 12 + size
    if end + raw > len(data):
        raise ValueError("model/raw sections extend beyond file")
    def uint(offset):
        if offset < 12 or offset + 4 > end:
            raise ValueError("invalid model pointer")
        return struct.unpack_from("<I", data, offset)[0]
    def string(offset, count=64):
        if offset < 12 or offset + count > end:
            raise ValueError("truncated resource field")
        return data[offset:offset+count].split(b"\0", 1)[0].decode("latin1").lower()
    pending, visited = [uint(84)], set()
    while pending:
        pointer = pending.pop()
        if pointer in visited:
            raise ValueError("repeated node pointer")
        visited.add(pointer)
        node = 12 + pointer
        flags = uint(node + 108)
        count = uint(node + 76)
        if count > 100000:
            raise ValueError("invalid child count")
        children = 12 + uint(node + 72)
        pending.extend(uint(children + 4*i) for i in range(count))
        name = string(node+32, 32)
        extra = node + 112 + (92 if flags & 2 else 0)
        if flags & 4:
            # Chunk emitters render another model instead of a particle bitmap.
            if string(extra + 184, 16) in EMPTY:
                yield name, "", "", "", string(extra + 120)
            extra += 216
        if flags & 16:
            extra += 68
        if flags & 32 and not flags & 512 and uint(extra + 108) and uint(extra + 12):
            yield name, string(extra+120), string(extra+312), string(extra+184), ""


def index_resources(root, game_data):
    resources = {}
    types = {3: "tga", 6: "plt", 2002: "mdl", 2022: "txi", 2033: "dds", 2072: "mtr", 2073: "ktx"}
    keys = sorted(game_data.glob("*.key"))
    if not keys:
        raise ValueError("No installed KEY files found")
    for path in keys:
        data = path.read_bytes()
        if data[:8] != b"KEY V1  ":
            raise ValueError(f"Invalid KEY: {path}")
        count, offset = struct.unpack_from("<I", data, 12)[0], struct.unpack_from("<I", data, 20)[0]
        bif_count, bif_offset = struct.unpack_from("<I", data, 8)[0], struct.unpack_from("<I", data, 16)[0]
        bifs = []
        for i in range(bif_count):
            _, name_offset, size, _ = struct.unpack_from("<IIHH", data, bif_offset+12*i)
            filename = data[name_offset:name_offset+size].split(b"\0", 1)[0].decode("ascii").replace("\\", "/")
            candidate = game_data.parent / filename
            if not candidate.is_file():
                candidate = game_data / Path(filename).name
            if not candidate.is_file():
                raise ValueError(f"Missing installed BIF: {candidate}")
            bifs.append(candidate)
        for i in range(count):
            name, kind, rid = struct.unpack_from("<16sHI", data, offset+22*i)
            if kind in types:
                name = name.split(b"\0",1)[0].decode("ascii").lower()
                value = None
                if kind in {2022, 2072}:
                    with bifs[rid >> 20].open("rb") as stream:
                        header = stream.read(20)
                        if header[:8] != b"BIFFV1  ":
                            raise ValueError("Invalid installed BIF")
                        table = struct.unpack_from("<I", header, 16)[0]
                        stream.seek(table + 16*(rid & 0xfffff))
                        _, start, size, actual_type = struct.unpack("<IIII", stream.read(16))
                        if actual_type != kind:
                            raise ValueError("KEY/BIF resource type mismatch")
                        stream.seek(start)
                        value = ArchiveText(f"{name}.{types[kind]}", stream.read(size))
                resources[(name, types[kind])] = value
    for path in sorted((game_data / "txpk").glob("*.erf")):
        with path.open("rb") as stream:
            header = stream.read(160)
            if header[:8] != b"ERF V1.0":
                raise ValueError(f"Invalid texture pack: {path}")
            count, offset = struct.unpack_from("<I", header, 16)[0], struct.unpack_from("<I", header, 24)[0]
            stream.seek(offset)
            for _ in range(count):
                name, rid, kind, _ = struct.unpack("<16sIHH", stream.read(24))
                if kind in types:
                    name = name.split(b"\0",1)[0].decode("ascii").lower()
                    value = None
                    if kind in {2022, 2072}:
                        position = stream.tell()
                        stream.seek(struct.unpack_from("<I", header, 28)[0]+rid*8)
                        start, size = struct.unpack("<II", stream.read(8))
                        stream.seek(start)
                        value = ArchiveText(f"{name}.{types[kind]}", stream.read(size))
                        stream.seek(position)
                    resources[(name, types[kind])] = value
    # Engine stock override files are part of the installation, not user overrides.
    for directory in (game_data.parent / "ovr",):
        for path in directory.rglob("*"):
            if path.is_file():
                resources[(path.stem.lower(), path.suffix[1:].lower())] = path
    config = json.loads((root / "hakbuilder.json").read_text())
    haks = config["HakList"]
    module = root.parent / "Module/ifo/module.ifo.json"
    if module.is_file():
        names = [row["Mod_Hak"]["value"] for row in json.loads(module.read_text(encoding="latin-1"))["Mod_HakList"]["value"]]
        by_name = {hak["Name"]: hak for hak in haks}
        haks = [by_name[name] for name in names]
    # Earlier module HAK entries win. Do not let unused source folders satisfy
    # a dependency, or let a lower-priority duplicate mask a broken winner.
    for hak in reversed(haks):
        directory = (root / hak["Path"]).resolve()
        if not directory.is_dir():
            raise ValueError(f"Missing configured HAK directory: {directory}")
        for path in directory.iterdir():
            if path.is_file():
                resources[(path.stem.lower(), path.suffix[1:].lower())] = path
    return resources, keys


def audit(root, game_data):
    resources, keys = index_resources(root, game_data)
    problems, checked = set(), Counter()
    def missing(model, node, kind, ref, via):
        problems.add((model, node, kind, ref, via))
    def texture(model, node, kind, ref, via, seen=None):
        ref = ref.lower()
        if ref in EMPTY:
            return
        checked["textureReferences"] += 1
        # CResRef is a 16-byte resource identifier even though MDL bitmap
        # fields hold 64 bytes. Legacy exporters often leave longer labels.
        if len(ref) > 16:
            checked["overlongReferences"] += 1
            ref = ref[:16]
        if kind in {"envmaptexture", "bumpyshinytexture"} and all(
            any((f"{ref}{face}", ext) in resources for ext in TEXTURES)
            for face in range(6)
        ):
            checked["cubeMapReferences"] += 1
            return
        if not any((ref, ext) in resources for ext in TEXTURES):
            missing(model, node, kind, ref, via)
            return
        txi = resources.get((ref, "txi"))
        seen = set() if seen is None else seen
        if txi and ref not in seen:
            seen.add(ref)
            for field, dependency in re.findall(r"(?im)^\s*(bumpmaptexture|envmaptexture|bumpyshinytexture)\s+(\S+)", txi.read_text(errors="replace")):
                texture(model, node, field, dependency, txi.name, seen)
    models = {name: path for (name, ext), path in resources.items() if ext == "mdl" and path is not None and path.is_relative_to(root)}
    for model, path in sorted(models.items()):
        checked["models"] += 1
        data = path.read_bytes()
        checked["binaryModels" if data[:4] == bytes(4) else "asciiModels"] += 1
        try:
            surfaces = list(model_surfaces(data))
        except (ValueError, struct.error) as exc:
            missing(model, "", "parse-error", str(exc), path.name)
            continue
        for node, bitmap, material, lightmap, emitter in surfaces:
            checked["surfaces"] += 1
            texture(model, node, "emitter", emitter, path.name)
            texture(model, node, "lightmap", lightmap, path.name)
            mat = resources.get((material, "mtr")) if material not in EMPTY else resources.get((bitmap, "mtr"))
            # A material name is also an address for runtime setters; it need
            # not name an MTR when the bitmap/native palette supplies pixels.
            if mat:
                slots = dict(re.findall(r"(?im)^\s*(texture\d+)\s+(\S+)", mat.read_text(errors="replace")))
                for field, ref in slots.items():
                    texture(model, node, field, ref, mat.name)
                if slots.get("texture0", "").lower() not in EMPTY:
                    continue
            if bitmap not in EMPTY:
                # Dynamic cloaks are deliberately still native PLTs. Their
                # numbered texture is chosen by cloakmodel.2da at runtime.
                if "_cloak_" in model:
                    cloak = "cloak_" + model.rsplit("_", 1)[1]
                    if (cloak, "plt") in resources:
                        continue
                texture(model, node, "bitmap", bitmap, path.name)
    return {"counts": dict(checked), "stockKeys": [p.name for p in keys], "affectedModels": len({p[0] for p in problems}), "missingReferences": len(problems)}, sorted(problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "output/model-texture-audit")
    args = parser.parse_args()
    summary, problems = audit(ROOT, args.game_data)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    with (args.output / "missing.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("Model", "Node", "Kind", "MissingResource", "ReferencedBy"))
        writer.writerows(problems)
    print(json.dumps(summary, indent=2))
    raise SystemExit(bool(problems))


if __name__ == "__main__":
    main()
