# Portrait orientation and Huge-image removal

## Runtime behavior and size reduction

NWN:EE 8193.35 introduced fallback to the closest available portrait size when a requested size is missing. With Huge absent and Large present, character creation can display Large. This behavior is documented in [Beamdog's official 8193.35 patch notes](https://nwn.beamdog.net/docs/patchnotes/87.8193.35.md), published with the [May 24, 2023 release](https://www.beamdog.com/news/new-patch-neverwinter-nights-enhanced-edition-arrives-today/). SWLOR's deployment targets 8193.37.15. This relies on the Enhanced Edition fallback and does not claim compatibility with older clients.

Removed all 1,027 Huge TGAs after verifying each has its corresponding Large TGA. No retained portrait pixels were changed by this removal. Character creation now uses the lower-resolution Large composition; when H and L previously used different artwork, the Large artwork is displayed. No in-game rendering check or live deployment was performed.

| Portrait HAK | Bytes | MiB |
| --- | ---: | ---: |
| Before Huge removal | 490,690,668 | 467.96 |
| After Huge removal | 233,610,462 | 222.79 |
| Saved | 257,080,206 | 245.17 |

This reduces the built HAK by **52.39%**, from 9,136 to 8,109 resources. `portrait_huge_removals.csv` records every removed file, its hash/size, and its retained Large fallback/hash. Git history retains the original Huge artwork without shipping it in the HAK.

## Orientation corrections

Before the removal, all 9,136 TGAs across 2,184 case-insensitive families were audited, using Huge as orientation reference where available. The audit compared 6,952 non-reference images through resized pixel comparisons and geometric feature matching, with sibling sizes bridging different crops. All 222 initial flip candidates and 545 uncertain pairs were visually reviewed. Manual review added 35 corrections; post-edit inspection caught and removed one automatic false positive (`po_martian1953_s`).

The final correction batch contains 256 exact horizontal pixel reflections across 200 families: 1 Large, 157 Medium, 60 Small, and 38 Tiny. These corrections remain intact after Huge removal. The character-selection example `po_mm_f_27` has M/S corrected to agree with L/T. Different artwork is preserved; `po_rodF9` L/M/S/T and `po_bisonant` S/T were flipped to match the original reference facing.

`portrait_orientation_corrections.csv` now uses retained Large references, or Medium when Large is absent, with their current hashes. A retained reference can itself be in the correction batch: the replay tool validates its complete planned correction before allowing dependent images to use it. `portrait_orientation_review.csv` preserves the 545 visual decisions with the historical `original_audit_reference` name; these historical references may be removed H files and are not runtime dependencies.

## Verification

- All 256 corrections are exact reflections, including alpha and texture padding, with dimensions/headers/image IDs preserved. RLE extension offsets are relocated as needed without changing their metadata contents.
- The removal deletes exactly the 1,027 recorded Huge files. All 8,109 retained TGAs are byte-identical to their pre-removal versions.
- All 1,027 Large fallbacks exist and match their recorded hashes; no Huge TGA remains in `sw_portrait`.
- All 8,109 resources in the rebuilt HAK were byte-compared with their source files.
- Seventeen Python tests cover the TGA utility, complete-manifest enforcement, replay/idempotence, pending/self-referencing canonicals, tampering, and the complete Huge-to-Large fallback corpus.
- The focused toolset portrait lookup tests check the retained T/S/M/L resources and intentional absence of H.

From the parent repository:

```powershell
python tools/NormalizePortraitOrientations.py
python -m unittest discover -s tools/tests -p 'test_portrait*.py'
```

`python tools/NormalizePortraitOrientations.py --apply` replays only the explicit 256 reviewed flips against their exact original hashes. It never guesses orientation, requires the full batch, preflights every correction before writing, and is idempotent. Future artwork or correction-batch changes require updating the reviewed manifests and corresponding expectations.
