# Portrait orientation and DDS conversion

## Current portrait pack

The NPC validity repair adds ten DDS/TXI pairs alongside the original conversion
corpus (8,119 DDS total). `portrait_size_repairs.csv` records their source names,
dimensions, and source/output SHA-256 hashes. Four missing Tiny images (Bith, Gran, female
Duros, male Kel Dor) are resized from their existing Small DDS images to 16x32.
The missing `abbasilisk_` and `stat_wwolf_` Large images are resized from Medium
to 128x256. The otherwise missing `parrot_` set uses NWN:EE's stock
`po_plc_parrot_m.tga` from `data/xp2patch.bif` at Large/Medium/Small/Tiny sizes.

Repairs use ImageMagick's `-resize WIDTHxHEIGHT! -define dds:compression=dxt1
-define dds:mipmaps=0`. Existing DDS scanline orientation is preserved; the stock
TGA is flipped vertically before DDS encoding, matching the existing converter.
Each DDS header explicitly sets DDSD_MIPMAPCOUNT and one mip level, and each TXI
contains `mipmap 0`. The original conversion/orientation manifests are unchanged.
Rebuild `sw_portrait.hak` when deploying these additions.

The source digest pins the bytes used to produce each repaired size. If a source
is corrected or reconverted, regenerate its dependent repairs and update both
digests together. `PortraitDdsCorpusTests` checks the current six DDS sources and
the stock parrot TGA through the toolset resource index. The stock-source and NPC
reference checks require a local NWN:EE installation (or `NWN_INSTALL_PATH`);
they report a skip when game data is unavailable. Custom-source checks always run.

All 8,119 portraits now ship as compressed DDS, including Medium and the three non-power-of-two images. No portrait TGAs or Huge portraits remain. Opaque images use DXT1 (7,970); images with non-opaque alpha use DXT5 (149). The original 8,109 conversions preserve their dimensions, framing, and facing; the ten size repairs use the dimensions described above. Each DDS explicitly declares one mip level and has a sibling TXI containing `mipmap 0`.

| Portrait HAK stage | Bytes | MiB |
| --- | ---: | ---: |
| Before Huge removal | 490,690,668 | 467.96 |
| TGA pack after Huge removal | 233,610,462 | 222.79 |
| Original DDS conversion pack, including TXIs (before size repairs) | 41,708,861 | 39.78 |
| Current DDS pack, including TXIs and ten size repairs | 41,766,423 | 39.83 |

The current DDS pack saves **191,844,039 bytes (182.96 MiB, 82.12%)** against the TGA pack after Huge removal. The built HAK contains 8,119 DDS and 8,119 TXI resources, all byte-compared with their sources. These are built HAK sizes, not compressed download estimates.

The conversion uses ImageMagick 7.1.2-22 with the approved DXT1/DXT5 compression settings and no generated mipmaps. Standard DDS pixels are vertically reversed before encoding to match NWN's storage convention, as documented by the [NWN Crunch portrait converter](https://neverwintervault.org/project/nwnee/other/tool/nwn-crunch-enhanced-edition). Generic DDS viewers display those stored rows upside down; NWN and the SWLOR toolset reverse them when sampling. Independent comparison of every output in this display orientation against the TGA sources found no horizontal/vertical orientation mismatches or dimension changes; all opaque sources remain opaque. Average per-image RGB absolute error is 1.215/255. The twelve images with the highest error and all three irregular dimensions were visually reviewed. DDS is lossy; the earlier lossless orientation correction is preserved in the converted source provenance.

`portrait_dds_conversions.csv` records every original TGA source hash, corresponding DDS hash, dimensions, format, byte sizes, and an 8x8 RGB grid signature from the original displayed image. Original TGAs are retained in git history at `8f1592587e3ecb8378e5d3ce47f670ef20a603f2`; they are not included in the HAK. Historical orientation and Huge-removal manifests retain their original TGA names/hashes, resolved through this conversion manifest during verification.

## Runtime behavior and size reduction

NWN:EE 8193.35 introduced fallback to the closest available portrait size when a requested size is missing. With Huge absent and Large present, character creation can display Large. This behavior is documented in [Beamdog's official 8193.35 patch notes](https://nwn.beamdog.net/docs/patchnotes/87.8193.35.md), published with the [May 24, 2023 release](https://www.beamdog.com/news/new-patch-neverwinter-nights-enhanced-edition-arrives-today/). SWLOR's deployment targets 8193.37.15. This relies on the Enhanced Edition fallback and does not claim compatibility with older clients.

Removed all 1,027 Huge TGAs after verifying each had its corresponding Large TGA. No retained portrait pixels were changed by that removal. Character creation uses the lower-resolution Large composition, now supplied as DDS; when H and L previously used different artwork, the Large artwork is displayed. The same official 8193.35 notes remove the Medium-TGA requirement for DDS portraits; 8193.36 adds DDS support to NUI. No in-game rendering check or live deployment was performed.

| Portrait HAK | Bytes | MiB |
| --- | ---: | ---: |
| Before Huge removal | 490,690,668 | 467.96 |
| After Huge removal | 233,610,462 | 222.79 |
| Saved | 257,080,206 | 245.17 |

Huge removal reduced the then-TGA HAK by **52.39%**, from 9,136 to 8,109 images. `portrait_huge_removals.csv` records every removed file, its hash/size, and its original Large fallback/hash. Git history retains the original Huge artwork without shipping it in the HAK.

## Orientation corrections

Before the removal, all 9,136 TGAs across 2,184 case-insensitive families were audited, using Huge as orientation reference where available. The audit compared 6,952 non-reference images through resized pixel comparisons and geometric feature matching, with sibling sizes bridging different crops. All 222 initial flip candidates and 545 uncertain pairs were visually reviewed. Manual review added 35 corrections; post-edit inspection caught and removed one automatic false positive (`po_martian1953_s`).

The final correction batch contains 256 exact horizontal pixel reflections across 200 families: 1 Large, 157 Medium, 60 Small, and 38 Tiny. These corrections remain intact after Huge removal. The character-selection example `po_mm_f_27` has M/S corrected to agree with L/T. Different artwork is preserved; `po_rodF9` L/M/S/T and `po_bisonant` S/T were flipped to match the original reference facing.

`portrait_orientation_corrections.csv` now uses retained Large references, or Medium when Large is absent, with their current hashes. A retained reference can itself be in the correction batch: the replay tool validates its complete planned correction before allowing dependent images to use it. `portrait_orientation_review.csv` preserves the 545 visual decisions with the historical `original_audit_reference` name; these historical references may be removed H files and are not runtime dependencies.

## Verification

- All 256 corrections are exact reflections, including alpha and texture padding, with dimensions/headers/image IDs preserved. RLE extension offsets are relocated as needed without changing their metadata contents.
- Huge removal deleted exactly the 1,027 recorded files without altering the remaining TGAs; subsequent DDS conversion records all 8,109 source hashes.
- All 1,027 Large DDS fallbacks exist and match their conversion hashes and historical source provenance; no Huge or TGA remains in `sw_portrait`.
- All 16,238 resources in the rebuilt HAK were byte-compared with their source files.
- Python tests cover the TGA utility, complete-manifest enforcement, replay/idempotence, pending/self-referencing canonicals, tampering, DDS dimensions/format/single-mip/payload/TXI validation, and the complete Huge-to-Large fallback corpus.
- The focused toolset portrait lookup tests decode T/S/M/L DDS resources and check intentional absence of H and TGAs.
- `PortraitDdsCorpusTests` loads the original 8,109 converted DDS portraits through the toolset's resource index and actual decoder, comparing dimensions and displayed RGB grids against the original TGA signatures. The test rejects the earlier top-down DDS encoding, which would have displayed upside down. The ten supplemental size repairs are covered separately by the Python resource tests, including hashes, dimensions, DXT format, payload size, mip flags, and TXIs.

From the parent repository:

```powershell
python tools/NormalizePortraitOrientations.py
python -m unittest discover -s tools/tests -p 'test_portrait*.py'
```

`python tools/NormalizePortraitOrientations.py --apply` replays only the explicit 256 reviewed flips against restored original TGA hashes. It verifies already-converted DDS files through the complete conversion manifest and refuses to perform a new lossless correction on DDS pixels. It never guesses orientation, requires the full batch, preflights every correction before writing, and is idempotent. Future artwork or correction-batch changes require updating the reviewed manifests and corresponding expectations.

To reproduce conversion, restore the TGA source pack from the historical commit into a separate directory, then run `python tools/ConvertPortraitsToDds.py --source <tga-directory> --output <new-staging-directory>` from the parent repository. This requires Pillow and ImageMagick. The tool stages every DDS/TXI and a conversion manifest without modifying the TGA source directory.
