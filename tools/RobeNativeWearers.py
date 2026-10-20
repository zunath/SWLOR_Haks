"""Losslessly rebind explicitly selected, immutable native wearer geometry."""
from __future__ import annotations

import hashlib
import re
import struct

import AlignRobeAnimations as align
import CompileModels as mdl
import RobeAnimations as animations
import RobePoseAudit as poses


def capture(name, data, expected_hash, garment, garment_hash, expected_garment_hash):
    digest = hashlib.sha256(data).hexdigest()
    if not expected_hash or digest != expected_hash:
        raise ValueError(f"{name}: native wearer is not the verified prior resource")
    if not expected_garment_hash or garment_hash != expected_garment_hash:
        raise ValueError(f"{name}: authored garment input changed; cannot preserve native geometry")
    if not mdl.binary(data) or data[20:84].split(b"\0", 1)[0].decode("ascii").lower() != name:
        raise ValueError(f"{name}: native wearer identity changed")
    return {"source_sha256": digest, "garment": garment, "garment_sha256": garment_hash,
            "old_parent": mdl.supermodel(data)}


def with_parent(data, parent):
    if not re.fullmatch(r"p[fm][a-z]_ra\d{3}", parent):
        raise ValueError("Native wearer requires a generated animation parent")
    result = bytearray(data)
    result[180:244] = parent.encode("ascii").ljust(64, b"\0")
    return bytes(result)


def canonical_source(text, data, parent):
    text = poses.accurate_rotations(text, data)
    result, count = re.subn(r"(?im)^(\s*setsupermodel\s+\S+\s+)\S+",
                           lambda match: match[1] + parent, text)
    if count != 1:
        raise ValueError("Native wearer source must have exactly one parent")
    return result.encode("latin1")


def _without_normals(text):
    """Input-compatibility view only; authoritative normals are never modified.

    Earlier compilers recomputed normals. Desired source and immutable native
    geometry must agree on every other property. Full roundtrip validation still
    compares the candidate's normals against its captured native source, and the
    byte whitelist below preserves every original normal byte exactly.
    """
    lines = text.splitlines(keepends=True)
    result, index = [], 0
    while index < len(lines):
        match = re.fullmatch(r"\s*normals\s+(\d+)\s*", lines[index])
        if match:
            index += 1 + int(match[1])
        else:
            result.append(lines[index])
            index += 1
    return "".join(result)


def validate_desired_source(desired, native_source, data, parent):
    desired_text, native_text = desired.decode("latin1"), native_source.decode("latin1")
    geometry = lambda text: mdl.parse_nodes(text.split("endmodelgeom", 1)[0])
    identity = lambda nodes: [(kind.lower(), name.lower(), props.get("parent", ["null"])[0].lower())
                              for kind, name, props in nodes]
    if identity(geometry(desired_text)) != identity(geometry(native_text)):
        raise ValueError("Native wearer has a different desired node topology or mesh type")
    mdl.validate_round_trip(_without_normals(desired_text).encode("latin1"),
                            with_parent(data, parent), _without_normals(native_text))


def validate_preserved_bytes(before, after):
    if len(before) != len(after):
        raise ValueError("Native wearer geometry size changed")
    uint = lambda offset: struct.unpack_from("<I", before, offset)[0]
    roots = [uint(84)] + [uint(12 + uint(12 + uint(132) + index * 4) + 72)
                           for index in range(uint(136))]
    allowed = {88, 89, 90, 91, *range(180, 244)}
    for root in roots:
        for _, offset in animations._node_offsets(before, root):
            allowed.update(range(offset + 28, offset + 32))
    if any(left != right and index not in allowed for index, (left, right) in enumerate(zip(before, after))):
        raise ValueError("Native wearer changed bytes outside parent/header/animation IDs")


def rebind(data, old_parent, new_parent, parent_name, base):
    result = align.remap_inherited_parts(data, old_parent, new_parent)
    result = animations.number_static_garment_ancestors(with_parent(result, parent_name))
    result = animations.preserve_static_body_parts(result, base)
    validate_preserved_bytes(data, result)
    animations.validate_animation_parts(result)
    animations.validate_body_parts(result, base, new_parent)
    poses.validate_body_skeleton(result, base, ignore_garment=True)
    poses.validate_garment_bindings(data, result, {name: name for name, _, _, _, _ in poses.Model(data, False).nodes})
    return result
