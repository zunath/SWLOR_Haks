"""Evaluate compiled skeletons, including native animation IDs and bind poses."""
from __future__ import annotations

from functools import lru_cache
import math
import re
import struct

import CompileModels as mdl


def accurate_rotations(text, data):
    """The legacy decompiler rounds tiny quaternion rotations to identity.

    Recover axis/angle with atan2 from the native quaternion instead of its
    rounded w component. Keep this in the compiler input so round-trip checks
    still validate exactly what will be shipped.
    """
    if not mdl.binary(data):
        return text
    model = Model(data)
    native = [*model.nodes, *(node for _, nodes in model.clips.values() for node in nodes)]
    pattern = re.compile(r"(?im)^\s*node\s+(\S+)\s+(\S+)\s*$([\s\S]*?)^\s*endnode\b")
    matches = list(pattern.finditer(text))
    if len(matches) != len(native):
        raise ValueError("Decompiler changed the native node/animation inventory")
    changes = []
    for match, (name, _, _, controllers, _) in zip(matches, native):
        if match[2].lower() != name:
            raise ValueError("Decompiler changed native node order")
        if 20 not in controllers:
            continue
        times, rotations = controllers[20]
        values = []
        for x, y, z, w in rotations:
            length = math.sqrt(x*x+y*y+z*z)
            values.append((x/length, y/length, z/length, 2*math.atan2(length, w)) if length else (0, 0, 0, 0))
        body = match[3]
        key = re.search(r"(?im)^\s*orientationkey\s+(\d+)[ \t]*$", body)
        if key:
            tail = body[key.end():].splitlines(keepends=True)
            # The first split line contains the newline after the count.
            start = 1 if tail and not tail[0].strip() else 0
            end = key.end()+sum(len(line) for line in tail[:start+int(key[1])])
            replacement = "\n  orientationkey " + str(len(values)) + "\n" + "".join(
                "    " + " ".join(format(v, '.17g') for v in (time, *value)) + "\n" for time, value in zip(times, values))
            body = body[:key.start()] + replacement + body[end:]
        else:
            if len(values) != 1:
                raise ValueError(f"{name}: animated quaternion was not decompiled as keys")
            replacement = "\n  orientation " + " ".join(format(v, '.17g') for v in values[0])
            body, count = re.subn(r"(?im)^\s*orientation\s+[^\n]+", replacement, body)
            if count != 1:
                raise ValueError(f"{name}: decompiler omitted a rotation controller")
        changes.append((match.start(3), match.end(3), body))
    result, cursor = [], 0
    for start, end, replacement in changes:
        result.extend((text[cursor:start], replacement))
        cursor = end
    return "".join([*result, text[cursor:]])


def multiply(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z)


def rotate(q, v):
    return multiply(multiply(q, (*v, 0)), (-q[0], -q[1], -q[2], q[3]))[:3]


def interpolate(a, b, weight, rotation=False):
    if rotation:
        dot = sum(x*y for x, y in zip(a, b))
        if dot < 0:
            b, dot = tuple(-x for x in b), -dot
        if dot < .9995:
            angle = math.acos(max(-1, min(1, dot)))
            left, right = math.sin((1-weight)*angle)/math.sin(angle), math.sin(weight*angle)/math.sin(angle)
            return tuple(left*x+right*y for x, y in zip(a, b))
    result = tuple(x+(y-x)*weight for x, y in zip(a, b))
    if rotation:
        length = math.sqrt(sum(x*x for x in result))
        return tuple(x/length for x in result)
    return result


def sample(controller, time, rotation=False):
    times, values = controller
    for index in range(1, len(times)):
        if time < times[index]:
            weight = max(0, (time-times[index-1])/(times[index]-times[index-1]))
            return interpolate(values[index-1], values[index], weight, rotation)
    return values[-1]


