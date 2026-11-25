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

The portrait companion is a searchable gallery page that shows every portrait in `sw_portrait` and `portraits.2da`. It opens in any browser and requires no server running.

### Build it locally

Install `requirements.txt` as in the tint-map section, then run:

```powershell
python tools/GeneratePortraitViewer.py
```

Open `portrait-viewer/index.html` in a browser. On NixOS, run the build with `nix-shell -p 'python3.withPackages (p: [ p.pillow ])' --run 'python tools/GeneratePortraitViewer.py'`.

After you change the generator, run its test:

```powershell
python -m unittest discover -s tools -p TestGeneratePortraitViewer.py
```

The build writes `portrait-viewer/catalogue.js` and the `.webp` files in `portrait-viewer/images/`. Git ignores both. Don't edit them. The build deletes any `.webp` there that it didn't make, so keep other images out of that folder.

### What the filters mean

- Race comes from `racialtypes.2da`.
- Sex shows the `gender.2da` name with its `portraits.2da` value, such as "Male (0)". A value with no `gender.2da` row shows as "Value 5".
- "Unspecified" means the table has no value. The page never guesses from file names or artwork.
- "SWLOR artwork" lists portraits from `sw_portrait`.
- "Base game" lists table rows that use a portrait from the unmodded game. A HAK portrait with the same ResRef replaces it.
- "Missing artwork" lists table rows with no image in either place.
- An image with no table row shows "Not listed in portraits.2da".
- A ResRef the table lists twice gets two cards, each with its table ID.

### Add or remove a portrait

Each build reads the current files, so a new portrait shows up on the next build. To remove a portrait, delete its `po_*.dds` files and its `portraits.2da` row. If you delete only the images, the row moves to "Missing artwork" until the next base-game refresh. If you delete only the row, the images stay as unlisted.

### Refresh the base-game portraits

The base-game previews are committed in `portrait-viewer/stock/`. No hak build reads that folder. A normal build only reads it, so refresh it only after a game update adds portraits. Point `--stock` at a folder of game resources extracted from the game's archives:

```powershell
python tools/GeneratePortraitViewer.py --stock <extracted game resources>
```

The refresh keeps one image for each `portraits.2da` row that has no HAK portrait. It uses the `_h` size, else `_m`. It deletes every other `.webp` in that folder.

### Download a build

GitHub builds the companion when a push to a branch changes any of these:

- `sw_portrait`
- `portraits.2da`, `racialtypes.2da` or `gender.2da`
- `portrait-viewer`
- `requirements.txt`
- the generator, its test or the `Portrait companion` workflow

Other pushes and tags build nothing. To build a branch anyway, open the workflow in the Actions tab and press Run workflow.

1. Open the Actions tab, then the `Portrait companion` workflow.
2. Open a green run and download `portrait-companion-<commit>` from its Artifacts list.
3. Extract the zip and open `index.html`.

A downloaded copy never updates. Artifacts expire after 14 days. Anyone signed in to GitHub who can read the repository can download them.

### Publish it on GitHub Pages

Publishing is off until a maintainer turns it on:

1. In Settings → Pages, set Source to "GitHub Actions".
2. In Settings → Secrets and variables → Actions → Variables, add `PORTRAIT_PAGES` with the value `true`.
3. In the Actions tab, run the `Portrait companion` workflow on `master`.

After that, each `master` build publishes the companion to `https://<owner>.github.io/SWLOR_Haks/`. Other branches still build artifacts only. Anyone can open the published site. To stop publishing, delete the variable.
