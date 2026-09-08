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

## Sound compression

Selected `sw_sound/*.wav` resources contain MP3 audio with a `BMU V1.0` wrapper.
Keep their WAV filenames and resource types so existing sound lookups continue
to work. This encoding was already used by 106 WAV resources in this repository.
The initial conversion changes 843 PCM effects and preserves 258 WAV resources,
including existing compressed audio, known game-configured loops,
cue/sampler/ACID metadata, and malformed containers. Music is unchanged.

The WAV collection falls from 129,209,674 to 58,401,578 bytes, saving
**70,808,096 bytes (67.53 MiB; 54.80%)**. These are resource/HAK payload savings;
Git history and actual NWSync transfer sizes differ. Encoding preserves channels,
uses 96 kbps for 801 effects and 64 kbps for 42 low-rate effects, and resamples
five nonstandard source rates to 22,050 Hz.

`tools/SoundCompressionManifest.json` records original Git provenance and deployed
hashes. Keep every conversion manifest: restore lossless originals from each
manifest's source commit, never by decoding an MP3 into a new master. If rollback
fails, the converter retains its prepared recovery manifest and reports its path
and source commit; recover the affected files before retrying.

Run the following commands from this HAK repository. `--repo-root` identifies the
parent SWLOR_NWN checkout containing `Module`; use its actual path if these
repositories are checked out separately. Generate fresh exclusions from current
placed sounds, blueprints, and looping 2DA fields before every conversion:

```powershell
$soundExclusionsPath = Join-Path $env:TEMP "swlor-sound-exclusions-$([guid]::NewGuid()).json"
python tools/AuditSoundCompression.py --repo-root .. --write-exclusions $soundExclusionsPath
if ($LASTEXITCODE -ne 0) { throw "Sound loop audit failed." }
python tools/CompressSoundResources.py --ffmpeg "<path-to-ffmpeg>" --exclude-manifest $soundExclusionsPath
if ($LASTEXITCODE -ne 0) { throw "Sound conversion dry run failed." }
```

Supply an FFmpeg build with `libmp3lame` support. The command above measures and
validates candidate outputs without replacing sources. To apply the conversion,
use a new manifest path; the converter refuses to overwrite a previous manifest:

```powershell
$soundManifestPath = "tools/SoundCompressionManifest-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').json"
python tools/CompressSoundResources.py --ffmpeg "<path-to-ffmpeg>" --exclude-manifest $soundExclusionsPath --manifest $soundManifestPath --apply
if ($LASTEXITCODE -ne 0) { throw "Sound conversion failed; inspect recovery diagnostics." }
Remove-Item -LiteralPath $soundExclusionsPath
```

The converter stages and decodes outputs, verifies recoverable Git originals,
and checks the entire WAV inventory before replacement and completion. It never
re-encodes existing compressed resources. Audit **every historical manifest**
against current loop references, since later manifests record previously
compressed resources as skipped:

```powershell
Get-ChildItem tools/SoundCompressionManifest*.json | ForEach-Object {
    python tools/AuditSoundCompression.py --repo-root .. --check $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "A compressed sound is configured to loop." }
}
python tools/TestSoundCompressionAudit.py
python tools/TestSoundCompression.py --ffmpeg "<path-to-ffmpeg>"
python tools/CompressSoundResources.py --ffmpeg "<path-to-ffmpeg>" --verify-manifest $soundManifestPath
```

Verify the complete inventory and decode against the newest complete manifest.
To verify the currently committed batch without converting anything, first set
`$soundManifestPath = "tools/SoundCompressionManifest.json"`. A historical loop
audit accepts a restored sound only if it matches the recorded original hash.
Commit restorations and record a new complete inventory with current exclusions,
retaining older manifests. Very short wrapped clips require the verifier's
explicit MP3 demuxer; generic file sniffing can misidentify them.

Rebuild and distribute `sw_sound.hak` through the normal HAK/NWSync workflow after
updating the parent repository's submodule pointer. Resource names are unchanged,
so this conversion needs no module resource edits. Keep manifests and backups out
of the sound HAK. Archive validation preserved all 1,159 resource identities and
confirmed the same 70,808,096-byte saving in the packed HAK.

Byte and decode checks do not replace listening in NWN. Before live deployment,
check positional effects, voices, short impacts, and ambient transitions. MP3 is
lossy and adds encoder delay/end padding; restore newly discovered looping or
timing-sensitive effects from their recorded originals and exclude them from
future conversion.

If you have any questions or issues please contact us on the Discord.
