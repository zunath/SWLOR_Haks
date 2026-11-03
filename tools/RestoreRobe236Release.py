"""Restore robe 236 (Long Duster) to the models released with the Combat Upgrade.

The Combat Upgrade (SWLOR_NWN #2277) shipped SWLOR_Haks 8a1ab83c7f. Its robe 236
is the original 2019 model, recompiled with only the tint material binding.
Later repairs made the coat panel rigid (#432), skinned the coat panel, hood and
belt to the torso (#455) and re-weighted the generated skirts (#539, #549).
Players found the result worse than the released robe, so the native models
return to their released bytes, with three corrections that stay:

- Animation part IDs follow the current coat animation parents. Released IDs no
  longer match them, so the coat flaps would stop animating (the October 4
  inheritance fix).
- The female Ewok/Ugnaught (pfd0) robe keeps its own sit and sitdown clips as
  aligned to the body chain on October 4; its released geometry is recompiled
  with them. Released clips would fork the shared female-dwarf animation bridge.
- The male Ewok/Ugnaught (pmd0) right sleeve keeps the forearm cuff weights
  from SWLOR_Haks #526, which keep the cuff on the wrist in bent-elbow poses.

Mounted (phenotype 22) copies have no animation parents in the HAKs and return
to their released bytes unchanged. The tint material and dye layers are not
touched. Regenerate the RGB body roots afterwards with GenerateRobeRgbModels.py.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import struct
import subprocess
import tempfile
from pathlib import Path

import CleanModels
import CompileModels as mdl
import GenerateTintMapAssets as tint
import RobeAnimations as anim
import RobePoseAudit as poses

ROOT = Path(__file__).resolve().parents[1]
ROBE = 236
RELEASE_COMMIT = "8a1ab83c7f842fefbc7ee388ac58af81713351d0"
# Git blob IDs of the released models, pinned so the recipe fails on any other source.
RELEASE_BLOBS = {
    "pfd0_robe236": "c817e032f8f20121f02ee2b559f3f713c0deb989",
    "pfd22_robe236": "bc2a2dd1c02aee94cb95202dfee20277086ac27e",
    "pfe0_robe236": "ad236b3b89a76765375402b7e0697d70679c0a28",
    "pfe22_robe236": "86d14f074638dac766b91d37aaa69ca54e08636c",
    "pfh0_robe236": "45b1f9dae23fdc24c06bd14a854353241dabf7c2",
    "pfh22_robe236": "8fad0af673523888f90ebd69ffd991ca7227bc8f",
    "pfo0_robe236": "6656f5c3d655a23c6cf9d9c6870c9ee6ae2eca9c",
    "pfo22_robe236": "319a763bb214c09ece1d71428016168d793c0485",
    "pmd0_robe236": "2d65efd699d4085906895e6e344bc6c355c1c6fe",
    "pmd22_robe236": "aff70e5b2040a787fa8af9b2fb466dd2eaacc213",
    "pme0_robe236": "ff6a289db8222b9bd2958a88321d35347cc71792",
    "pme22_robe236": "164d62d5f5425323c8631e6503df9770ad736a9a",
    "pmh0_robe236": "e1a6eadd681bfe9ffbe7079dd4d7df22a06c0227",
    "pmh22_robe236": "22362ec89593e70b68b06b9292d160bff12f2372",
    "pmo0_robe236": "6d53ada680652fd1866a78d9d5f7956b77e92bc4",
    "pmo22_robe236": "b1f04e3ef0e3e3a770181ae4c7b2b9b9fd4f8fbf",
}
# pmd0_robe236 as repaired by SWLOR_Haks #526; only its right sleeve's skin rows are used.
CUFF_DONOR_BLOB = "263f5940d86e8f7bf0a856130fbdf5233adc0e23"
CUFF_MODEL, CUFF_MESH = "pmd0_robe236", "arm_r"
# pfd0_robe236 with its sit/sitdown clips aligned to the body chain (October 4); only its clips are used.
ALIGNED_CLIPS_BLOB = "4a39bea0e56f5d5ee8e5d1144f89473a81a8cdca"
SPLICED_MODEL = "pfd0_robe236"


def git_blob(blob: str) -> bytes:
    data = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob", blob],
                          capture_output=True, check=True).stdout
    if hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != blob:
        raise ValueError(f"Git blob {blob} does not match its pinned ID")
    return data


def uint(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def node_offsets(data: bytes, animations: bool = True) -> list[int]:
    """Geometry (and optionally animation) node offsets in traversal order."""
    offsets = []

    def visit(pointer):
        offset = 12 + pointer
        offsets.append(offset)
        for index in range(uint(data, offset + 76)):
            visit(uint(data, 12 + uint(data, offset + 72) + index * 4))
    visit(uint(data, 84))
    for index in range(uint(data, 136) if animations else 0):
        visit(uint(data, 12 + uint(data, 12 + uint(data, 132) + index * 4) + 72))
    return offsets


def follow_parent_parts(data: bytes, parent: bytes) -> bytes:
    """Give each node its animation parent's part ID, everywhere it occurs.

    A node the parent does not animate gets -1, as when the compiler numbers a
    model against that parent; its own in-model tracks are then unused.
    """
    expected = anim.binary_parts(parent)
    remap = {}
    for path, part in anim.binary_parts(data).items():
        if part < 0:
            continue
        target = expected.get(path, -1)
        if remap.setdefault(part, target) != target:
            raise ValueError(f"{'/'.join(path)}: ambiguous part mapping")
    numbered = [target for target in remap.values() if target >= 0]
    if len(set(numbered)) != len(numbered):
        raise ValueError("Part mapping merges distinct nodes")
    result = bytearray(data)
    for offset in node_offsets(data):
        part = struct.unpack_from("<i", data, offset + 28)[0]
        if part >= 0:
            struct.pack_into("<i", result, offset + 28, remap[part])
    return bytes(result)


def skin_rows(data: bytes, name: str):
    """Return (weights offset, indices offset, stride, count, palette, maps) for a named skin."""
    for offset in node_offsets(data, animations=False):
        if data[offset + 32:offset + 64].split(b"\0", 1)[0].decode("ascii").lower() != name:
            continue
        flags = uint(data, offset + 108)
        if not flags & 0x40:
            raise ValueError(f"{name}: expected a skin mesh")
        mesh = offset + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
        skin, raw = mesh + 512, 12 + uint(data, 4)
        count, stride = struct.unpack_from("<H", data, mesh + 448)[0], uint(data, mesh + 440)
        palette = struct.unpack_from("<17h", data, skin + 64)
        maps = data[12 + uint(data, skin + 20):12 + uint(data, skin + 20) + 4 * uint(data, skin + 24)]
        # Inverse bind rotations are quaternions; inverse bind translations are vectors.
        binds = [data[12 + uint(data, skin + field):12 + uint(data, skin + field) + size * uint(data, skin + field + 4)]
                 for field, size in ((28, 16), (40, 12))]
        return raw + uint(data, skin + 12), raw + uint(data, skin + 16), stride, count, palette, maps, binds
    raise ValueError(f"{name}: skin not found")


def keep_repaired_cuff(data: bytes, donor: bytes) -> bytes:
    """Copy only the repaired sleeve's weight and bone-slot rows onto the released mesh."""
    meshes = {name: props for name, _, props in mdl.binary_nodes(data) if props}
    repaired = {name: props for name, _, props in mdl.binary_nodes(donor) if props}
    released, fixed = meshes[CUFF_MESH], repaired[CUFF_MESH]
    for field in ("faces", "tverts"):
        if released[field] != fixed[field]:
            raise ValueError(f"{CUFF_MESH}: released and repaired sleeves differ in {field}")
    # The repaired file was recompiled, which moved seven positions by float32 rounding (< 1e-7 m).
    for field in ("verts", "normals"):
        if len(released[field]) != len(fixed[field]) or any(
                abs(a - b) > 1e-6 for left, right in zip(released[field], fixed[field]) for a, b in zip(left, right)):
            raise ValueError(f"{CUFF_MESH}: released and repaired sleeves differ in {field}")
    weights, indices, stride, count, palette, maps, binds = skin_rows(data, CUFF_MESH)
    d_weights, d_indices, d_stride, d_count, d_palette, d_maps, d_binds = skin_rows(donor, CUFF_MESH)
    if (stride, count, palette, maps, binds) != (d_stride, d_count, d_palette, d_maps, d_binds):
        raise ValueError(f"{CUFF_MESH}: released and repaired sleeves use different skeleton bindings")
    result = bytearray(data)
    for vertex in range(count):
        for target, source, size in ((weights, d_weights, 16), (indices, d_indices, 8)):
            start, origin = target + vertex * (stride or size), source + vertex * (stride or size)
            result[start:start + size] = donor[origin:origin + size]
    result = bytes(result)
    restored = {name: props for name, _, props in mdl.binary_nodes(result) if props}
    if restored[CUFF_MESH]["weights"] != repaired[CUFF_MESH]["weights"]:
        raise ValueError(f"{CUFF_MESH}: repaired cuff weights were not reproduced")
    return result


