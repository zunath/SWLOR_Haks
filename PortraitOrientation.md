# Portrait orientation corrections

Audited all 9,136 portrait TGAs across 2,184 case-insensitive families, including every 1,027 Huge and 1,694 Large image. Huge is the orientation reference when present; otherwise Large is used. The 490 families without either retain their largest available image (Medium, or the sole available size) as reference.

Corrected 256 images across 200 families with exact horizontal pixel reflections: 1 Large, 157 Medium, 60 Small, and 38 Tiny. Huge images remain authoritative and unchanged. The character-selection example is `po_mm_f_27`: H/L already agreed, while M/S were reversed. Those M/S images now match H/L/T.

The audit compared all 6,952 non-reference images using resized pixel comparisons and geometric feature matching, with verified sibling sizes bridging different crops. All 222 initial flip candidates and 545 uncertain pairs were visually reviewed. Manual review added 35 corrections. Post-edit inspection of every changed image caught and removed one automatic false positive (`po_martian1953_s`); its original crop already agrees with H/L/M/T. The tiny blue portal (`po_plc_bluprtl_t`) was verified by its sloped upper edge despite weak conflicting feature matches.

Different artwork is preserved. `po_rodF9` L/M/S/T and `po_bisonant` S/T were flipped to match canonical facing. Different frontal artwork, already aligned images, and symmetric images without an observable orientation mismatch were retained. No portraits were resized, regenerated, renamed, added, or removed.

## Verification

- All 256 changed files decode to exact horizontal reflections, including alpha and texture padding.
- The other 8,880 TGA files are byte-for-byte unchanged.
- TGA headers, image IDs, dimensions, bit depths, and origin flags are preserved. Legacy compressed images retain RLE encoding; extension metadata is preserved with its absolute footer pointer relocated when recompression changes the payload length.
- Eight focused standard-library tests cover uncompressed/RLE images, scanline-crossing legacy packets, alpha/origin preservation, exact double reflections, malformed input, and metadata relocation.
- `sw_portrait.hak` was rebuilt, and every one of its 9,136 resources was byte-compared with the corrected sources.
- No live-game deployment or in-game rendering check was performed.

`portrait_orientation_corrections.csv` records the reviewed corrections, canonical references, original hashes, and corrected hashes. `portrait_orientation_review.csv` records the 545 uncertain-pair decisions. The correction tool validates all inputs before applying the explicit manifest and is idempotent; it does not guess orientation.

From the parent repository:

```powershell
python tools/NormalizePortraitOrientations.py
python -m unittest discover -s tools/tests -p test_portrait_tga.py
```

Use `python tools/NormalizePortraitOrientations.py --apply` only to reapply the recorded fixes to their exact original images. Future artwork changes require reviewing and updating the manifest; unexpected content fails verification.
