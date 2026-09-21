"""Inherited humanoid animations must not key bone positions or scales.

A player model inherits its clips from a shared supermodel chain, but keeps its
own bind pose. A position or scale key on a skeleton bone therefore replaces the
wearer's own proportions with the authoring rig's, and the replacement latches:
native idle animates rotation only and cannot restore the overwritten offset.
Native BioWare clips key bone rotations and the root position alone for exactly
this reason.

This removes the position and scale controllers from skeleton bones in every
clip of the humanoid player chain, leaving rotations, root travel, part
attachment dummies, geometry and all other controller bytes untouched. The edit
is made in place at the same byte offsets so unrelated clips cannot be disturbed
the way a full decompile/recompile round trip would disturb them.

Robe animation banks are then closed up into the packed layout the native
compiler writes, because RobeAnimationBanks splits, joins and repacks them and
fails closed on anything else. Closing up deletes only the vacated controller
slots and their orphaned floats: every surviving byte is copied and only offsets
move. The robe manifest's digests are refreshed to match.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re
import struct

import RobeAnimationBanks as banks

ROOT = Path(__file__).resolve().parents[1]
ROBE_MANIFEST = "tools/RobeRgbModels.json"
SOURCE_MARKER = "# SWLOR compiled animation source v1 "
# The skeleton the wearer owns. Part attachment dummies (head, lhand, rhand,
# lforearm, impact) are not bones and are keyed by native clips as well.
BONES = frozenset({"torso_g", "pelvis_g", "neck_g", "head_g"} |
                  {f"{side}{bone}_g" for side in "lr"
                   for bone in ("bicep", "forearm", "hand", "thigh", "shin", "foot")})
POSITION, SCALE = 8, 36
ANIMATION_DIRECTORIES = ("sw_cr_creature", "sw_anim_f", "sw_anim_m")


def binary(data: bytes) -> bool:
    return len(data) >= 244 and data[:4] == bytes(4)


def uint(data, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def name_at(data, offset: int) -> str:
    return data[offset:offset + 32].split(b"\0", 1)[0].decode("latin1").lower()


def supermodel(data: bytes) -> str:
    result = data[180:244].split(b"\0", 1)[0].decode("latin1").lower()
    return "" if result == "null" else result


def animation_nodes(data):
    """Yield every animation node offset, depth first, for each clip in the model."""
    for index in range(uint(data, 136)):
        animation = 12 + uint(data, 12 + uint(data, 132) + index * 4)
        clip = data[animation + 8:animation + 72].split(b"\0", 1)[0].decode("latin1").lower()
        pending, visited = [uint(data, animation + 72)], set()
        while pending:
            pointer = pending.pop()
            if pointer in visited:
                raise ValueError("Repeated native animation node")
            visited.add(pointer)
            node = pointer + 12
            if node + 112 > 12 + uint(data, 4):
                raise ValueError("Animation node is outside model data")
            yield clip, node
            pending.extend(uint(data, 12 + uint(data, node + 72) + child * 4)
                           for child in range(uint(data, node + 76)))


def offenders(data: bytes):
    """Report (clip, bone, controller) for every bone transform an inherited clip keys."""
    result = []
    if not binary(data):
        return result
    for clip, node in animation_nodes(data):
        bone = name_at(data, node + 32)
        if bone not in BONES:
            continue
        start, count, allocated = struct.unpack_from("<3I", data, node + 84)
        if count != allocated:
            raise ValueError(f"Unexpected native controller arrays for {clip}/{bone}")
        for key in range(count):
            kind, rows = struct.unpack_from("<IH", data, 12 + start + key * 12)
            if rows and kind in (POSITION, SCALE):
                result.append((clip, bone, kind))
    return result


def strip(data: bytes) -> bytes:
    """Drop bone position and scale keys, preserving every other byte and the file length."""
    result = bytearray(data)
    for clip, node in animation_nodes(result):
        bone = name_at(result, node + 32)
        if bone not in BONES:
            continue
        start, count, allocated = struct.unpack_from("<3I", result, node + 84)
        if count != allocated:
            raise ValueError(f"Unexpected native controller arrays for {clip}/{bone}")
        kept = [bytes(result[12 + start + key * 12:12 + start + (key + 1) * 12]) for key in range(count)]
        kept = [key for key in kept if uint(key, 0) not in (POSITION, SCALE)]
        if len(kept) == count:
            continue
        # Shrink the key array in place. The trailing slots become unreferenced
        # padding inside this node's own allocation, and the float array keeps
        # its length so every later offset in the model stays valid.
        result[12 + start:12 + start + count * 12] = b"".join(kept).ljust(count * 12, b"\0")
        struct.pack_into("<3I", result, node + 84, start, len(kept), len(kept))
    return bytes(result)


def _controllers(data, node: int):
    """A node's controller keys: (type, rows, time index, value index, columns, padding)."""
    start, count, _ = struct.unpack_from("<3I", data, node + 84)
    keys = [struct.unpack_from("<IHHHB3s", data, 12 + start + key * 12) for key in range(count)]
    if any(columns & 0xF0 for _, _, _, _, columns, _ in keys):
        raise ValueError("Unexpected native controller encoding")
    return keys


