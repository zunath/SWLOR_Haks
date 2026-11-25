Website: https://starwarsnwn.com

Discord: https://discord.gg/MyQAM6m

Forums: https://forums.starwarsnwn.com

This repository holds all of the hakpak content used on the Star Wars: Legends of the Old Republic server for Neverwinter Nights. 

These files can be packed into hakpaks by running the "CompileSWLOR.exe" program. Simply run this, wait for it to finish and then find the packed files in the output folder.

## Tint-map asset generator

Install the generators' pinned Python dependencies before running them:

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
rebuilds every dependent wearer). An `--update-animation` run preserves every root
listed in the manifest's `native_wearer_sources` automatically and rejects an explicit
list that differs from it. The generator requires prior ownership and unchanged garment
hashes, preserves native mesh/normal/skin bytes, and rebinds only parent/header/part
IDs. Their captured native source still passes all strict geometry and motion
audits. The manifest records the selection and source hashes. Reusing `--stage`
resumes other compilations only when their actual compiler/input fingerprints match;
it never substitutes for current validation. Round-trip validation ignores only the
faces CleanModels drops because their corners coincide after it welds identical
vertices; such faces cover no area. A wearer whose shared family gains garment
spacers can no longer be preserved and compiles fresh under that rule.

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

## Portrait companion

The portrait companion is a searchable page of every portrait in `sw_portrait` and `portraits.2da`. It opens in any browser, with no server.

### Build it locally

Install `requirements.txt` as in the tint-map section. Then build the companion:

```powershell
python tools/GeneratePortraitViewer.py
```

Open `portrait-viewer/index.html` in a browser. On NixOS, build it with `nix-shell -p 'python3.withPackages (p: [ p.pillow ])' --run 'python tools/GeneratePortraitViewer.py'`.

Run the test after you change the generator:

```powershell
python -m unittest discover -s tools -p TestGeneratePortraitViewer.py
```

The generator owns `portrait-viewer/catalogue.js` and every `.webp` in `portrait-viewer/images/`. Git ignores both. Never edit them or store other images there.

### What the filters mean

- Race labels come from `racialtypes.2da`.
- Sex shows the raw value from `portraits.2da`.
- "Unspecified" means the table records no value. Nothing is guessed from file names or artwork.
- "Missing artwork" lists table rows with no image in `sw_portrait`. Many of them are base-game portraits.
- An image with no table row shows "Not listed in portraits.2da".

### Add or remove a portrait

Each build reads the current files, so a new portrait appears on the next build. To remove a portrait completely, delete its `po_*.dds` files and its `portraits.2da` row. Deleting only the images moves the row to "Missing artwork". Deleting only the row keeps the images, unlisted.

### Download a build

The `Portrait companion` workflow builds the companion on every push to any branch or tag. A push of several commits builds only the last one.

1. Open the Actions tab, then the `Portrait companion` workflow.
2. Open a green run and download `portrait-companion-<commit>` from its Artifacts list.
3. Extract the zip and open `index.html`.

A downloaded copy never updates. Artifacts expire after 14 days. Anyone signed in to GitHub who can read the repository can download them.

### Publish it on GitHub Pages

Publishing is off. A maintainer turns it on once:

1. In Settings → Pages, set Source to "GitHub Actions".
2. In Settings → Secrets and variables → Actions → Variables, add `PORTRAIT_PAGES` with the value `true`.
3. Run the `Portrait companion` workflow on `master` from the Actions tab.

From then on, each push to `master` publishes the companion to `https://<owner>.github.io/SWLOR_Haks/`. Other branches still build artifacts only. A Pages site is public, even for a private repository, unless the account is on GitHub Enterprise Cloud. To stop publishing, delete the variable.
