#!/usr/bin/env python3
"""Reject oversized individual HAK resources before packaging for NWSync."""
import json
from pathlib import Path
import re

from RobeAnimationBanks import LIMIT_BYTES


def oversized_resources(root):
    root = Path(root).resolve()
    # Match GenerateTintMapAssets.load_hak_config without importing its image dependencies.
    config_text = (root / "hakbuilder.json").read_text(encoding="utf-8-sig")
    configuration = json.loads(re.sub(r",\s*([}\]])", r"\1", config_text))
    result = []
    for hak in configuration["HakList"]:
        directory = (root / hak["Path"]).resolve()
        if not directory.is_relative_to(root) or not directory.is_dir():
            raise ValueError(f"Missing or redirected HAK directory: {directory}")
        for path in directory.iterdir():
            if path.is_file() and path.stat().st_size >= LIMIT_BYTES:
                result.append((path.relative_to(root).as_posix(), path.stat().st_size))
    return sorted(result)


def main():
    oversized = oversized_resources(Path(__file__).resolve().parents[1])
    if oversized:
        for path, size in oversized:
            print(f"{path}: {size:,} bytes (NWSync limit: {LIMIT_BYTES:,})")
        raise SystemExit(f"{len(oversized)} resources exceed the NWSync per-file size limit.")
    print(f"All configured HAK resources are below {LIMIT_BYTES:,} bytes.")


if __name__ == "__main__":
    main()