def _tracks(data):
    """Each animation node's identity and controller values, independent of any offset."""
    for clip, node in animation_nodes(data):
        floats = 12 + uint(data, node + 96)
        yield (clip, bytes(data[node:node + 64]), bytes(data[node + 76:node + 84]),
               [(kind, rows, columns, padding,
                 bytes(data[floats + time * 4:floats + (time + rows) * 4]),
                 bytes(data[floats + values * 4:floats + (values + rows * columns) * 4]))
                for kind, rows, time, values, columns, padding in _controllers(data, node)])


def pack(data: bytes) -> bytes:
    """Close up a stripped robe bank into the packed layout of the native compiler.

    The compiler writes a node's keys directly before its floats, and each
    controller's times and values as consecutive blocks in key order. Removing
    a controller therefore leaves vacated key slots between the two arrays and
    floats that no key references. Both are deleted and the offsets behind them
    moved; nothing else is rewritten. A packed bank is returned unchanged.
    """
    result = bytearray(data)
    pointers = [84, 132]  # Geometry root and the animation offset array.
    roots = [uint(result, 84)]
    for index in range(uint(result, 136)):
        entry = 12 + uint(result, 132) + index * 4
        animation = 12 + uint(result, entry)
        pointers += [entry, animation + 72, animation + 184]
        roots.append(uint(result, animation + 72))
    removed, pending, visited = [], roots, set()
    while pending:
        pointer = pending.pop()
        node = pointer + 12
        if pointer in visited or node + 112 > len(result):
            raise ValueError("Repeated or out-of-range native node")
        visited.add(pointer)
        children, child_count, _ = struct.unpack_from("<3I", result, node + 72)
        pointers += [node + 72, node + 84, node + 96]
        pointers += [12 + children + child * 4 for child in range(child_count)]
        pending.extend(uint(result, 12 + children + child * 4) for child in range(child_count))
        start, count, _ = struct.unpack_from("<3I", result, node + 84)
        floats, float_count, float_allocated = struct.unpack_from("<3I", result, node + 96)
        if float_count != float_allocated or (float_count and not start):
            raise ValueError("Unexpected native controller arrays")
        # Vacated key slots sit between the last remaining key and the floats.
        slots = 12 + start + count * 12
        vacated = 12 + floats - slots if float_count else 0
        if vacated < 0 or vacated % 12 or any(result[slots:slots + vacated]):
            raise ValueError("Unexpected native controller arrays")
        if vacated:
            removed.append((slots, slots + vacated))
        cursor = orphaned = 0
        for key, (kind, rows, time, values, columns, padding) in enumerate(_controllers(result, node)):
            if time < cursor or values != time + rows:
                raise ValueError("Unexpected native controller float layout")
            if time > cursor:
                removed.append((12 + floats + cursor * 4, 12 + floats + time * 4))
                orphaned += time - cursor
            struct.pack_into("<IHHHB3s", result, 12 + start + key * 12,
                             kind, rows, time - orphaned, values - orphaned, columns, padding)
            cursor = values + rows * columns
        if cursor > float_count:
            raise ValueError("Unexpected native controller float layout")
        if cursor < float_count:
            removed.append((12 + floats + cursor * 4, 12 + floats + float_count * 4))
        struct.pack_into("<3I", result, node + 84, start if count else 0, count, count)
        struct.pack_into("<3I", result, node + 96, floats if cursor else 0, cursor - orphaned, cursor - orphaned)
    if not removed:
        banks.CompiledBridge(data)
        return data
    removed.sort()
    ends, totals = [], [0]
    for begin, end in removed:
        if ends and begin < ends[-1]:
            raise ValueError("Overlapping native controller arrays")
        ends.append(end)
        totals.append(totals[-1] + end - begin)
    for field in pointers:
        target = uint(result, field) + 12
        if target == 12:
            continue
        # Only a float array may begin with a removed block; it then starts where that block did.
        index = bisect_right(ends, target)
        if index < len(removed) and removed[index][0] < target:
            raise ValueError("Native offset refers to removed controller data")
        struct.pack_into("<I", result, field, target - 12 - totals[index])
    for begin, end in reversed(removed):
        del result[begin:end]
    struct.pack_into("<I", result, 4, len(result) - 12)
    result = bytes(result)
    banks.CompiledBridge(result)  # The strict bank parser is the authority on the layout.
    if list(_tracks(result)) != list(_tracks(data)):
        raise ValueError("Packing changed animation data")
    return result