def clip_nodes(data: bytes) -> list[list[tuple[str, int]]]:
    """(name, offset) of every node in each in-model clip, in traversal order."""
    clips = []
    for index in range(uint(data, 136)):
        header = 12 + uint(data, 12 + uint(data, 132) + index * 4)
        nodes = []

        def visit(pointer):
            offset = 12 + pointer
            nodes.append((data[offset + 32:offset + 64].split(b"\0", 1)[0].decode("ascii").lower(), offset))
            for child in range(uint(data, offset + 76)):
                visit(uint(data, 12 + uint(data, offset + 72) + child * 4))
        visit(uint(data, header + 72))
        clips.append(nodes)
    return clips


def copy_clip_values(compiled: bytes, aligned: bytes) -> bytes:
    """Use the aligned model's exact controller floats.

    Decompiling and recompiling a clip moves its values by float32 rounding,
    which is enough to version the shared animation bridge. The compiler emits
    the same controller layout, so only the value arrays are copied.
    """
    result = bytearray(compiled)
    for target, source in zip(clip_nodes(compiled), clip_nodes(aligned), strict=True):
        if [name for name, _ in target] != [name for name, _ in source]:
            raise ValueError("Recompiled clips list different nodes")
        for (name, offset), (_, origin) in zip(target, source):
            keys = [(12 + uint(model, at + 84), uint(model, at + 88)) for model, at in ((compiled, offset), (aligned, origin))]
            values = [(12 + uint(model, at + 96), uint(model, at + 100)) for model, at in ((compiled, offset), (aligned, origin))]
            (key, key_count), (old_key, old_key_count) = keys
            (value, value_count), (old_value, old_value_count) = values
            if (key_count, value_count) != (old_key_count, old_value_count) or \
                    compiled[key:key + 12 * key_count] != aligned[old_key:old_key + 12 * key_count]:
                raise ValueError(f"{name}: recompiled clip controllers differ in layout")
            result[value:value + 4 * value_count] = aligned[old_value:old_value + 4 * value_count]
    return bytes(result)


