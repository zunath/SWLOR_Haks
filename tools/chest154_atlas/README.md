# Reproducible chest154–157 tint atlas (512 × 512)

This directory contains the inputs needed to **rebuild the already-approved 512 × 512 ATI2/BC5 atlas** `sw_tint2/tm_a0694cacd9d05.dds` without reading that DDS as a generation input.

- `original_chest154.plt` is the archived unedited 512 × 512 PLT from commit `cddf857282a4992878e642f223a5c2e16f142688`. Its SHA-256 matches the `sourceSha256` recorded for all eight chest154–157 entries in `TintMapSources.json`.
- `approved_shades_512.pgm` is an editable 8-bit grayscale image containing the *approved enhanced shade samples*.
- `approved_mip_shades.u8` stores the corresponding approved grayscale source samples for each smaller mipmap, in order from 256 × 256 to 1 × 1. Retaining these values avoids small rounding differences after BC5 compression.
- `recipe.json` pins input provenance, output format and expected Git blob checksum.
- `../RebuildChest154TintAtlas.py` combines the approved shade samples with the **unaltered original PLT dye-layer plane**, then uses the same BC5 encoder already present in `GenerateTintMapAssets.py`.

To verify exact reproduction on a checkout with Python and NumPy:

```sh
python3 -B tools/RebuildChest154TintAtlas.py --check
```

To reconstruct the DDS and its TXI from these inputs:

```sh
python3 -B tools/RebuildChest154TintAtlas.py --apply
```

The `--check` command reconstructs the DDS in memory and verifies its **entire binary content**, full 10-level mip chain, 512 × 512 dimensions, unchanged dye categories, eight catalog bindings and `mipmap 1` TXI. It does **not** overwrite files.

## Provenance limitation

The original artist's editable layered project/history was not available. The grayscale source was therefore recovered from the accepted compressed 512 × 512 DDS at quantized shader-sample precision. The recovery was checked by round-tripping every BC4 shade block. This is an **independent, bit-exact reconstruction source**, not a claim to recover the historical brush strokes or authoring steps. The shipped texture, MTR materials, shaders and palette/RGB rendering are unchanged.

If future artists wish to edit the atlas, they can modify the PGM and intentionally rebuild the mip-shade source and expected output checksum. The current recipe pins the previously approved output so unintended changes fail loudly.