def chain_models(root: Path) -> list[Path]:
    """The humanoid animation chain reachable from the player body and garment models.

    A worn robe replaces the body parts and brings its own supermodel, so the
    chain has to be seeded from the garment models as well as the body roots.
    Other part folders carry meshes rather than a skeleton and are excluded.
    """
    index = {}
    for directory in ANIMATION_DIRECTORIES:
        for path in sorted((root / directory).glob("*.mdl")):
            index.setdefault(path.stem.lower(), path)
    pending, seen = [], set()
    for directory in (root / "sw_pt_root", root / "sw_pt_robe"):
        for path in sorted(directory.glob("*.mdl")):
            data = path.read_bytes()
            pending.append(supermodel(data) if binary(data) else ascii_supermodel(data))
    while pending:
        name = pending.pop()
        if not name or name in seen or name not in index:
            continue
        seen.add(name)
        data = index[name].read_bytes()
        if binary(data):
            pending.append(supermodel(data))
    return [index[name] for name in sorted(seen)]


def ascii_supermodel(data: bytes) -> str:
    match = re.search(rb"(?im)^\s*setsupermodel\s+\S+\s+(\S+)", data)
    result = match[1].decode("latin1").lower() if match else ""
    return "" if result == "null" else result


_TRANSFORM = re.compile(r"\A\s*(position|scale)(key)?\b(.*)\Z", re.IGNORECASE)


def strip_source_text(text: str) -> str:
    """Remove bone position/scale controllers from the clips of an editable bank source.

    Only `newanim` blocks are touched; the geometry section keeps the bind pose
    that these controllers were wrongly overriding.
    """
    lines, result = text.split("\n"), []
    in_animation, bone, index = False, False, 0
    while index < len(lines):
        line = lines[index]
        keyword = line.strip().split(" ", 1)[0].lower() if line.strip() else ""
        if keyword == "newanim":
            in_animation = True
        elif keyword == "doneanim":
            in_animation, bone = False, False
        elif keyword == "node":
            parts = line.split()
            bone = in_animation and len(parts) > 2 and parts[2].lower() in BONES
        elif keyword == "endnode":
            bone = False
        match = _TRANSFORM.match(line) if bone else None
        if match:
            index += 1
            if match[2]:  # positionkey/scalekey: skip the declared value rows too
                rows = int(match[3].strip().split(" ")[0] or 0)
                index += rows
            continue
        result.append(line)
        index += 1
    return "\n".join(result)