class Model:
    def __init__(self, data, read_clips=True):
        if not mdl.binary(data):
            raise ValueError("Pose audit requires compiled models")
        self.data = data
        self.parent = mdl.supermodel(data)
        self.scale = struct.unpack_from("<f", data, 176)[0]
        self.nodes = self.read_nodes(self.uint(84))
        self.clips = {}
        for index in range(self.uint(136) if read_clips else 0):
            offset = 12 + self.uint(12 + self.uint(132) + index*4)
            name = data[offset+8:offset+72].split(b"\0", 1)[0].decode().lower()
            self.clips[name] = (struct.unpack_from("<f", data, offset+112)[0],
                                self.read_nodes(self.uint(offset+72)))

    def uint(self, offset):
        return struct.unpack_from("<I", self.data, offset)[0]

    def read_nodes(self, root):
        result = []
        def visit(pointer, parent):
            offset = 12 + pointer
            name = self.data[offset+32:offset+64].split(b"\0", 1)[0].decode().lower()
            controllers = {}
            for index in range(self.uint(offset+88)):
                kind, rows, times, start, columns = struct.unpack_from("<IHHHB", self.data, 12+self.uint(offset+84)+index*12)
                if kind not in (8, 20, 36) or not rows:
                    continue
                if columns != {8:3, 20:4, 36:1}[kind]:
                    raise ValueError(f"{name}: unsupported transform controller {kind}/{columns}")
                values = 12 + self.uint(offset+96)
                controllers[kind] = (struct.unpack_from(f"<{rows}f", self.data, values+times*4),
                    [struct.unpack_from(f"<{columns}f", self.data, values+(start+row*columns)*4) for row in range(rows)])
            current = len(result)
            result.append((name, parent, struct.unpack_from("<i", self.data, offset+28)[0], controllers, offset))
            for index in range(self.uint(offset+76)):
                visit(self.uint(12+self.uint(offset+72)+index*4), current)
        visit(root, None)
        return result

    def pose(self, clip=None, time=0, translation_scale=1):
        tracks = {node[2]: node[3] for node in clip[1] if node[2] >= 0} if clip else {}
        result, by_name = [], {}
        for index, (name, parent, part, defaults, _) in enumerate(self.nodes):
            controllers = {**defaults, **tracks.get(part, {})}
            p = sample(controllers[8], time) if 8 in controllers else (0, 0, 0)
            if 8 in tracks.get(part, {}):
                p = tuple(v*translation_scale for v in p)
            q = sample(controllers[20], time, True) if 20 in controllers else (0, 0, 0, 1)
            s = sample(controllers[36], time)[0] if 36 in controllers else 1
            if parent is not None:
                pp, pq, ps = result[parent]
                p = tuple(a+b for a, b in zip(pp, rotate(pq, tuple(v*ps for v in p))))
                q, s = multiply(pq, q), ps*s
            result.append((p, q, s))
            by_name[name if name not in by_name else f"{name}_copy{index}"] = (p, q, s)
        return by_name

    def skin_bindings(self):
        """Native inverse binds are indexed by geometry traversal, not part ID."""
        result = {}
        seen = set()
        for index, (name, _, _, _, offset) in enumerate(self.nodes):
            if name in seen:
                name = f"{name}_copy{index}"
            seen.add(name)
            flags = self.uint(offset+108)
            if not flags & 0x40:
                continue
            extra = offset+112+(92 if flags & 2 else 0)+(216 if flags & 4 else 0)+(68 if flags & 16 else 0)+512
            qptr, qcount = self.uint(extra+28), self.uint(extra+32)
            tptr, tcount = self.uint(extra+40), self.uint(extra+44)
            bindings = {}
            mesh = extra-512
            count = struct.unpack_from("<H", self.data, mesh+448)[0]
            stride = self.uint(mesh+440)
            raw = 12+self.uint(4)
            used = set()
            for vertex in range(count):
                weights = struct.unpack_from("<4f", self.data, raw+self.uint(extra+12)+vertex*(stride or 16))
                indices = struct.unpack_from("<4h", self.data, raw+self.uint(extra+16)+vertex*(stride or 8))
                used.update(index for weight, index in zip(weights, indices) if weight != 0 and index >= 0)
            bone_nodes = struct.unpack_from("<17h", self.data, extra+64)
            for slot in used:
                bone = bone_nodes[slot]
                if bone < 0:
                    continue
                if bone >= min(len(self.nodes), qcount, tcount):
                    raise ValueError(f"{name}: inverse bind references absent bone {bone}")
                bindings[self.nodes[bone][0]] = (12+qptr+bone*16, 12+tptr+bone*12)
            result[name] = bindings
        return result


