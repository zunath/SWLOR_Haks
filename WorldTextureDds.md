# World texture DDS conversion

This reviewed batch replaces **2,089 world-texture TGAs with standard DDS** and saves **517,263,083 bytes (517.26 MB / 493.30 MiB)** in shipped HAK payloads. Texture dimensions and resource names are retained. No model, material, TXI, 2DA, UI, inventory, or gameplay-icon resource was changed.

| Measure | Before | After |
|---|---:|---:|
| Converted texture payloads | 688,405,699 bytes | 171,142,616 bytes |
| Complete 40 affected HAKs | 5,283,042,204 bytes | 4,765,779,121 bytes |

The texture payloads shrink by 75.14%; the complete affected HAKs shrink by 9.79%. The before-HAK total is reconstructed from unchanged resource counts/container tables plus recorded TGA payload lengths. No compressed-download saving is claimed. Existing DDS portraits are not counted again.

## Reviewed scope

Only reviewed ordinary world textures with power-of-two dimensions of at least 128x128 are included. The batch retains UI/inventory/ability folders, scattered legacy icons, minimaps, palettes, emitters, auxiliary material maps, metadata-sensitive textures, ambiguous resource collisions, small/irregular images and files that would grow. SET `ImageMap2D` and `EnvMap` references are resolved explicitly; legacy `mi*`/`mz*` minimap names are also held even without a defining loose SET.

All 67,044 binary/ASCII models were scanned for special texture roles, with no parse errors. Installed stock TXI/MTR metadata and all 33 stock SETs were also checked. The toolset minimap preview explicitly loads TGAs. Texture selection therefore requires a reviewed allowlist and cannot rely on file extensions or name prefixes alone.

`tools/WorldTextureDdsScopeHolds.json` records 808 additional small/special textures retained during final scope review. `tools/WorldTextureDdsQualityHolds.json` records 54 retained TGAs whose trial DDS exceeded this batch's conservative quality limits: RGB mean absolute error >= 8/255 or any RGB channel error >= 180/255. These are asset-review choices, not universal guarantees of image quality.

## Encoding and provenance

- Encoder: Version: ImageMagick 7.1.2-22 Q16-HDRI x64 ccf56fb:20260512 https://imagemagick.org.
- Explicit `dds:cluster-fit=true`; BC1/DXT1 for opaque images, BC3/DXT5 for any nonopaque alpha.
- Every output has a complete mip chain through 1x1, with its standard DDS header and exact payload length validated.
- TGA origin flags are normalized, then displayed rows are reversed for NWN's standard DDS convention. Generic DDS viewers show the stored rows upside down; NWN/toolset decoding restores display orientation.
- Preparation retains RGB beneath transparency; it does not flatten images, resize the base level or replace TXI metadata.
- Mean per-image RGB absolute error is 1.741/255; mean alpha error is 0.080/255. No orientation mismatch was detected. High-error RGB/alpha pairs were visually reviewed, with noisy artwork retained as TGA when unsuitable.

`tools/WorldTextureDdsSources.json` pins every original TGA by SHA-256, dimensions, alpha classification and expected DDS size. `tools/WorldTextureDdsConversions.json` records output hashes, sizes, mip counts, display grids and error metrics. The original lossless TGAs remain in Git history at **565e5901801a73f8f15d0971264428d3870f969d**; they are not duplicated in packed directories. Hash-pinned manifests use LF line endings on every platform.

The parent repository provides `tools/SelectWorldTextureDdsCandidates.py`, `tools/ConvertWorldTexturesToDds.py`, focused Python tests and the toolset corpus regression. See its `SWLOR.Game.Server/Readmes/WorldTextureDds.md` for repeatable staging/validation commands. Selection starts from a reviewed allowlist and can only narrow it.

## Verification

