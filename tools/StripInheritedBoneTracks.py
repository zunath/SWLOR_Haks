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
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Report offenders without writing.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    failures, sources = 0, 0
    for path in chain_models(args.root):
        data = path.read_bytes()
        if not binary(data):
            continue
        found = offenders(data)
        if found:
            failures += len(found)
            clips = sorted({clip for clip, _, _ in found})
            print(f"{path.relative_to(args.root).as_posix()}: {len(found)} bone tracks in {clips}")
            if not args.check_only:
                updated = strip(data)
                if offenders(updated):
                    raise RuntimeError(f"{path}: bone tracks remain after stripping")
                path.write_bytes(updated)
        stale = update_source(args.root, path, args.check_only)
        if stale:
            sources += stale
            print(f"{source_path_for(args.root, path).relative_to(args.root).as_posix()}: editable source refreshed")
    if args.check_only:
        print(f"{failures} inherited bone transform tracks found; {sources} editable sources out of date.")
        return 1 if failures or sources else 0
    print(f"Removed {failures} inherited bone transform tracks; refreshed {sources} editable sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
