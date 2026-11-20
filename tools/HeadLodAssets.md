# Authored player-head LODs

Keep the approved close MDL and add a same-named LOD definition, for example:

```text
pfh0_head258
3.0 cf258_l1
6.0 cf258_l2
```

All three entries have native compiled MDLs. This is separate from the shared
resource redirects managed by `ModelLodAssets.json`: an authored head retains its
own close MDL, including when the client disables LODs. Never replace that MDL
with a redirect or regenerate its geometry during reduction.

Register each set in `HeadLodAssets.json`, including the approved close checksum,
two strictly increasing switch distances, compiled checksums, and triangle counts.
Caps include every mesh: 30,000 close, 10,000 at LOD 1, and 3,000 at LOD 2.
Keep material and texture bindings consistent across levels. Explicitly listed
separate eyelash materials may be omitted from both reduced levels; that exception
does not allow removing skin, eyes, eyebrows, or other material bindings.
An eyelash node sharing a retained material can also be omitted; record its node
name in `omittedEyelashNodes`. Embedded lashes require a documented component
audit. Keep all close-model eyelashes unchanged.

The normal model inventory recognizes registered native head sets without treating
their definitions as shared redirects. Normal validation also checks the head
manifest. Run these checks in a complete HAK checkout:

```powershell
python -B tools/HeadLodAssets.py
python -B tools/ModelLodAssets.py --check
python -B -m unittest discover -s tools/tests -p test_head_lod_assets.py
```

These checks establish file identity, budgets and bindings, not visual quality or
runtime color replay. Use the repository's NWN visual verification procedure for
each reduced level, inspect features and silhouettes, and check palette/RGB colors
while switching actual LODs. Matching material names alone does not establish that
an inactive LOD has received the latest color values.
