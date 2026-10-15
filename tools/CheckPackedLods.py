#!/usr/bin/env python3
"""Fail the HAK build when packed archives lack their checked-in LOD redirects.

nwn_erf skips files whose extension it does not know, so a packer invoked without
`--add-restypes lod:2078` drops every .lod while every source check still passes.
"""
import json
from pathlib import Path
import re
import struct

LOD_TYPE = 2078


def packed_resrefs(archive, restype):
    with Path(archive).open("rb") as stream:
        header = stream.read(32)
        if len(header) < 32 or header[4:8] not in (b"V1.0", b"V1.1"):
            raise ValueError(f"Not an ERF archive: {archive}")
        count, = struct.unpack_from("<I", header, 16)
        keys, = struct.unpack_from("<I", header, 24)
        name_size = 16 if header[4:8] == b"V1.0" else 32
        stream.seek(keys)
        table = stream.read(count * (name_size + 8))
    if len(table) != count * (name_size + 8):
        raise ValueError(f"Truncated ERF key list: {archive}")
    result = set()
    for index in range(count):
        offset = index * (name_size + 8)
        if struct.unpack_from("<H", table, offset + name_size + 4)[0] == restype:
            result.add(table[offset:offset + name_size].split(b"\0", 1)[0].decode("ascii").lower())
    return result


def missing_lods(root):
    root = Path(root).resolve()
    config_text = (root / "hakbuilder.json").read_text(encoding="utf-8-sig")
    configuration = json.loads(re.sub(r",\s*([}\]])", r"\1", config_text))
    output = (root / configuration["OutputPath"] / "hak").resolve()
    failures = []
    for hak in configuration["HakList"]:
        expected = {path.stem.lower() for path in (root / hak["Path"]).glob("*.lod")}
        if not expected:
            continue
        archive = output / f"{hak['Name']}.hak"
        if not archive.is_file():
            failures.append(f"{archive.name}: archive was not built")
            continue
        missing = expected - packed_resrefs(archive, LOD_TYPE)
        if missing:
            failures.append(f"{archive.name}: {len(missing)} LOD redirects missing, e.g. {sorted(missing)[0]}")
    return failures


def main():
    failures = missing_lods(Path(__file__).resolve().parents[1])
    if failures:
        raise SystemExit("Packed HAKs dropped model LOD redirects; the packer needs "
                         "--add-restypes lod:2078.\n" + "\n".join(failures))
    print("Every packed HAK contains its checked-in LOD redirects.")


if __name__ == "__main__":
    main()
