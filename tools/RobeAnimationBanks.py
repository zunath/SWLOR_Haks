"""Losslessly partition compiled, dummy-only robe bridges for NWSync.

This reads native MDL layouts from CleanModels and the historical compiler
(232-byte model, 196-byte animation, 112-byte dummy node). Only resource names
and relative offsets are rewritten;
controller descriptors, float bytes, events and native part IDs are copied.
Unexpected layouts fail closed. Joining a partition must reproduce its original
compiled file byte for byte before any output can be installed.
"""
from dataclasses import dataclass
import math
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
        self.geometry_first = _uint(self.data, 72) == cursor
        if self.geometry_first:
            start, pointers, rename = cursor, [], []
            cursor = self._tree(cursor, pointers, rename)
            self.geometry = self._component(start, cursor, pointers, rename)
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
        if not self.geometry_first:
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

    def _tree(self, start, pointers, names, depth=0, parent=0):
        if depth > 256:
            raise ValueError("Excessive dummy-node hierarchy depth")
        self._require(start, 112)
        if _uint(self.data, start + 108) != 1:
            raise ValueError("Only dummy-node animation bridges can be partitioned")
        if _uint(self.data, start + 64):
            raise ValueError("Unexpected runtime geometry pointer")
        parent_pointer = _uint(self.data, start + 68)
        if parent_pointer:
            if parent_pointer != parent:
                raise ValueError("Unexpected native parent node pointer")
            pointers.append(start + 68)
        if _name(self.data, start + 32, 32) == self.name:
            names.append((start + 32, 32))
        count = _uint(self.data, start + 76)
        # CleanModels writes controllers before the child array/subtrees. The
        # historical compiler writes them after. Relocate either without
        # changing any controller, event or native part-ID bytes.
        controllers_first = _uint(self.data, start + 88) and _uint(self.data, start + 84) == start + 112
        cursor = start + 112
        if controllers_first:
            cursor = self._array(start + 84, 12, cursor, pointers)
            cursor = self._array(start + 96, 4, cursor, pointers)
        children = cursor
        cursor = self._array(start + 72, 4, cursor, pointers)
        for index in range(count):
            offset = children + index * 4
            if _uint(self.data, offset) != cursor:
                raise ValueError("Child nodes are not contiguous native compiler output")
            pointers.append(offset)
            cursor = self._tree(cursor, pointers, names, depth + 1, start)
        if not controllers_first:
            cursor = self._array(start + 84, 12, cursor, pointers)
            cursor = self._array(start + 96, 4, cursor, pointers)
        return cursor

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
        if self.geometry_first:
            _set_uint(result, 72, len(result))
            result.extend(self.geometry.relocated(len(result), name))
        for index, clip in enumerate(clips):
            _set_uint(result, 232 + index * 4, len(result))
            result.extend(clip.relocated(len(result), name))
        if not self.geometry_first:
            _set_uint(result, 72, len(result))
            result.extend(self.geometry.relocated(len(result), name))
        return struct.pack("<3I", 0, len(result), 0) + result


def validate_motion_revision(before, after, allowed_clips):
    """Permit named motion fixes without cloning an otherwise identical rig.

    Comparing repacked native components preserves every other controller byte,
    timing/event header, part ID, geometry bind and model property.
    """
    old, new = CompiledBridge(before), CompiledBridge(after)
    allowed = set(allowed_clips)
    if not allowed or not allowed <= set(old.clip_names):
        raise ValueError("Motion revision must name existing animation clips")
    if (old.name, old.parent, old.clip_names) != (new.name, new.parent, new.clip_names):
        raise ValueError("Motion revision changed model identity, parent or clip inventory")
    def unchanged(model):
        return model.pack(model.name, model.parent,
                          [clip for name, clip in zip(model.clip_names, model.clips) if name not in allowed])
    if unchanged(old) != unchanged(new):
        raise ValueError("Motion revision changed the skeleton or an unselected animation")


def _equivalent(left, right):
    if isinstance(left, float) or isinstance(right, float):
        return math.isclose(left, right, rel_tol=2e-6, abs_tol=1.1e-7)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equivalent(left[key], right[key]) for key in left)
    if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
        return len(left) == len(right) and all(_equivalent(a, b) for a, b in zip(left, right))
    return left == right


