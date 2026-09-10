"""Losslessly partition compiled, dummy-only robe bridges for NWSync.

This reads the native nwnmdlcomp layout (232-byte model, 196-byte animation,
112-byte dummy node). Only resource names and relative offsets are rewritten;
controller descriptors, float bytes, events and native part IDs are copied.
Unexpected layouts fail closed. Joining a partition must reproduce its original
compiled file byte for byte before any output can be installed.
"""
from dataclasses import dataclass
import re
import struct

LIMIT_BYTES = 15 * 1024 * 1024
TARGET_BYTES = 14 * 1024 * 1024
_NAME = re.compile(r"p[fm][a-z]_ra\d{3}(?:_b\d{2,5})?\Z")
_HEAD = re.compile(r"p[fm][a-z]_ra\d{3}\Z")


def is_bank_name(name):
    return isinstance(name, str) and bool(_NAME.fullmatch(name)) and len(name) <= 16


def bank_names(manifest):
    names = set(manifest.get("animation_bridges", {}).values())
    for record in manifest.get("animation_bank_sets", {}).values():
        names.update(record["parts"])
    if not all(is_bank_name(name) for name in names):
        raise ValueError("Invalid robe animation bank resource name")
    return names


def _uint(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _set_uint(data, offset, value):
    struct.pack_into("<I", data, offset, value)


def _name(data, offset, length):
    raw = bytes(data[offset:offset + length])
    if len(raw) != length or b"\0" not in raw:
        raise ValueError("Invalid native model string")
    return raw.split(b"\0", 1)[0].decode("ascii")


def _set_name(data, offset, length, name):
    encoded = name.encode("ascii")
    if len(encoded) >= length:
        raise ValueError("Native model name exceeds field size")
    data[offset:offset + length] = encoded.ljust(length, b"\0")


@dataclass
class Component:
    start: int
    data: bytes
    pointers: tuple
    names: tuple

    def relocated(self, start, model_name):
        result = bytearray(self.data)
        for offset in self.pointers:
            _set_uint(result, offset, _uint(result, offset) + start - self.start)
        for offset, length in self.names:
            _set_name(result, offset, length, model_name)
        return result


class CompiledBridge:
    def __init__(self, binary):
        if len(binary) < 244 or binary[:4] != bytes(4):
            raise ValueError("Robe animation banks require compiled models")
        if _uint(binary, 8) or _uint(binary, 4) != len(binary) - 12:
            raise ValueError("Robe animation banks require dummy models without raw data")
        self.data = memoryview(binary)[12:]
        self.header = bytes(self.data[:232])
        self.name = _name(self.data, 8, 64)
        self.parent = _name(self.data, 168, 64)
        if not is_bank_name(self.name):
            raise ValueError(f"Not a generated robe animation bank: {self.name}")
        # Every added ancestor must be neutral for inherited translation scaling.
        if self.data[164:168] != struct.pack("<f", 1):
            raise ValueError("Robe bank partitioning requires unit animation scale")
        self._geometry_header(0)
        if _uint(self.data, 132):
            raise ValueError("Unexpected runtime supermodel pointer")
        pointers = []
        cursor = self._array(120, 4, 232, pointers)
        offsets = [_uint(self.data, 232 + i * 4) for i in range(_uint(self.data, 124))]
        self.clips = []
        names = []
        for offset in offsets:
            if offset != cursor:
                raise ValueError("Animation blocks are not contiguous native compiler output")
            start = cursor
            self._require(start, 196)
            self._geometry_header(start)
            names.append(_name(self.data, start + 8, 64))
            pointers, rename = [start + 72], []
            if _name(self.data, start + 120, 64) == self.name:
                rename.append((start + 120, 64))
            cursor = self._array(start + 184, 36, start + 196, pointers)
            if _uint(self.data, start + 72) != cursor:
                raise ValueError("Unexpected animation root offset")
            cursor = self._tree(cursor, pointers, rename)
            self.clips.append(self._component(start, cursor, pointers, rename))
        if len(set(names)) != len(names) or names != sorted(names):
            raise ValueError("Animation names must be unique and sorted for native lookup")
        self.clip_names = names
        if _uint(self.data, 72) != cursor:
            raise ValueError("Unexpected model geometry offset")
        start, pointers, rename = cursor, [], []
        cursor = self._tree(cursor, pointers, rename)
        self.geometry = self._component(start, cursor, pointers, rename)
        if cursor != len(self.data):
            raise ValueError("Unrecognized trailing native model data")

    def _require(self, offset, count):
        if offset < 0 or count < 0 or offset + count > len(self.data):
            raise ValueError("Truncated or out-of-range native model data")

    def _geometry_header(self, offset):
        self._require(offset, 112)
        if any(self.data[offset + 80:offset + 104]):
            raise ValueError("Unexpected runtime geometry arrays")

    def _array(self, offset, stride, cursor, pointers):
        self._require(offset, 12)
        pointer, count, allocated = struct.unpack_from("<3I", self.data, offset)
        if allocated != count or pointer != (cursor if count else 0):
            raise ValueError("Unexpected native array layout")
        self._require(cursor, count * stride)
        if count:
            pointers.append(offset)
        return cursor + count * stride

    def _tree(self, start, pointers, names, depth=0):
        if depth > 256:
            raise ValueError("Excessive dummy-node hierarchy depth")
        self._require(start, 112)
        if _uint(self.data, start + 108) != 1:
            raise ValueError("Only dummy-node animation bridges can be partitioned")
        if any(self.data[start + 64:start + 72]):
            raise ValueError("Unexpected runtime node pointers")
        if _name(self.data, start + 32, 32) == self.name:
            names.append((start + 32, 32))
        count = _uint(self.data, start + 76)
        cursor = self._array(start + 72, 4, start + 112, pointers)
        for index in range(count):
            offset = start + 112 + index * 4
            if _uint(self.data, offset) != cursor:
                raise ValueError("Child nodes are not contiguous native compiler output")
            pointers.append(offset)
            cursor = self._tree(cursor, pointers, names, depth + 1)
        cursor = self._array(start + 84, 12, cursor, pointers)
        return self._array(start + 96, 4, cursor, pointers)

    def _component(self, start, end, pointers, names):
        return Component(start, bytes(self.data[start:end]),
                         tuple(offset - start for offset in pointers),
                         tuple((offset - start, length) for offset, length in names))

    def pack(self, name, parent, clips):
        if not is_bank_name(name):
            raise ValueError(f"Invalid robe bank name: {name}")
        result = bytearray(self.header)
        _set_name(result, 8, 64, name)
        _set_name(result, 168, 64, parent)
        struct.pack_into("<3I", result, 120, 232 if clips else 0, len(clips), len(clips))
        result.extend(bytes(4 * len(clips)))
        for index, clip in enumerate(clips):
            _set_uint(result, 232 + index * 4, len(result))
            result.extend(clip.relocated(len(result), name))
        _set_uint(result, 72, len(result))
        result.extend(self.geometry.relocated(len(result), name))
        return struct.pack("<3I", 0, len(result), 0) + result


def split(data, target_bytes=TARGET_BYTES):
    """Return head -> tail banks; never resample, truncate, or divide a clip."""
    if not 0 < target_bytes < LIMIT_BYTES:
        raise ValueError("Bank target must be below the NWSync file size limit")
    model = CompiledBridge(data)
    if not _HEAD.fullmatch(model.name):
        raise ValueError("Partition the complete bridge, not one of its child banks")
    overhead = 244 + len(model.geometry.data)
    if overhead >= target_bytes:
        raise ValueError(f"{model.name}: skeleton alone exceeds the bank size target")
    groups, current, size = [], [], overhead
    for name, clip in zip(model.clip_names, model.clips):
        cost = len(clip.data) + 4
        if overhead + cost >= target_bytes:
            raise ValueError(f"{model.name}/{name}: one animation exceeds the bank size target")
        if current and size + cost >= target_bytes:
            groups.append(current)
            current, size = [], overhead
        current.append(clip)
        size += cost
    groups.append(current)
    names = [model.name] + [f"{model.name}_b{index:02d}" for index in range(1, len(groups))]
    result = {name: model.pack(name, names[index + 1] if index + 1 < len(names) else model.parent, group)
              for index, (name, group) in enumerate(zip(names, groups))}
    validate_split(data, list(result.values()))
    return result


def join(parts):
    """Recover the original native compiler bytes, including their exact offsets."""
    if not parts:
        raise ValueError("Missing robe animation banks")
    models = [CompiledBridge(data) for data in parts]
    first = models[0]
    if not _HEAD.fullmatch(first.name):
        raise ValueError("The first bank must be the original bridge head")
    clips = []
    for index, model in enumerate(models):
        expected = first.name if index == 0 else f"{first.name}_b{index:02d}"
        if model.name != expected or (index and models[index - 1].parent != model.name):
            raise ValueError("Broken or reordered robe animation bank chain")
        # Empty canonical packs include every header/geometry field, native part
        # count, bind transform and model bound, with names/offsets normalized.
        if model.pack(first.name, "null", []) != first.pack(first.name, "null", []):
            raise ValueError("Robe animation banks have different skeletons or model metadata")
        clips.extend(model.clips)
    result = first.pack(first.name, models[-1].parent, clips)
    CompiledBridge(result)  # Also reject duplicate or globally unsorted animations.
    return result


def validate_split(original, parts):
    if any(len(data) >= LIMIT_BYTES for data in parts):
        raise ValueError("Compiled animation model exceeds the NWSync file size limit")
    if join(parts) != original:
        raise ValueError("Partitioning changed original compiled animation data")
