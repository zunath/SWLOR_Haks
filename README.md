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

If you have any questions or issues please contact us on the Discord.
