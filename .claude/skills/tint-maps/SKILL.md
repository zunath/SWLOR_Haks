---
name: tint-maps
description: Rules for the packed tint maps (tm_* masks), the palette atlas and the tint shaders (fs_plt_tinter, fs_plt_tinter_nm, fs_plt_hair_nm, fs_hav_*, fs_cloaktint). Use when changing a tint shader, GenerateTintMapAssets.py, CloakTintAssets.py, a tint .mtr or .txi; when a dyed part glitters, looks grainy at a distance, fizzes at a dye border or turns magenta; or before an in-game tint check.
---

# Tint maps

A packed tint map (`tm_*`, BC5) holds two channels per texel. They need opposite sampling, and most tint bugs come from mixing them up.

## Sampling rules

- **R is the shade: average it at a distance.** Up close, read the exact base-level shade (`texture2D(texUnit7, wrappedUV, -10.0).r`). Once a texel is smaller than a pixel, blend to the mipmapped shade (`texture2D(texUnit7, vVertexTexCoords).r`), weighted by `far = clamp(max(length(dFdx(texels)), length(dFdy(texels))) - 1.0, 0.0, 1.0)` with `texels = vVertexTexCoords * tintMapSize`. A level-0 shade at a distance picks one random texel per pixel: grain and glitter. A mipmapped shade up close averages across a dye border: the border fizzes.
- **G is the layer id: never filter it.** Read it with `texelFetch(texUnit7, texel, 0)` under `#if __VERSION__ >= 130`. Halfway between two ids is a third, wrong layer. A texel-centre `texture2D` read is not enough: with mipmaps present, anisotropic filtering (16x is the client default) samples several texels even at level 0.
- **Take derivatives from unwrapped UVs.** `fract(vVertexTexCoords)` jumps at a UV seam, and a mip level picked from it there is the smallest one. Sample mipmapped reads with `vVertexTexCoords`; the mask repeats.
- **`ResolveTintTexel` takes a texel centre.** Fetch that texel with `texelFetch`. The smoothing path blends finished colours, never ids or shades.

## Asset rules

- **Every tint mask stores its full mip chain to 1x1, with a `mipmap 1` TXI.** `write_packed_dds` does this. Rebuilding the chain (`--refresh-packed-metadata`) never changes the base level.
- **A DDS declares exactly the levels it stores.** A base-only DDS with no declared count once crashed the client: the loader read a mip level past the payload. `tint_dds_layout` in `TestTintShaderMaterials.py` guards this. Keep its negative controls.
- **The cloak atlas stays single-level with `mipmap 0`** (`mipmaps=False`). `fs_cloaktint` reads it with a `-10` bias and has no far-shade blend.
- **The palette atlas `plt_palette` keeps `mipmap 0`.** Its rows are categories too.
- **`RobeRgbModels.json` hashes `GenerateTintMapAssets.py`.** Any generator edit fails `GenerateRobeRgbModels.py --check` until the robes are regenerated, or the hash is refreshed for a change that cannot reach robe output.

## Robes

Robe meshes never receive per-creature uniforms, so they render native palette colours through `useNativePalette`. They still run the tint shader, so every sampling bug above shows on robes too. A robe is the quickest place to see grain and border fizz.

## In-game checks

1. Build the override from the shader versions the live server ships, not from `master`. A shader newer than the client's haks can fail to compile and turn every dyed part magenta. Find the live version by hashing the shader in the client's nwsync cache against `git log` of the file.
2. Put the changed shaders, masks and TXIs in the game's `development` folder, which outranks every hak. Keep a list of what you put there, and remove exactly that list afterwards.
3. Restart the client fully. A shader change does not show otherwise.
4. Look at a dyed robe from far away (grain), the robe collar up close (border fizz), a dyed helmet, dyed hair and a cloak.
5. Read `logs/nwengineLog.txt` in the game's user folder. `Failed to link material custom parameter` for every row uniform means the shader did not compile.
6. Compare with the override off before blaming a change. Split the override (masks only, shaders only) to find which half causes a defect.

## Tests

`GenerateTintMapAssets.py --check`, `GenerateRobeRgbModels.py --check`, `TestTintMaterialProfiles.py` and `TestDyeAppearance.py` run anywhere. `TestTintShaderMaterials.py` and `TestHelmetTintFiltering.py` render through WGL and need Windows and `--game-data`.
