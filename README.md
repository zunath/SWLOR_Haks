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

The generator audits authored geometry, skin bindings, and compiler round trips
before updating `sw_pt_root`, `sw_pt_robe`, `roberender.2da`, and `phenotype.2da`.
Build and deploy all three affected HAKs together. Keep `RobeRgb_` phenotype rows
reserved permanently: saved creatures can refer to these native byte-sized IDs.
The hash manifest in `tools/RobeRgbModels.json` detects changed source models and
stale generated outputs; the regular tint audit also runs this check.

If you have any questions or issues please contact us on the Discord.
