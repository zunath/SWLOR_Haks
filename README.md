Website: https://starwarsnwn.com

Discord: https://discord.gg/MyQAM6m

Forums: https://forums.starwarsnwn.com

This repository holds all of the hakpak content used on the Star Wars: Legends of the Old Republic server for Neverwinter Nights. 

These files can be packed into hakpaks by running the "CompileSWLOR.exe" program. Simply run this, wait for it to finish and then find the packed files in the output folder.

## Tint-map asset generator

Install the generator's pinned Python dependency before running it:

```powershell
python -m pip install -r requirements.txt
```

Validate the generated tint-map assets with:

```powershell
python tools/GenerateTintMapAssets.py --check
```

Normal-body robe RGB models are generated after tint materials and source models
are finalized:

```powershell
python tools/GenerateRobeRgbModels.py --game-data "<NWN install>/data" --apply
python tools/GenerateRobeRgbModels.py --check
```

When correcting an existing animation without changing the rig, pass
`--update-animation <internal-name>` to generation; repeat for each intentionally
changed clip. This reuses the existing shared banks only after proving that the
compiled skeleton and all unselected animations are byte-identical. Changes to
the skeleton, other clips, or an ambiguous prior family are rejected. Structural
changes should use the ordinary versioned generation workflow above.

The generator audits authored geometry, skin bindings, and compiler round trips
before updating `sw_pt_root`, `sw_pt_robe`, `roberender.2da`, and `phenotype.2da`.
Build and deploy all three affected HAKs together. Keep `RobeRgb_` phenotype rows
reserved permanently: saved creatures can refer to these native byte-sized IDs.
The hash manifest in `tools/RobeRgbModels.json` detects changed source models and
stale generated outputs; the regular tint audit also runs this check.

## Speeder rider models

A mounted rider uses a riding phenotype: 22 for normal bodies and 24 for large
bodies. Body parts fall back to the base body through `DefaultPhenoType`, but a
robe animates through its own supermodel, so every robe needs a riding copy or it
keeps standing and running while the body sits. `tools/GenerateSpeederRiderModels.py`
generates the seated animation banks (`sw_cr_creature/*_rd.mdl`), a body root for
every race and gender (`sw_pt_root/p??22.mdl`, `p??24.mdl`) and the robe copies
(`sw_pt_robe/p??22_robeNNN.mdl`, `p??24_robeNNN.mdl`). The pose lives in
`tools/SpeederRiderPose.json` as bone rotations only. The bike is a tail model,
and the tail attachment cannot be keyed, so each riding root's bind pose cancels
the seated pelvis tilt to keep the bike level.

Run this after adding or changing any robe, body root, or base animation bank:

```
python -B tools/GenerateSpeederRiderModels.py --game-data "<NWN>/data" --apply
python -B tools/GenerateTintMapAssets.py --import-stock-models --game-data "<NWN>/data"
python -B tools/GenerateRobeRgbModels.py --apply --game-data "<NWN>/data"
python -B -m unittest discover -s tools -p "TestSpeederRiderModels.py"
```

The second command adds `tintmap.2da` rows for the new robe names and binds tint
materials on robes copied from the installed game; the third refreshes the RGB
robe manifest, which records `tintmap.2da` and `phenotype.2da` as inputs. Rebuild
`sw_cr_creature`, `sw_pt_root`, `sw_pt_robe` and `sw_2da` together. An exact RGB
robe override is not rendered while mounted: generated RGB phenotypes exist for
unmounted bodies only, so the robe shows its palette colors until the rider dismounts.

If you have any questions or issues please contact us on the Discord.
