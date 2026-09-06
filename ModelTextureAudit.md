# Model texture audit — 2026-09-06

The two reported heads are repaired in the source assets and packed repair haks.
The full custom-model collection is **not clean**: the audit still finds 479
unresolved texture resource names across 942 model resources (2,213 distinct
model/node/reference findings). These are missing-reference findings, not a claim
that all 942 resources are selectable or visibly broken in game.

## Repairs

| Models | Cause and repair |
| --- | --- |
| Human female head 232 (`pfh0_head232`) | The model used a preserved authored material whose `texture0` still requested the removed PLT as a raster. Rebound all three meshes to the existing mapped tint material. |
| Human male head 231 (`pmh0_head231`) | The same error affected all six meshes. Rebound them to their existing mapped tint material. |
| Male right thigh 265, human/dwarf/elf/half-orc (`pmh0_legr265`, `pmd0_legr265`, `pme0_legr265`, `pmo0_legr265`) | Recovered the exact human palette from Git before the conversion, converted it through the existing generator, and restored the four consuming model bindings. The packed pixels deduplicate to an already present texture. |
| Neck 2, both sexes, six stock race families, phenotypes 0/2 | Imported the 24 previously stock-only model variants and both original neck-2 palettes. Added explicit tint bindings and catalog rows for Skin and Cloth1; the native meshes had no tint-catalog coverage. Verified geometry/UV/skinning bytes unchanged and all 4,096 palette-layer assignments per sex preserved. |
| Female Wookiee chest 209 (`pfe0_chest209`) | The original female PLT assigned equipment to Hair. Used the matching male UV atlas to restore equipment channels only where the female texture used Hair, including 69,364 leather-strap pixels. Preserved every female shade byte and existing metal/tattoo assignment. `tools/RepairWookieeHarness.py` reproduces the source correction from hash-verified historical palettes. |
| Stock base body parts, including female Rodian pelvis 1 | Imported 475 omitted stock meshes across both sexes, six race families, and phenotypes 0/2. Converted 18 stock palettes using native palette precedence, preserving existing custom palette choices. Covers pelvis/feet 1 and torso/bicep/forearm/hand/thigh/shin 1/2. Every imported mesh has a Skin binding and matches its original geometry, UV, and skinning hash. `tools/StockBodyTintModels.json` records provenance; `tools/ImportStockBodyTintModels.py` reproduces the import. |
| Six Gith creature/animation models | Added the generated material binding to their existing `pmh0_forel002` surfaces, which consume the restored tattooed forearm palette. |

The generator now recognizes an explicit diffuse slot backed by a converted PLT,
while retaining genuinely fixed raster materials and unresolved authored inputs
without a proven replacement. Normal/specular inputs, shader behavior, palette
layers, and the original UV-compatible pixels are retained. Six model/material
rows were added to `tintmap.2da` for the initial repairs, followed by 24 neck-2 rows.

The recovered thigh palette has SHA-256
`940c846cb05ea6859b644c646c779c73df5a6de5b5e1a999c1c58823fe8f4137`,
dimensions 1024 × 1024, and palette layers 2, 4, 5, 6. Its older missing
`pmh0_legr265_s` specular reference remains explicitly reported.

## Complete findings

- [Every model/node/reference finding](model_texture_audit.csv)
- [Findings grouped by texture and model](model_texture_resources.csv)

Examples needing original source artwork or an individually reviewed replacement:

- Human male head 266: `horns_zab1` diffuse map is absent.
- Human female heads 252, 253, 254, 255, 256, 259, 260, 261, 270, and 271:
  one or more authored normal/specular/illumination maps are absent.
- Human male heads 284 and 289 and Zabrak heads 033/036: auxiliary maps are absent.
- Creature and tileset resources also contain unresolved diffuse names. Some
  models are animation helpers or use appearance-table/runtime texture selection;
  the CSV intentionally retains their unresolved embedded references for review.

No remaining exact raster/PLT filename was found in either source snapshot
immediately before the PLT conversion (`de1e6b21f00^`) or the hak reorganization
(`2ea4a29958a^`). This does not prove those files never existed in an upstream
author's package. Arbitrary substitute artwork was not assigned to these meshes.

## Coverage and limits

The scan parsed all 67,044 winning custom MDLs in the module's configured haks:
62,630 binary and 4,414 ASCII models, with no parse failures. It examined 616,577
render-surface/emitter records and 599,150 texture-reference occurrences.

Resolution includes:

- Module HAK precedence, case-insensitive resource names, and installed stock
  `nwn_base.key` / `nwn_retail.key` BIF resources.
- Installed texture-pack ERFs and installation overrides, including stock MTR
  and TXI declarations rather than merely checking whether a material exists.
- Rendered mesh bitmaps, material texture slots, lightmaps, particle textures,
  and recursively referenced TXI bump/environment maps.
- DDS/TGA/PLT/KTX resource names, the native 16-character resource identifier,
  and complete six-face environment maps. The scan encountered 788 overlong
  reference occurrences and resolved 1,924 environment-map occurrences as cubes.
- Exclusion of non-rendered/walkmesh geometry, unused texture labels on chunk
  emitters, and native dynamic-cloak palette selection.

This is a resource-dependency audit, not an in-game screenshot review of every
model. It does not certify client overrides, an installed server/client's deployed
hak versions, arbitrary script-driven texture swaps, visual suitability, UVs, or
every non-tint image's pixel payload. Missing auxiliary maps can affect appearance
without removing the diffuse texture. A model in an active hak may be unused by
the world or used only as an animation source.

## Validation and delivery

- 69 tint regression tests and six texture-audit tests pass.
- The final tint catalog audit passes for 6,831 materials, 28,404 model/material
  rows, and 27,869 compiled tint models.
- The 96 relevant server tint/robe tests also pass.
- The dependent robe rebuild validated 3,256 generated models, 1,596 independent
  garment skeletons, and 114,560 body-pose samples with no failures.
  Recompilation changed unused bytes after NUL-terminated animation-event names
  in 64 animation files. After proving all differences were confined to that
  padding, the original byte-identical files were retained and the final robe
  catalog/source/output check passed. No animation changes are included.
- Twenty-two repair haks were packed under `output/texture-repair-haks/`: `sw_2da`,
  `sw_pt_head`, `sw_pt_neck`, all fourteen base body-part haks, `sw_tint_mtr`,
  `sw_tint0`, `sw_tint1`, `sw_tint2`, and `sw_cr_creature`. All 40,925 packed
  resources were compared byte-for-byte with their source files. Archive hashes
  and resource counts are recorded in that directory's `manifest.json`.
- The haks have not been installed on a live server or client, and the repaired
  heads still need in-game visual confirmation after deployment.

Repeat the audit from this repository:

```powershell
python -B tools/AuditModelTextures.py --game-data "<NWN installation>/data"
python -B tools/TestModelTextures.py
python -B -m unittest discover -s tools -p 'TestTint*.py'
python -B tools/GenerateTintMapAssets.py --check
```

`AuditModelTextures.py` intentionally exits with status 1 while unresolved
references remain; it writes `output/model-texture-audit/summary.json` and
`missing.csv`. Existing findings are not silently allowlisted.
