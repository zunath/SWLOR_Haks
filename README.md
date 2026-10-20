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

When unchanged native wearers contain geometry that the compiler cannot retain
exactly, select each explicitly with `--preserve-native-wearer <root>` during a
complete generation, including `--update-animation` runs (an animation update
rebuilds every dependent wearer). Pass every root listed in the manifest's
`native_wearer_sources`. The generator requires prior ownership and unchanged garment
hashes, preserves native mesh/normal/skin bytes, and rebinds only parent/header/part
IDs. Their captured native source still passes all strict geometry and motion
audits. The manifest records the selection and source hashes. Reusing `--stage`
resumes other compilations only when their actual compiler/input fingerprints match;
it never substitutes for current validation.

The generator audits authored geometry, skin bindings, and compiler round trips
before updating `sw_pt_root`, `sw_pt_robe`, `roberender.2da`, and `phenotype.2da`.
Build and deploy all three affected HAKs together. Keep `RobeRgb_` phenotype rows
reserved permanently: saved creatures can refer to these native byte-sized IDs.
The hash manifest in `tools/RobeRgbModels.json` detects changed source models and
stale generated outputs; the regular tint audit also runs this check.

If you have any questions or issues please contact us on the Discord.

## Model compiler

Use CleanModels CLI v4.4.2 bundled with [cleanmodels-qt v1.1.7](https://github.com/plenarius/cleanmodels-qt/releases/tag/v1.1.7).
Install the pinned Windows release (archive and executable hashes are checked):

```powershell
powershell -ExecutionPolicy Bypass -File tools/SetupModelCompiler.ps1
python tools/CompileModels.py --model <resref> --game-data "<NWN install>/data"
```

The default install is `%LOCALAPPDATA%/SWLOR/tools/cleanmodels-qt/1.1.7/cleanmodels-qt/cleanmodels.exe`.
Set `CLEANMODELS` for another install of the same CLI. The shared generator
adapter runs CleanModels directly, supplies staged dependencies/materials and
custom body skeletons, and validates binary and round-trip data before `--apply`.
It does not invoke the old compiler or the game executable. Compiler hashes are
part of generation caches, so changing compilers requires revalidation.
The adapter spells out omitted static identity rotations in temporary inputs
to avoid non-unit default quaternions from v4.4.2. Authored rotations and source
files remain intact. Animation banks accept both compiler layouts and relocate
stored parent offsets during split/join and controller stripping.
Existing binary assets are not rebuilt by installing the tool. Their manifests
keep original build-tool fingerprints and compiler hashes; separate
`validation_tool_files` / `validation_implementation` entries record the current
Python audit tools. These entries cannot override model or texture hashes, and
new generation still invalidates caches using the actual compiler/tool hashes.
`validation_compiler_sha256` records the CLI used to audit and repack historical
banks without replacing their original build compiler hash. Repack preflight
uses the restricted current-tool overlay and keeps asset hashes authoritative.
Cross-compiler motion revisions map candidate part IDs to the unchanged original
rig, compare hierarchy/controller/event values, then copy only selected clips;
original headers, geometry, and unselected native components remain intact.
Resumed compiles refresh staged materials from the active HAK resources.
New build manifests and cache/checkpoint keys record material contents and absent
material lookups, so material-only edits or newly added MTRs invalidate reuse.