- All 2,089 installed DDS files passed pinned-hash, dimensions, format, alpha, complete-mipmap and decoded-pixel validation.
- All 40 affected HAKs were rebuilt with `nwn_erf`. All **71,482 packed resources**, including unchanged resources, matched their loose source lengths and SHA-256 hashes. Converted keys are DDS and no corresponding TGA remains in these HAKs.
- The actual toolset loader resolved/decoded every replacement and matched its spatial RGB signature. All 20 focused toolset texture tests passed, including the complete conversion corpus.
- All 29 focused Python converter/selector tests passed, covering orientation/alpha, actual encoding, metadata exclusions, safe staging and tamper detection.
- No in-game client rendering test or live deployment was performed. Deploy the affected HAKs together when the linked PRs are merged.

## Affected HAKs

| HAK | Converted textures | Packed bytes | Saved bytes |
|---|---:|---:|---:|
| sw_cr_creature.hak | 334 | 943,157,053 | 98,436,687 |
| sw_cr_vehicle.hak | 156 | 150,014,949 | 58,258,242 |
| sw_door.hak | 5 | 124,141,440 | 829,570 |
| sw_plc.hak | 822 | 539,943,030 | 138,577,884 |
| sw_plc_cep.hak | 70 | 163,924,562 | 15,994,718 |
| sw_plc_mdrn.hak | 56 | 370,723,967 | 11,137,068 |
| sw_pt_helm.hak | 16 | 80,525,595 | 5,728,533 |
| sw_pt_lshin.hak | 1 | 36,389,274 | 51,339 |
| sw_pt_lshoul.hak | 2 | 40,202,964 | 1,223,144 |
| sw_pt_neck.hak | 2 | 212,799,117 | 1,223,144 |
| sw_pt_robe.hak | 1 | 334,400,265 | 2,446,580 |
| sw_pt_rshoul.hak | 2 | 42,603,468 | 1,223,144 |
| sw_skybox.hak | 47 | 132,790,350 | 16,650,840 |
| sw_t_alienruin.hak | 64 | 23,346,097 | 17,546,312 |
| sw_t_bunker.hak | 26 | 5,389,598 | 12,230,180 |
| sw_t_castle1.hak | 4 | 28,084,158 | 878,856 |
| sw_t_cepcityex.hak | 12 | 108,004,003 | 523,140 |
| sw_t_cepcrypt.hak | 5 | 38,379,391 | 219,916 |
| sw_t_cepdesert.hak | 2 | 42,880,656 | 218,232 |
| sw_t_cepdungeon.hak | 1 | 34,077,592 | 38,106 |
| sw_t_cepforest.hak | 4 | 60,736,556 | 436,464 |
| sw_t_cepmine.hak | 10 | 95,607,059 | 669,199 |
| sw_t_cepswamp.hak | 14 | 26,193,253 | 1,789,768 |
| sw_t_dungeon.hak | 1 | 1,571,614 | 38,132 |
| sw_t_futcity.hak | 13 | 13,180,341 | 2,691,140 |
| sw_t_jungle.hak | 51 | 33,816,549 | 4,232,826 |
| sw_t_labstore.hak | 44 | 27,368,936 | 2,628,590 |
| sw_t_metalint.hak | 50 | 16,938,353 | 20,228,298 |
| sw_t_modernex.hak | 2 | 60,808,500 | 808,064 |
| sw_t_modint.hak | 47 | 30,013,088 | 8,214,342 |
| sw_t_modint2.hak | 40 | 7,002,337 | 11,732,512 |
| sw_t_planet.hak | 66 | 30,881,551 | 22,860,274 |
| sw_t_ravforest.hak | 14 | 6,418,299 | 4,913,792 |
| sw_t_season.hak | 83 | 610,187,579 | 42,928,746 |
| sw_t_secbase.hak | 1 | 23,773,393 | 43,616 |
| sw_t_secretbs.hak | 1 | 23,670,547 | 43,616 |
| sw_t_suburb.hak | 1 | 85,755,128 | 153,107 |
| sw_t_swprefab.hak | 10 | 6,582,067 | 6,115,720 |
| sw_t_tatooine.hak | 8 | 59,343,588 | 3,124,590 |
| sw_t_treetop.hak | 1 | 94,152,854 | 174,652 |