def keep_aligned_clips(data: bytes, aligned: bytes) -> bytes:
    """Rebuild a released model with the in-model clips of its aligned successor.

    AlignRobeAnimations.py re-timed the female Ewok/Ugnaught robe's own sit and
    sitdown clips to the current body chain. Released clips would fork the shared
    female-dwarf animation bridge for every robe on that body, so the released
    geometry is compiled with the aligned clips and keeps its released skin binds.
    """
    with tempfile.TemporaryDirectory(prefix="swlor-robe236-") as directory:
        stage = Path(directory)
        compiler, _ = mdl.prepare_compiler(stage)
        texts = {}
        for label, model in (("released", data), ("aligned", aligned)):
            folder = stage / label
            folder.mkdir()
            (folder / f"{SPLICED_MODEL}.mdl").write_bytes(model)
            mdl.run_compiler(compiler, stage, ["-de", str(folder / f"{SPLICED_MODEL}.mdl"),
                                               str(stage / f"{label}_text") + "/"], f"{label}.log")
            texts[label] = poses.accurate_rotations(
                (stage / f"{label}_text" / f"{SPLICED_MODEL}.mdl").read_text(encoding="latin1"), model)
        geometry = re.search(r"(?is)\A.*?^\s*endmodelgeom\b[^\n]*\n", texts["released"], re.M)
        clips = re.search(r"(?ims)^\s*newanim\b.*\Z", texts["aligned"])
        if not geometry or not clips or re.search(r"(?im)^\s*newanim\b", texts["released"][geometry.end():]) is None:
            raise ValueError(f"{SPLICED_MODEL}: expected geometry followed by in-model clips")
        # Clip nodes repeat their geometry node's type; the aligned clips name the
        # later skinned coverings, so give them the released types.
        kinds = {name: kind for kind, name, _ in mdl.parse_nodes(geometry[0])}
        clip_text = re.sub(r"(?im)^(\s*node\s+)(\S+)(\s+)(\S+)(\s*)$",
                           lambda match: match[1] + kinds.get(match[4].lower(), match[2]) + match[3] + match[4] + match[5],
                           clips[0])
        source = geometry[0] + clip_text
        parent = mdl.supermodel(data)
        # The coat parent lives outside the compiler's resource folders. Compile
        # without it, restore its name, then take every part ID from the parent.
        prepared = re.sub(r"(?im)^(\s*setsupermodel\s+\S+\s+)\S+", r"\1NULL", source, count=1)
        (stage / "input").mkdir()
        (stage / "input" / f"{SPLICED_MODEL}.mdl").write_text(prepared, encoding="latin1")
        # Compile without the tint material files: their NormalTangents hint makes the
        # compiler add tangent rows, which neither shipped native robe carries and the
        # robe generators cannot parse. The material name itself is kept.
        (stage / "binary").mkdir()
        CleanModels.run(compiler, stage, "compile", [stage / "input" / f"{SPLICED_MODEL}.mdl"],
                        stage / "binary", "compile.log")
        CleanModels.validate_binary(stage / "binary" / f"{SPLICED_MODEL}.mdl")
        compiled = bytearray(copy_clip_values((stage / "binary" / f"{SPLICED_MODEL}.mdl").read_bytes(), aligned))
        compiled[180:244] = parent.encode("ascii").ljust(64, b"\0")
        names = {node[0]: node[0] for node in poses.Model(data, False).nodes}
        result = poses.preserve_skin_bindings(data, bytes(compiled), names)
        poses.validate_garment_bindings(data, result, names)
        (stage / "check").mkdir()
        (stage / "check" / f"{SPLICED_MODEL}.mdl").write_bytes(result)
        mdl.run_compiler(compiler, stage, ["-de", str(stage / "check" / f"{SPLICED_MODEL}.mdl"),
                                           str(stage / "decompiled") + "/"], "validate.log")
        mdl.validate_round_trip(source.encode("latin1"), result,
                                (stage / "decompiled" / f"{SPLICED_MODEL}.mdl").read_text(encoding="latin1"))
        return result