def preserve_skin_bindings(source, generated, names):
    """Keep authored inverse binds even when the compiler rebuilds node arrays."""
    if not mdl.binary(source):
        return generated  # ASCII sources have no precomputed inverse binds.
    original, target = Model(source, False), Model(generated, False)
    targets = target.skin_bindings()
    result = bytearray(generated)
    for mesh, bones in original.skin_bindings().items():
        mapped = targets[names[mesh]]
        if set(mapped) != {names[bone] for bone in bones}:
            raise ValueError(f"{mesh}: bound bone set changed during conversion")
        for bone, (qsrc, tsrc) in bones.items():
            qdst, tdst = mapped[names[bone]]
            result[qdst:qdst+16] = source[qsrc:qsrc+16]
            result[tdst:tdst+12] = source[tsrc:tsrc+12]
    return bytes(result)


def inverse_transform(position, orientation):
    rotation = (-orientation[0], -orientation[1], -orientation[2], orientation[3])
    return rotate(rotation, tuple(-v for v in position)), rotation


def compiler_skin_binding_repairs(data):
    """Recognize the legacy compiler's incorrect inverse-transform composition.

    NmcMesh.cpp adds inverse local translations without rotating them through
    their parents. Only repair a mesh when every used bind matches that exact
    calculation. Authored inverse binds that differ from it remain untouched.
    Native inverse-bind quaternions use WXYZ; controller quaternions use XYZW.
    """
    model = Model(data, False)
    world = model.pose()
    indices = {node[0]: index for index, node in enumerate(model.nodes)}
    if len(indices) != len(model.nodes):
        return []  # Ambiguous source names need a separate bone-reference audit.
    if any(abs(transform[2] - 1) > 2e-6 for transform in world.values()):
        return []  # Native inverse binds have no scale field.
    repairs = []
    for mesh, bones in model.skin_bindings().items():
        legacy_position, legacy_rotation = (0, 0, 0), (0, 0, 0, 1)
        index = indices[mesh]
        while index is not None:
            _, index, _, controllers, _ = model.nodes[index]
            position = sample(controllers[8], 0) if 8 in controllers else (0, 0, 0)
            rotation = sample(controllers[20], 0, True) if 20 in controllers else (0, 0, 0, 1)
            position, rotation = inverse_transform(position, rotation)
            legacy_position = tuple(a+b for a, b in zip(legacy_position, position))
            legacy_rotation = multiply(legacy_rotation, rotation)
        pending = []
        for bone, (qoffset, toffset) in bones.items():
            bp, bq, _ = world[bone]
            lp = tuple(a+b for a, b in zip(legacy_position, rotate(legacy_rotation, bp)))
            lt, lq = inverse_transform(lp, multiply(legacy_rotation, bq))
            stored = struct.unpack_from("<4f", data, qoffset)
            aq = (*stored[1:], stored[0])
            at = struct.unpack_from("<3f", data, toffset)
            if math.dist(at, lt) > 2e-5 or not mdl.equivalent_quaternion(aq, lq):
                break
            mp, mq, _ = world[mesh]
            _, inverse_bone = inverse_transform(bp, bq)
            correct_t = rotate(inverse_bone, tuple(a-b for a, b in zip(mp, bp)))
            correct_q = multiply(inverse_bone, mq)
            error = math.dist(at, correct_t)
            if error > 2e-5 or not mdl.equivalent_quaternion(aq, correct_q):
                pending.append((mesh, bone, qoffset, toffset, correct_q, correct_t, error))
        else:
            repairs.extend(pending)
    return repairs


def repair_compiler_skin_bindings(data):
    result = bytearray(data)
    for _, _, qoffset, toffset, q, t, _ in compiler_skin_binding_repairs(data):
        struct.pack_into("<4f", result, qoffset, q[3], *q[:3])
        struct.pack_into("<3f", result, toffset, *t)
    return bytes(result)


