"""Combine garment animation overlays with a complete wearable body skeleton."""
from __future__ import annotations

import re
import struct

import CompileModels as mdl

ANIMATION = re.compile(r"(?im)^\s*newanim\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*doneanim[^\n]*")
NODE = re.compile(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b")
TRANSFORMS = {"position", "orientation", "scale"}
# A bridge is shared by a whole garment family, while each wearer keeps its own
# bind pose. Copying a bone's offset or scale into the bridge would stamp the
# source body's proportions onto every other appearance, and the replacement
# latches because native idle animates rotation only.
WEARER_BONES = {"torso_g", "pelvis_g", "neck_g", "head_g"} | {
    f"{side}{bone}_g" for side in "lr"
    for bone in ("bicep", "forearm", "hand", "thigh", "shin", "foot")}
BODY_MOTION_NODES = WEARER_BONES | {"rootdummy"}


def binary_parts(data, root=None):
    """Animation part IDs are distinct from the skin's traversal/bone indices."""
    if not mdl.binary(data):
        raise ValueError("Animation ID validation requires a compiled model")
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    result = {}
    def visit(pointer, path):
        offset = pointer + 12
        node = data[offset + 32:offset + 64].split(b"\0", 1)[0].decode("ascii").lower()
        current = (*path, node) if path is not None else ()
        result[current] = struct.unpack_from("<i", data, offset + 28)[0]
        for index in range(uint(offset + 76)):
            visit(uint(12 + uint(offset + 72) + index * 4), current)
    visit(root if root is not None else uint(84), None)
    return result


def validate_body_parts(generated, base, animation_parent):
    actual = binary_parts(generated)
    for path, part in binary_parts(base).items():
        if part >= 0 and path not in actual:
            raise ValueError(f"{'/'.join(path)}: body animation attachment is missing")
    for expected in (binary_parts(base), binary_parts(animation_parent)):
        for path, part in expected.items():
            if part >= 0 and path in actual and actual[path] != part:
                raise ValueError(f"{'/'.join(path)}: animation part {actual[path]} must be {part}")


def preserve_static_body_parts(data, base):
    """Keep unanimated native body leaves at -1 after a fresh compiler pass.

    CleanModels can allocate an otherwise unused ID to an ordinary dummy leaf
    even when its inherited native body ID is -1. Do not change animated nodes
    or ancestors: the engine needs their IDs to reach descendant tracks.
    """
    actual, expected = binary_parts(data), binary_parts(base)
    targets = {path: actual[path] for path, part in expected.items()
               if path and part == -1 and actual.get(path, -1) >= 0}
    if not targets:
        return data
    for path in targets:
        if any(other[:len(path)] == path and len(other) > len(path) for other in actual):
            raise ValueError(f"{'/'.join(path)}: cannot restore a static body ID above descendants")
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    roots = [uint(84)] + [uint(12 + uint(12 + uint(132) + index * 4) + 72)
                           for index in range(uint(136))]
    trees = [_node_offsets(data, root) for root in roots]
    for tree in trees[1:]:
        for path, offset in tree:
            part = struct.unpack_from("<i", data, offset + 28)[0]
            if uint(offset + 88) and (path in targets or part in targets.values()):
                raise ValueError(f"{'/'.join(path)}: cannot restore an animated body ID")
    result = bytearray(data)
    for tree in trees:
        for path, offset in tree:
            if path in targets:
                struct.pack_into("<i", result, offset + 28, -1)
    result = bytes(result)
    validate_animation_parts(result)
    return result


def validate_animation_parts(data):
    geometry_parts = binary_parts(data)
    used = {}
    for path, part in geometry_parts.items():
        if part >= 0:
            if part in used:
                raise ValueError(f"Animation ID {part} is shared by {used[part]} and {path}")
            used[part] = path
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    for index in range(uint(136)):
        animation = 12 + uint(12 + uint(132) + index * 4)
        for path, part in binary_parts(data, uint(animation + 72)).items():
            if path not in geometry_parts or part != geometry_parts[path]:
                raise ValueError(f"{'/'.join(path)}: clip uses an invalid animation part {part}")


def _node_offsets(data, root):
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    result = []
    def visit(pointer, path):
        offset = pointer + 12
        node = data[offset + 32:offset + 64].split(b"\0", 1)[0].decode("ascii").lower()
        current = (*path, node) if path is not None else ()
        result.append((current, offset))
        for index in range(uint(offset + 76)):
            visit(uint(12 + uint(offset + 72) + index * 4), current)
    visit(root, None)
    return result


def static_garment_ancestors(data):
    """Geometry paths of an animation parent's unnumbered garment joints above a tracked node.

    The compiler numbers only nodes with tracks, so a garment's static copy of a
    wearer bone has part -1. The engine animates nothing beneath a -1 node: cloth
    helpers under it (coat tails, skirt bones) stay at their bind shape.
    """
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    parts = binary_parts(data)
    tracked = set()
    for index in range(uint(136)):
        root = uint(12 + uint(12 + uint(132) + index * 4) + 72)
        tracked.update(path for path, offset in _node_offsets(data, root) if uint(offset + 88))
    return sorted(path for path, part in parts.items()
                  if path and part < 0 and path[-1].startswith("rg_") and
                  any(other[:len(path)] == path and len(other) > len(path) for other in tracked))


def number_static_garment_ancestors(data):
    """Give those joints unused part IDs in geometry and every clip, adding no tracks.

    Without controllers the engine still leaves each joint at its wearer's own bind,
    so latched channels and per-wearer adapter transforms are unchanged; the IDs
    only let the engine reach the animated helpers below. Wearer roots compiled
    against this parent inherit the IDs by name.
    """
    targets = static_garment_ancestors(data)
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    roots = [uint(84)] + [uint(12 + uint(12 + uint(132) + index * 4) + 72) for index in range(uint(136))]
    trees = [_node_offsets(data, root) for root in roots]
    used = [struct.unpack_from("<i", data, offset + 28)[0] for tree in trees for _, offset in tree]
    assigned = {path: max(used) + 1 + index for index, path in enumerate(targets)}
    # An in-place motion revision can arrive with already-numbered joints but
    # an older undersized header, so normalize the high-water mark in that case too.
    part_count = max(uint(88), max([*used, *assigned.values()]) + 1)
    if not targets and part_count == uint(88):
        return data
    result = bytearray(data)
    for index, tree in enumerate(trees):
        for path, offset in tree:
            if path in assigned:
                # Geometry keeps each joint's bind transform; clips must add nothing.
                if index and uint(offset + 88):
                    raise ValueError(f"{'/'.join(path)}: a static garment joint must not carry tracks")
                struct.pack_into("<i", result, offset + 28, assigned[path])
    # Children allocate their own mesh IDs after this inherited ID space. The
    # manually numbered joints must reserve it just like compiler-owned IDs.
    struct.pack_into("<I", result, 88, part_count)
    result = bytes(result)
    validate_animation_parts(result)
    return result


def validate_static_garment_parts(data):
    if static_garment_ancestors(data):
        raise ValueError("Animated garment joints sit beneath unnumbered joints: " +
                         ", ".join("/".join(path) for path in static_garment_ancestors(data)))


def model_name(text):
    return re.search(r"(?im)^\s*newmodel\s+(\S+)", text)[1].lower()


def geometry(text):
    return mdl.parse_nodes(text.split("endmodelgeom", 1)[0])


def chain(name, load):
    result, seen = [], set()
    while name:
        if name in seen:
            raise ValueError(f"Cyclic animation inheritance: {name}")
        seen.add(name)
        text = load(name)
        if text is None:
            break
        result.append((name, text))
        name = mdl.supermodel(text.encode("latin1"))
    return result


def resolved_animations(models):
    result = {}
    for name, text in models:
        for animation in ANIMATION.finditer(text):
            result.setdefault(animation[1].lower(), (name, animation))
    return result


class BodyAnimationInheritance:
    """Identify ordinary garments without dropping authored animation overlays."""

    def __init__(self, load):
        self.load = load
        self.owners = {}
        self.parents = {}

    def animation_owners(self, name, visiting=None):
        if name in self.owners:
            return self.owners[name]
        visiting = set() if visiting is None else visiting
        if name in visiting:
            raise ValueError(f"Cyclic animation inheritance: {name}")
        visiting.add(name)
        text = self.load(name)
        result = None
        if text is not None:
            parent = mdl.supermodel(text.encode("latin1"))
            inherited = self.animation_owners(parent, visiting) if parent else {}
            if inherited is not None:
                result = dict(inherited)
                for clip in ANIMATION.finditer(text):
                    result[clip[1].lower()] = name
        visiting.remove(name)
        self.owners[name] = result
        return result

    def attachment_parents(self, text):
        name = model_name(text)
        if name not in self.parents:
            parents = {}
            for node in NODE.finditer(text.split("endmodelgeom", 1)[0]):
                parent = re.search(r"(?im)^\s*parent\s+(\S+)", node[3])[1].lower()
                parents[node[2].lower()] = None if parent == name else parent
            parents.pop(name)
            self.parents[name] = parents
        return self.parents[name]

    def can_inherit(self, base_name, robe):
        expected = self.animation_owners(base_name)
        if expected is None or self.animation_owners(model_name(robe)) != expected:
            return False
        base = self.attachment_parents(self.load(base_name))
        garment = self.attachment_parents(robe)
        # Missing joints are filled from the body by body_root. Shared joints
        # must stay under the same parents so the compiler can match their IDs.
        return all(garment[node] == base[node] for node in garment.keys() & base.keys())


def skeleton(base, overlays, name):
    """Retain body attachment paths and add the overlay's animation helpers."""
    result = {}
    for owner, text in [(model_name(base), base), *overlays]:
        for _, node, props in geometry(text):
            if node == owner:
                continue
            parent = props.get("parent", ["null"])[0].lower()
            if parent == owner:
                parent = name
            if node in result:
                if result[node]["parent"] != [parent]:
                    raise ValueError(f"{owner}/{node}: incompatible animation parent")
                continue
            result[node] = {key: value for key, value in props.items() if key in TRANSFORMS}
            result[node]["parent"] = [parent]
    return result


def serialize_node(name, props):
    lines = [f"node dummy {name}"]
    for key, value in props.items():
        if key.endswith("key"):
            lines.append(f"  {key} {len(value)}")
            lines.extend("    " + " ".join(row) for row in value)
        else:
            lines.append(f"  {key} " + " ".join(value))
    return "\n".join([*lines, "endnode\n"])


def order_nodes(nodes, root):
    result, visited = [], set()
    def visit(name):
        if name in visited:
            raise ValueError(f"Cyclic animation skeleton: {name}")
        visited.add(name)
        result.append((name, nodes[name]))
        for child, props in nodes.items():
            if props["parent"] == [name]:
                visit(child)
    visit(root)
    if len(result) != len(nodes):
        raise ValueError("Orphaned animation skeleton nodes")
    return result


def tracks(animation, owner, names, duration):
    if animation is None:
        return {}
    length = float(re.search(r"(?im)^\s*length\s+(\S+)", animation[3])[1])
    factor = duration / length if length else 1
    result = {}
    for _, node, props in mdl.parse_nodes(animation[3]):
        if node == owner or node not in names:
            continue
        bone = node.lower() in WEARER_BONES
        values = {}
        for key, value in props.items():
            if key in TRANSFORMS:
                if bone and key != "orientation":
                    continue
                values[key] = value
            elif key.endswith("key"):
                if key.removesuffix("key") not in TRANSFORMS:
                    raise ValueError(f"{owner}/{node}: unsupported garment controller {key}")
                if bone and key != "orientationkey":
                    continue
                values[key] = [[format(float(row[0]) * factor, '.9g'), *row[1:]] for row in value]
        result[node] = values
    return result


def bridge(base_name, robe_name, load, name):
    """Return an animation-only supermodel, or None for ordinary body inheritance.

    The compiler matches part numbers only against the immediate parent's tree.
    A separate garment is an incomplete skeleton; using it as a body's parent
    loses attachment IDs. Coat overlays also reuse IDs already used by cloaks.
    Compiling their clips against this complete skeleton remaps both correctly.
    """
    body_chain = chain(base_name, load)
    robe_chain = chain(robe_name, load)
    body_animations = resolved_animations(body_chain)
    robe_animations = resolved_animations(robe_chain)
    overlays = {clip: (owner, animation) for clip, (owner, animation) in robe_animations.items()
                if clip not in body_animations or body_animations[clip][0] != owner}
    if not overlays:
        return None
    owners = {owner for owner, _ in overlays.values()}
    base = body_chain[0][1]
    nodes = skeleton(base, [(owner, text) for owner, text in robe_chain if owner in owners], name)
    nodes = {name: {"parent": ["null"]}, **nodes}
    ordered = order_nodes(nodes, name)
    output = (f"newmodel {name}\nsetsupermodel {name} {base_name}\nclassification CHARACTER\n"
              f"setanimationscale 1\nbeginmodelgeom {name}\n" +
              "".join(serialize_node(node, props) for node, props in ordered) +
              f"endmodelgeom {name}\n")
    for clip, (owner, animation) in sorted(overlays.items()):
        body_owner, body_animation = body_animations.get(clip, (None, None))
        primary = body_animation if body_animation is not None else animation
        header = primary[3][:NODE.search(primary[3]).start()]
        duration = float(re.search(r"(?im)^\s*length\s+(\S+)", header)[1])
        # The body controls the clip duration and events; garment tracks follow
        # the same phase. Keep every coat controller, filling omitted body tracks.
        combined = tracks(body_animation, body_owner, nodes, duration)
        for node, values in tracks(animation, owner, nodes, duration).items():
            if body_animation is not None and node in BODY_MOTION_NODES:
                continue
            target = combined.setdefault(node, {})
            for key, value in values.items():
                target.pop(key.removesuffix("key") if key.endswith("key") else key + "key", None)
                target[key] = value
        anim_nodes = {node: {"parent": props["parent"], **combined.get(node, {})} for node, props in ordered}
        output += f"newanim {clip} {name}\n{header}" + "".join(
            serialize_node(node, props) for node, props in order_nodes(anim_nodes, name)) + f"doneanim {clip} {name}\n"
    return (output + f"donemodel {name}\n").encode("latin1")