def _component_values(component, model_name, animation=False):
    """Read meaningful dummy-node data independently of compiler serialization."""
    data = component.relocated(0, model_name)
    def tree(node):
        controls = {}
        keys, count, values = _uint(data, node + 84), _uint(data, node + 88), _uint(data, node + 96)
        for index in range(count):
            kind, rows, times, start, columns = struct.unpack_from("<IHHHB", data, keys + index * 12)
            if columns & 0xF0 or kind in controls:
                raise ValueError("Unsupported or duplicate bank controller")
            timestamps = struct.unpack_from(f"<{rows}f", data, values + times * 4)
            frames = [struct.unpack_from(f"<{columns}f", data, values + (start + row * columns) * 4)
                      for row in range(rows)]
            if kind == 20:
                normalized = []
                for frame in frames:
                    length = math.sqrt(sum(value * value for value in frame))
                    if not math.isclose(length, 1, abs_tol=2e-6):
                        raise ValueError("Bank orientation is not a unit quaternion")
                    frame = tuple(value / length for value in frame)
                    pivot = frame[3] if abs(frame[3]) > 1e-6 else next((value for value in frame[:3] if abs(value) > 1e-6), 1)
                    normalized.append(tuple(-value for value in frame) if pivot < 0 else frame)
                frames = normalized
            controls[kind] = (columns, timestamps, frames)
        if not animation:
            # CleanModels emits these static defaults even when omitted in ASCII.
            for kind, default in ((8, (0., 0., 0.)), (20, (0., 0., 0., 1.)), (36, (1.,))):
                if kind in controls and _equivalent(controls[kind], (len(default), (0.,), [default])):
                    del controls[kind]
        children = _uint(data, node + 72)
        return (_name(data, node + 32, 32), _uint(data, node + 24), _uint(data, node + 28),
                _uint(data, node + 108), controls,
                [tree(_uint(data, children + index * 4)) for index in range(_uint(data, node + 76))])
    if not animation:
        return tree(0)
    events, count = _uint(data, 184), _uint(data, 188)
    return (_name(data, 8, 64), _name(data, 120, 64), struct.unpack_from("<2f", data, 112),
            [(struct.unpack_from("<f", data, events + index * 36)[0], _name(data, events + index * 36 + 4, 32))
             for index in range(count)], tree(_uint(data, 72)))


def _same_skeleton(old, new):
    # Runtime function pointers and array placement differ between compilers;
    # classification/fog, scale and the complete ordered rig must agree. Bounds
    # are compiler-derived; the output always retains the original header.
    metadata = lambda model: (model.header[104:120], struct.unpack_from("<f", model.header, 164))
    return (_equivalent(metadata(old), metadata(new)) and
            _equivalent(_component_values(old.geometry, old.name), _component_values(new.geometry, old.name)))


def _align_revision_parts(before, candidate):
    """Address candidate tracks with the original rig's native part IDs."""
    from RobeAnimations import binary_parts, validate_animation_parts
    validate_animation_parts(before)
    validate_animation_parts(candidate)
    expected = binary_parts(before)
    if expected.keys() != binary_parts(candidate).keys():
        raise ValueError("Motion revision changed the skeleton attachment paths")
    result = bytearray(candidate)
    def walk(pointer, path):
        node = 12 + pointer
        struct.pack_into("<i", result, node + 28, expected[path])
        children = _uint(result, node + 72)
        for index in range(_uint(result, node + 76)):
            child = _uint(result, 12 + children + index * 4)
            name = _name(result, 12 + child + 32, 32).lower()
            walk(child, (*path, name))
    walk(_uint(result, 84), ())
    for index in range(_uint(result, 136)):
        animation = 12 + _uint(result, 12 + _uint(result, 132) + index * 4)
        walk(_uint(result, animation + 72), ())
    validate_animation_parts(result)
    return bytes(result)


def revise_motion(before, candidate, allowed_clips):
    """Keep unselected compiled bytes, ignoring unused event-string padding.

    The native compiler leaves bytes after an event name's NUL uninitialized.
    Candidate checks compare controller values and hierarchy across compiler
    layouts. Unselected native components and all original geometry/header bytes
    are copied from the original model, including their unused padding.
    """
    old, new = CompiledBridge(before), CompiledBridge(candidate)
    allowed = set(allowed_clips)
    if not allowed or not allowed <= set(old.clip_names):
        raise ValueError("Motion revision must name existing animation clips")
    cross_layout = old.geometry_first != new.geometry_first
    if cross_layout:
        new = CompiledBridge(_align_revision_parts(before, candidate))
    identical_skeleton = old.pack(old.name, old.parent, []) == new.pack(new.name, new.parent, [])
    if ((old.name, old.parent, old.clip_names) != (new.name, new.parent, new.clip_names) or
            not identical_skeleton and (not cross_layout or not _same_skeleton(old, new))):
        raise ValueError("Motion revision changed the skeleton, model identity or clip inventory")

    def comparable(clip):
        data = clip.relocated(0, old.name)
        events, count = struct.unpack_from("<II", data, 184)
        for index in range(count):
            start = events + index * 36 + 4
            _name(data, start, 32)
            end = start + 32
            zero = data.index(0, start, end)
            data[zero:end] = bytes(end - zero)
        return data

    clips = []
    for name, original, proposed in zip(old.clip_names, old.clips, new.clips):
        if name in allowed:
            clips.append(proposed)
            continue
        if original.relocated(0, old.name) != proposed.relocated(0, old.name):
            if comparable(original) != comparable(proposed) and (not cross_layout or not _equivalent(
                    _component_values(original, old.name, True), _component_values(proposed, old.name, True))):
                raise ValueError(f"Motion revision changed unselected animation: {name}")
        clips.append(original)
    result = old.pack(old.name, old.parent, clips)
    validate_motion_revision(before, result, allowed)
    return bytes(result)


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