def expected_models(active=None) -> dict[Path, bytes]:
    active = active or tint.find_active_models()
    outputs = {}
    for name, blob in RELEASE_BLOBS.items():
        data = git_blob(blob)
        if not mdl.binary(data):
            raise ValueError(f"{name}: released model must be compiled")
        if name[3:].startswith("0_"):
            parent = mdl.supermodel(data)
            if parent not in active:
                raise ValueError(f"{name}: animation parent {parent} is not in the HAKs")
            if name == SPLICED_MODEL:
                data = keep_aligned_clips(data, git_blob(ALIGNED_CLIPS_BLOB))
            data = follow_parent_parts(data, active[parent].read_bytes())
            if name == CUFF_MODEL:
                data = keep_repaired_cuff(data, git_blob(CUFF_DONOR_BLOB))
        outputs[ROOT / "sw_pt_robe" / f"{name}.mdl"] = data
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, data in expected_models().items():
        if args.apply and (not path.is_file() or path.read_bytes() != data):
            path.write_bytes(data)
        if not path.is_file() or path.read_bytes() != data:
            stale.append(path.name)
    if stale:
        raise ValueError(f"Robe 236 differs from its restored release models: {stale}; run with --apply")
    print(f"{'Applied' if args.apply else 'Validated'} {len(RELEASE_BLOBS)} released robe {ROBE} models")


if __name__ == "__main__":
    main()
