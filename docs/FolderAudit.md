# Hak folder audit — 2026-09-05

Reviewed **160,163 original files (13,127,759,088 bytes)** across all **115 source/support folders and 13 root files**. Moved **1,875 files** to evidence-backed categories. All original filenames and contents are unchanged. Five audit documents were added under `docs/`, outside the packaged hak folders.

## Review records

- [Every move, evidence, original size and SHA-256](FolderAuditMoves.csv)
- [Every explicitly uncertain file retained](FolderAuditUncertain.csv): **2,368 files**
- [Textures without an explicit model/material reference](FolderAuditUnreferencedTextures.csv): **16,273 files**, retained; five empty materials overlap the uncertain list
- [Complete before/after folder counts](FolderAuditCoverage.csv)

Paths in these CSV files are relative to the hak repository. Evidence paths are mapped to their final locations where moved. Detailed per-file inventories, original hashes, decoded-reference verification results and the guarded move script are available locally in ignored `output/hak_folder_audit/`; the review CSVs above are the durable repository record.

## What moved

Most changes reunite placeable textures and material companions with the models that consume them. Additional corrections move vehicle, creature, head and weapon textures to their consumers; physical wing/tail models out of VFX; perk-window requirement indicators into UI; a seven-file CEP Forest cube map out of Mine; and six explicitly `[mdrn]`-labelled placeable model families, their walkmeshes and three exclusively owned textures into modern placeables.

| Original folder | Destination folder | Files |
| --- | --- | ---: |
| `sw_plc` | `sw_plc_mdrn` | 675 |
| `sw_plc` | `sw_plc_cep` | 510 |
| `sw_plc_cep` | `sw_plc` | 382 |
| `sw_plc_cep` | `sw_plc_mdrn` | 179 |
| `sw_plc` | `sw_cr_vehicle` | 45 |
| `sw_plc` | `sw_weapon` | 12 |
| `sw_ability` | `sw_ui` | 11 |
| `sw_plc_mdrn` | `sw_plc` | 11 |
| `sw_vfx` | `sw_cr_creature` | 10 |
| `sw_plc` | `sw_pt_head` | 8 |
| `sw_t_mine` | `sw_t_cepforest` | 7 |
| `sw_plc` | `sw_cr_creature` | 7 |
| `sw_plc` | `sw_t_modernex` | 4 |
| `sw_plc_cep` | `sw_cr_creature` | 4 |
| `sw_plc_cep` | `sw_weapon` | 4 |
| `sw_cr_creature` | `sw_cr_vehicle` | 2 |
| `sw_plc_cep` | `sw_plc_swtor` | 2 |
| `sw_plc` | `sw_item` | 1 |
| `sw_plc_mdrn` | `sw_skybox` | 1 |

## Coverage and method

Three independent reviewers covered disjoint inventories, reconciled against the complete hashed baseline: 47,199 tileset files, 86,513 creature/placeable/body-part/door/item/weapon files, and 26,438 support files plus 13 root files. Every baseline file appears exactly once in the combined inventory.

The review checked file extensions and naming families, build configuration membership, relevant 2DA ownership columns, all 70 tileset SET definitions, model/material/TXI dependencies, model and walkmesh companions, texture headers, portraits, audio, palettes, shaders, UI, source artwork, TLK files and root tooling. Every source MDL/MTR/TXI was scanned: 11,591 readable files and 54,329 compiled models. For proposed texture relocations, 1,912 cited files were parsed or decompiled to confirm real bitmap/material/texture declarations; there were no decoder failures. The dependency graph was checked across all categories to retain shared resources.

This is a file-ownership and packaging audit, not a visual inspection of every image or a claim that every legacy asset has a proven runtime consumer. Names and the existing reorganization script's fallback rules were not treated as sufficient evidence for moving ambiguous files. Short byte matches and mesh-node names can look like texture references: decompilation rejected 11 such candidates. The six moved modern model families had their texture ownership recomputed after relocation.

## Files deliberately left in place

All paths and reasons appear in the linked review lists. Important groups include:

- Shared textures used across several tilesets or placeable categories, including `sw_t_crypt/zdc04_deco_01.dds` and `zdc04_lshaft_01.dds`. The global scan found additional CEP-placeable consumers, overriding the initial tileset-only recommendation.
- Legacy CEP/modern/core placeables with ambiguous pack provenance or inconsistent table labels; alternate tileset models, minimaps, materials and documentation without a unique owner. Some apparently foreign prefixes are intentional: the Beholder SET includes `zib01`/`zdc04` models, and Castle includes `zic01` extensions.
- `sw_vfx/asteriods.mdl`: sky-scale geometry and skybox texture, but declared EFFECT; intended sky-overlay/VFX ownership remains unclear.
- `sw_palette/cep_tilesets.txt`: documentation spanning multiple tilesets.
- Nine legacy `sw_ability/is_*` icons without exact 2DA matches; their existing ability category was retained.
- Two helmet-like textures, one left-bicep PLT also referenced by a right-bicep model, and five pre-existing zero-byte material files. No content repairs were attempted.
- The 16,273 textures with no explicit model/material dependency. This does not establish that they are unused: engine naming rules, dynamic lookups, base-game content and legacy overrides may account for them. They were neither deleted nor relocated on that basis.

Confirmed exceptions kept in place include `sw_vfx/tron.mdl` and its walkmesh (placement-grid VFX), `sw_ui/ctl_loadstatus.mdl` (UI animation), VFX exposed through wing/tail attachment tables, source PNGs in `sw_ability_source`, and root tools used by the build scripts.

## Verification

- Rehashed all **160,163 original files after moving**: every SHA-256 and size matches the baseline.
- Exact original file inventory preserved after applying the move mapping; no original files added, lost or overwritten.
- No duplicate packaged resource filenames before or after relocation.
- Both hak build configurations and the module agree on all **113 packaged haks**. No hak names, load order, 2DA/TLK contents or build configuration changed.
- Gameplay icon standards audit passed for **1,466 manifest entries**.
- No hardcoded old source-path references found in server/build/tool sources for the relocated files (resource names remain unchanged).
- No hak archives or module were rebuilt or deployed; these are source-folder changes. Rebuild the affected haks before deployment.
- Temporary decompilation directories were removed; no audit helper process remains running.
