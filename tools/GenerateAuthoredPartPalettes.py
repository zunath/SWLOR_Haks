#!/usr/bin/env python3
"""Author palette (PLT) sources for parts that only had fixed rasters.

Shoulder 258 and the forearm/hand 215 prosthetic drew fixed grey artwork, so
armor dyes and exact RGB never reached them. Their artwork is monochrome, so a
single Metal1 layer whose shade is the raster's luminance keeps the look while
the native palette lookup (p<g><r>0 -> p<g>h0 -> pmh0) selects the new PLT for
every playable body. The prosthetic's 2x2 diffuse gradient relied on a TXI bump
replacement for its detail; that bump is baked into the shade because the tint
material does not read TXI bump replacements.

Helmet 64 (the Chadra Fan head) had its mismatched PLT removed and drew its
fixed fur-and-skin artwork. Its new palette puts the bare skin (warm texels) on
the Skin layer and the fur on Hair, so it follows the wearer's skin and hair
colors like a head does.

Run, then convert with: python -B tools/GenerateTintMapAssets.py --generate-preserving
"""
import argparse
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SKIN, HAIR, METAL1 = 0, 1, 2
PLT_HEADER = b"PLT V1  " + (10).to_bytes(4, "little") + bytes(4)

# palette name: (diffuse raster, bump raster or None)
SOURCES = {
    "helm_064": ("sw_pt_helm/helm_064.dds", None),
    "pmh0_shol258": ("sw_pt_lshoul/pmh0_shoL258.dds", None),
    "pmh0_shor258": ("sw_pt_rshoul/pmh0_shoR258.dds", None),
    "pfh0_shol258": ("sw_pt_lshoul/pfh0_shoL258.dds", None),
    "pfh0_shor258": ("sw_pt_rshoul/pfh0_shoR258.dds", None),
    "pmh0_forel215": ("sw_plc_cep/silverarm_sacf.tga", "sw_plc_cep/silverarm_fore.tga"),
    "pmh0_forer215": ("sw_plc_cep/silverarm_sacf.tga", "sw_plc_cep/silverarm_fore.tga"),
    "pmh0_handl215": ("sw_plc_cep/silverarm_sach.tga", "sw_plc_cep/silverarm_hand.tga"),
    "pmh0_handr215": ("sw_plc_cep/silverarm_sach.tga", "sw_plc_cep/silverarm_hand.tga"),
}
FOLDERS = {"helm": "sw_pt_helm", "shol": "sw_pt_lshoul", "shor": "sw_pt_rshoul", "forel": "sw_pt_lfore",
           "forer": "sw_pt_rfore", "handl": "sw_pt_lhand", "handr": "sw_pt_rhand"}


def pixels(relative: str, size: tuple[int, int] | None = None) -> np.ndarray:
    """RGB rows in texture storage order (row 0 is sampled at v = 0)."""
    image = Image.open(ROOT / relative).convert("RGB")
    if size is not None and image.size != size:
        image = image.resize(size, Image.BILINEAR)
    rgb = np.asarray(image, dtype=np.float64)
    # Pillow returns TGA rows top first, but NWN samples a TGA's first stored
    # (bottom) row at v = 0, like a DDS's first stored row.
    return rgb[::-1] if relative.lower().endswith(".tga") else rgb


def luminance(relative: str, size: tuple[int, int] | None = None) -> np.ndarray:
    rgb = pixels(relative, size)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def palette(name: str) -> bytes:
    diffuse, bump = SOURCES[name]
    if bump is None:
        shade = luminance(diffuse)
    else:
        detail = luminance(bump)
        base = luminance(diffuse, (detail.shape[1], detail.shape[0]))
        shade = base * (detail / 255.0) * 1.15
    shade = np.clip(np.rint(shade), 0, 255).astype(np.uint8)
    height, width = shade.shape
    if name.startswith("helm_"):
        rgb = pixels(diffuse)
        warm = (rgb[..., 0] > rgb[..., 1] + 18) & (rgb[..., 0] > rgb[..., 2] + 18)
        layers = np.where(warm, SKIN, HAIR).astype(np.uint8)
    else:
        layers = np.full_like(shade, METAL1)
    data = np.stack([shade, layers], axis=-1)
    return PLT_HEADER + width.to_bytes(4, "little") + height.to_bytes(4, "little") + data.tobytes()


def output_path(name: str) -> Path:
    part = "helm" if name.startswith("helm_") else name[5:].rstrip("0123456789")
    return ROOT / FOLDERS[part] / f"{name}.plt"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[], help="write only these palettes")
    args = parser.parse_args()
    for name in args.only or SOURCES:
        data = palette(name)
        output_path(name).write_bytes(data)
        print(f"{output_path(name).relative_to(ROOT).as_posix()} sha256={hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
