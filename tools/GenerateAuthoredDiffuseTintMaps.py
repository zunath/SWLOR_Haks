"""Register RGB controls for authored rasters whose old PLT atlases do not match.

The original diffuse supplies full-resolution detail and default colors. A
uniform selector supplies the one meaningful RGB channel, never the stale PLT.
"""
import hashlib

import numpy as np
import GenerateTintMapAssets as g


SOURCES = {
    "helm_101": ("sw_pt_helm/helm_101.dds", 4),
    "pfh0_neck012": ("sw_pt_neck/pfh0_neck012.dds", 0),
    "pmh0_neck012": ("sw_pt_neck/pmh0_neck012.dds", 0),
    "pfh0_shinl264": ("sw_pt_lshin/pfh0_shinL264.dds", 4),
}


def generate():
    entries = g.load_source_manifest()
    preserved_order = list(entries)
    for name, (relative, layer_id) in SOURCES.items():
        source = g.REPOSITORY_ROOT / relative
        if g.authored_material_sources()[name].get("implicitTexture0") != name:
            raise ValueError("Expected original inherited diffuse: " + name)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        # Include the selector in its identity: different controls must not share
        # a packed map merely because their original rasters are identical.
        identity = hashlib.sha256(bytes.fromhex(source_hash) + bytes([layer_id])).hexdigest()
        texture = g.tint_texture_resref(identity, 4, 4)
        output = g.tint_directory(texture) / (texture + ".dds")
        output.parent.mkdir(exist_ok=True)
        shade = np.full((4, 4), 128, dtype=np.uint8)
        layers = np.full((4, 4), layer_id, dtype=np.uint8)
        g.write_packed_dds(output, 4, 4, shade, layers)
        source_name = g.AUTHORED_DIFFUSE_SOURCES[name]
        entries[source_name] = dict(
            model=source_name, material=source_name,
            layers=[layer_id], width=4, height=4, source=relative,
            sourceSha256=identity, authoredDiffuse=name, diffuseSha256=source_hash,
            shadeSha256=hashlib.sha256(g.decode_dds_shades(output, 4, 4).tobytes()).hexdigest(),
            layerSha256=hashlib.sha256(layers.tobytes()).hexdigest(),
            texture=texture, output=output.relative_to(g.REPOSITORY_ROOT).as_posix(),
        )
    g.write_source_manifest(entries, preserved_order)
    # Seed declarations for the new sources; the binding refresh then applies
    # scoped profiles and native attachment defaults.
    g._AUTHORED_DIFFUSE_TEXTURES = None
    for source_name in g.AUTHORED_DIFFUSE_SOURCES.values():
        entry = entries[source_name]
        g.update_mtr(g.mtr_path(source_name), source_name, entry["texture"], 4, 4)
    print("Registered four authored diffuse RGB controls; original artwork is unchanged.")


def validate(entries):
    errors = []
    for name, (relative, layer) in SOURCES.items():
        source_name = g.AUTHORED_DIFFUSE_SOURCES[name]
        entry = entries.get(source_name, {})
        digest = hashlib.sha256((g.REPOSITORY_ROOT / relative).read_bytes()).hexdigest()
        identity = hashlib.sha256(bytes.fromhex(digest) + bytes([layer])).hexdigest()
        expected = dict(authoredDiffuse=name, diffuseSha256=digest,
                        sourceSha256=identity, source=relative, layers=[layer], width=4, height=4)
        if any(entry.get(key) != value for key, value in expected.items()):
            errors.append(f"{source_name}: authored diffuse control or original artwork changed; regenerate")
    return errors


if __name__ == "__main__":
    generate()