def validate_body_skeleton(generated, base):
    actual, expected = Model(generated, False), Model(base, False)
    if not math.isclose(actual.scale, expected.scale, abs_tol=1e-7):
        raise ValueError("The wearer's animation scale changed")
    if len(actual.nodes) < len(expected.nodes):
        raise ValueError("Ordinary body skeleton was truncated")
    # Some stock bodies have repeated impact helpers. Compare the entire
    # ordered body subtree instead of collapsing those nodes into a name map.
    for expected_node, actual_node in zip(expected.nodes[1:], actual.nodes[1:]):
        name, parent, part, controllers, _ = expected_node
        target_name, target_parent, target_part, target_controllers, _ = actual_node
        if name != target_name or parent != target_parent:
            raise ValueError(f"Missing or moved ordinary body attachment {name}")
        if target_part != part:
            raise ValueError(f"{name}: ordinary body animation ID changed")
        p = expected.nodes[parent][0] if parent else None
        tp = actual.nodes[target_parent][0] if target_parent else None
        if p != tp or controllers.keys() != target_controllers.keys():
            raise ValueError(f"{name}: ordinary body hierarchy/controllers changed")
        for key in controllers:
            times, values = controllers[key]
            tt, tv = target_controllers[key]
            if len(times) != len(tt) or len(values) != len(tv):
                raise ValueError(f"{name}: ordinary body bind controller changed")
            for left, right in zip(values, tv):
                equivalent = mdl.equivalent_quaternion(left, right) if key == 20 else all(
                    math.isclose(a, b, abs_tol=2e-6) for a, b in zip(left, right))
                if not equivalent:
                    raise ValueError(f"{name}: ordinary body bind transform changed")


def validate_garment_bindings(source, generated, names):
    if not mdl.binary(source):
        raise ValueError("Garment bind audit requires a compiled reference")
    original, target = Model(source, False), Model(generated, False)
    before, after = original.pose(), target.pose()
    for node, renamed in names.items():
        if node not in before:
            raise ValueError(f"{node}: no original garment bind reference")
        bp, bq, bs = before[node]
        ap, aq, az = after[renamed]
        if math.dist(ap, bp) > 2e-5 or not mdl.equivalent_quaternion(aq, bq) or abs(az-bs) > 2e-6:
            raise ValueError(f"{node}: garment bind pose changed")
    target_bones = target.skin_bindings()
    for mesh, bones in original.skin_bindings().items():
        actual = target_bones[names[mesh]]
        if {names[bone] for bone in bones} != set(actual):
            raise ValueError(f"{mesh}: garment skin bone references changed")
        for bone, (qsrc, tsrc) in bones.items():
            qdst, tdst = actual[names[bone]]
            if source[qsrc:qsrc+16] != generated[qdst:qdst+16] or source[tsrc:tsrc+12] != generated[tdst:tdst+12]:
                raise ValueError(f"{mesh}/{bone}: authored skin inverse bind changed")


def validate_body_poses(parent, base, library):
    """Compare each inherited clip at five phases, including weapon attachments."""
    original = library.model(base)
    clips, expected = library.clips(parent), library.clips(base)
    count = 0
    for name, clip in expected.items():
        if name not in clips or not math.isclose(clip[0], clips[name][0], abs_tol=2e-6):
            raise ValueError(f"{parent}/{name}: ordinary animation duration changed")
        for phase in (0, .25, .5, .75, 1):
            time = phase * clip[0]
            expected_scale = 1 if library.owners(base)[name] == base else original.scale
            actual, before = original.pose(clips[name], time, original.scale), original.pose(clip, time, expected_scale)
            for joint in before.keys() - {original.nodes[0][0]}:
                ap, aq, az = actual[joint]
                bp, bq, bz = before[joint]
                if math.dist(ap, bp) > 2e-5 or not mdl.equivalent_quaternion(aq, bq) or abs(az-bz) > 2e-6:
                    raise ValueError(f"{parent}/{name}/{joint}: body pose changed at {phase}")
            count += 1
    return count


class Library:
    def __init__(self, load):
        self.load = load

    @lru_cache(None)
    def model(self, name):
        data = self.load(name)
        if data is None:
            raise ValueError(f"Missing animation model {name}")
        return Model(data)

    @lru_cache(None)
    def clips(self, name):
        model = self.model(name)
        return {**(self.clips(model.parent) if model.parent else {}), **model.clips}

    @lru_cache(None)
    def owners(self, name):
        model = self.model(name)
        return {**(self.owners(model.parent) if model.parent else {}), **{clip:name for clip in model.clips}}

    def body_track_names(self, base, owner, clip):
        targets = {node[2]: node[0] for node in self.model(base).nodes if node[2] >= 0}
        return {node[0]: targets[node[2]] for node in self.model(owner).clips[clip][1]
                if node[2] in targets}