def source_path_for(root: Path, model: Path) -> Path:
    return root / "model_sources" / (model.relative_to(root).as_posix() + ".ascii")


def update_source(root: Path, model: Path, check_only: bool) -> int:
    """Keep the editable bank source and its compiled/content digests in step."""
    path = source_path_for(root, model)
    if not path.exists():
        return 0
    raw = path.read_bytes()
    saved = raw.decode("utf-8-sig").replace("\r\n", "\n")
    end = saved.index("\n")
    if not saved.startswith(SOURCE_MARKER):
        raise ValueError(f"{path}: missing compiled animation source metadata")
    content = strip_source_text(saved[end + 1:])
    header = (SOURCE_MARKER + hashlib.sha256(model.read_bytes()).hexdigest() + " " +
              hashlib.sha256(content.encode("utf-8")).hexdigest())
    if header == saved[:end]:
        return 0
    if not check_only:
        # Keep the file's existing preamble and line endings; only the header
        # digests and the removed controller lines change.
        text = header + "\n" + content
        path.write_bytes(raw[:3] if raw[:3] == b"\xef\xbb\xbf" else b"")
        with path.open("ab") as handle:
            handle.write(text.replace("\n", "\r\n" if b"\r\n" in raw else "\n").encode("utf-8"))
    return 1


def refresh_robe_manifest(root: Path, changed: list[Path]) -> int:
    """Keep the robe catalog's ownership proofs on the exact bytes now installed."""
    path = root / ROBE_MANIFEST
    if not changed or not path.is_file():
        return 0
    raw = path.read_bytes()
    manifest = json.loads(raw)
    names = {model.stem.lower() for model in changed}
    refreshed = 0
    for model in changed:
        relative = model.relative_to(root).as_posix()
        if relative in manifest["files"]:
            manifest["files"][relative] = hashlib.sha256(model.read_bytes()).hexdigest()
            refreshed += 1
    for head, record in manifest.get("animation_bank_sets", {}).items():
        if names.isdisjoint(record["parts"]):
            continue
        directory = root / ("sw_anim_f" if head[1] == "f" else "sw_anim_m")
        original = banks.join([(directory / f"{part}.mdl").read_bytes() for part in record["parts"]])
        record["source_sha256"] = hashlib.sha256(original).hexdigest()
        # A changed output must acquire a fresh proof on regeneration.
        manifest.get("model_builds", {}).pop(head, None)
    text = json.dumps(manifest, indent=2) + "\n"
    path.write_bytes(text.replace("\n", "\r\n" if b"\r\n" in raw else "\n").encode("utf-8"))
    return refreshed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Report offenders without writing.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    failures, sources, unpacked, changed = 0, 0, 0, []
    for path in chain_models(args.root):
        data = path.read_bytes()
        if not binary(data):
            continue
        updated = data
        found = offenders(data)
        if found:
            failures += len(found)
            clips = sorted({clip for clip, _, _ in found})
            print(f"{path.relative_to(args.root).as_posix()}: {len(found)} bone tracks in {clips}")
            updated = strip(data)
            if offenders(updated):
                raise RuntimeError(f"{path}: bone tracks remain after stripping")
        if banks.is_bank_name(path.stem.lower()):
            packed = pack(updated)
            if packed != updated and not found:
                unpacked += 1
                print(f"{path.relative_to(args.root).as_posix()}: robe bank is not in the packed native layout")
            updated = packed
        if updated != data and not args.check_only:
            path.write_bytes(updated)
            changed.append(path)
        stale = update_source(args.root, path, args.check_only)
        if stale:
            sources += stale
            print(f"{source_path_for(args.root, path).relative_to(args.root).as_posix()}: editable source refreshed")
    if args.check_only:
        print(f"{failures} inherited bone transform tracks found; {unpacked} robe banks unpacked; "
              f"{sources} editable sources out of date.")
        return 1 if failures or unpacked or sources else 0
    digests = refresh_robe_manifest(args.root, changed)
    print(f"Removed {failures} inherited bone transform tracks; packed {unpacked} robe banks; "
          f"refreshed {sources} editable sources and {digests} robe manifest digests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
