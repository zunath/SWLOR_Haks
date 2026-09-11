"""Keep ordinary body attachments independent of a garment's authored rig."""
from __future__ import annotations

import hashlib
import json
import re

import CompileModels as mdl
import RobeAnimations as anim


def serialize(kind, name, props):
    lines = [f"node {kind} {name}"]
    for key, value in props.items():
        if key in mdl.ARRAY_PROPERTIES or key.endswith("key"):
            lines.append(f"  {key} {len(value)}")
            lines.extend("    " + " ".join(row) for row in value)
        else:
            lines.append(f"  {key} " + " ".join(value))
    return "\n".join([*lines, "endnode\n"])


def hierarchy(text):
    owner = anim.model_name(text)
    nodes = {n: (kind, props) for kind, n, props in anim.geometry(text)}
    paths = {owner: ()}
    def path(name):
        if name not in paths:
            paths[name] = (*path(nodes[name][1]["parent"][0].lower()), name)
        return paths[name]
    for name in nodes:
        path(name)
    return nodes, paths


class Families:
    """Share complete animation parents among robes with the same clip sources.

    Body and garment tracks have distinct native IDs. Garments with a different
    shoulder/hand hierarchy also receive distinct paths inside the parent.
    """

    def __init__(self, load, body_track_names=None):
        self.load = load
        self.body_track_names = body_track_names
        self.resolved = {}
        self.parsed = {}
        self.groups = {}
        self.members = {}
        self.fallbacks = {}

    def clips(self, name):
        if name not in self.resolved:
            text = self.load(name)
            if text is None:
                self.resolved[name] = {}
            else:
                parent = mdl.supermodel(text.encode("latin1"))
                self.resolved[name] = {**(self.clips(parent) if parent else {}),
                                       **anim.resolved_animations([(name, text)])}
        return self.resolved[name]

    def clip_nodes(self, owner, clip):
        key = (owner, clip[1].lower())
        if key not in self.parsed:
            self.parsed[key] = {n: props for _, n, props in mdl.parse_nodes(clip[3])}
        return self.parsed[key]

    def tracks(self, source, owner, names, duration, position_scale=1, remap=None):
        if source is None:
            return {}
        length = float(re.search(r"(?im)^\s*length\s+(\S+)", source[3])[1])
        factor = duration/length if length else 1
        result = {}
        for node, props in self.clip_nodes(owner, source).items():
            if remap is not None:
                node = remap.get(node)
            if node == owner or node not in names:
                continue
            values = {}
            for key, value in props.items():
                if key in anim.TRANSFORMS:
                    values[key] = value
                elif key.endswith("key"):
                    if key.removesuffix("key") not in anim.TRANSFORMS:
                        raise ValueError(f"{owner}/{node}: unsupported garment controller {key}")
                    values[key] = [[format(float(row[0])*factor, '.9g'), *row[1:]] for row in value]
            if position_scale != 1:
                if "position" in values:
                    values["position"] = [format(float(v)*position_scale, '.9g') for v in values["position"]]
                if "positionkey" in values:
                    values["positionkey"] = [[row[0], *[format(float(v)*position_scale, '.9g') for v in row[1:]]]
                                             for row in values["positionkey"]]
            # The native compiler represents a single time-zero transform as
            # a static controller. Emit that representation before auditing
            # the round trip, while retaining every actual animated curve.
            for key in list(values):
                rows = values[key]
                if key.endswith("key") and len(rows) == 1 and float(rows[0][0]) == 0:
                    values[key.removesuffix("key")] = values.pop(key)[0][1:]
            result[node] = values
        return result

    def add(self, base, robe, resource_name=None):
        name = resource_name or anim.model_name(robe)
        body_clips, garment_clips = self.clips(base), self.clips(name)
        missing = []
        for owner, text in anim.chain(name, self.load):
            parent = mdl.supermodel(text.encode("latin1"))
            if parent and self.load(parent) is None:
                missing.append(parent)
        # An unresolved source cannot supply animation. Use the body's complete
        # clips for those absent tracks and record the repair in the audit.
        if missing:
            self.fallbacks[name] = missing
        resolved = {**body_clips, **garment_clips}
        # The carry suppression overlay is deliberately controller-free. It is
        # inherited from the common tail, so it must not version every garment
        # family or acquire body/garment tracks during bridge generation.
        if "sw_nohold" in resolved:
            owner, overlay = resolved["sw_nohold"]
            length = re.search(r"(?im)^\s*length\s+(\S+)", overlay[3])
            nodes = mdl.parse_nodes(overlay[3])
            if (length is None or float(length[1]) != 1 or not nodes or
                    re.search(r"(?im)^\s*event\b", overlay[3]) or
                    any(kind != "dummy" or set(props) - {"parent"} for kind, _, props in nodes)):
                raise ValueError(f"{owner}/sw_nohold: carry overlay must have no controllers or events")
            resolved.pop("sw_nohold")
        signature = json.dumps([(c, o) for c, (o, _) in sorted(resolved.items())])
        key = base + "/" + hashlib.sha256(signature.encode()).hexdigest()[:16]
        group = self.groups.setdefault(key, {"base": base, "clips": resolved, "paths": {}, "members": []})
        nodes, paths = hierarchy(robe)
        animated = set()
        for clip, (owner, source) in resolved.items():
            animated.update(self.clip_nodes(owner, source))
            if clip in body_clips:
                body_owner, body_source = body_clips[clip]
                animated.update(self.clip_nodes(body_owner, body_source))
        required = {()}
        for node in animated & nodes.keys():
            p = paths[node]
            required.update(p[:i] for i in range(len(p)+1))
        by_path = {p: nodes[n][1] for n, p in paths.items()}
        for p in required:
            group["paths"].setdefault(p, by_path[p])
        group["members"].append(name)
        self.members[name] = (key, nodes, paths)
        return key

    def aliases(self, key):
        group = self.groups[key]
        if "aliases" not in group:
            aliases, used = {(): "rg_model"}, {"rg_model"}
            for path in sorted(group["paths"]):
                if not path:
                    continue
                name = "rg_" + path[-1]
                suffix = 1
                while name in used:
                    suffix += 1
                    name = f"rg_{path[-1]}_{suffix}"
                if len(name) > 31:
                    raise ValueError(f"Garment joint name exceeds engine limit: {name}")
                aliases[path] = name
                used.add(name)
            group["aliases"] = aliases
        return group["aliases"]

    def body_root(self, robe_name, generated, parent, aliases=None):
        key, garment, paths = self.members[robe_name]
        group = self.groups[key]
        aliases = self.aliases(key) if aliases is None else aliases
        base = group["base"]
        renamed = {node: aliases.get(path, "rm_" + node) for node, path in paths.items()}
        if len(set(renamed.values())) != len(renamed) or any(len(n) > 31 for n in renamed.values()):
            raise ValueError(f"{robe_name}: ambiguous garment node names")
        output = []
        for kind, node, props in anim.geometry(self.load(base)):
            props = dict(props)
            if props.get("parent") == [base]:
                props["parent"] = [generated]
            output.append(serialize(kind, generated if node == base else node, props))
        for node, (kind, props) in garment.items():
            props = dict(props)
            props["parent"] = [generated if not paths[node] else renamed[props["parent"][0].lower()]]
            if "weights" in props:
                props["weights"] = [[renamed[value.lower()] if i % 2 == 0 else value
                                      for i, value in enumerate(row)] for row in props["weights"]]
            output.append(serialize(kind, renamed[node], props))
        scale = re.search(r"(?im)^\s*setanimationscale\s+(\S+)", self.load(base))
        result = (f"newmodel {generated}\nsetsupermodel {generated} {parent}\nclassification CHARACTER\n"
                  f"setanimationscale {scale[1] if scale else '1'}\nbeginmodelgeom {generated}\n" +
                  "".join(output) + f"endmodelgeom {generated}\ndonemodel {generated}\n")
        return result.encode("latin1"), renamed

    def clip_tracks(self, key, clip, body_nodes, scale):
        """Resolve exact body/garment controllers for both ordinary and shared rigs."""
        group = self.groups[key]
        base = group["base"]
        owner, source = group["clips"][clip]
        body_owner, body_source = self.clips(base).get(clip, (None, None))
        primary = body_source if body_source is not None else source
        header = primary[3][:anim.NODE.search(primary[3]).start()]
        duration = float(re.search(r"(?im)^\s*length\s+(\S+)", header)[1])
        # Locally authored translations need compensation when inherited by a
        # scaled wearer. Retain native numeric part-ID remapping for body tracks.
        body_factor = 1/scale if body_owner == base else 1
        remap = self.body_track_names(base, body_owner, clip) if self.body_track_names and body_source else None
        body = self.tracks(body_source, body_owner, body_nodes, duration, body_factor, remap)
        original_names = {path[-1] for path in group["paths"] if path}
        garment = self.tracks(body_source, body_owner, original_names, duration, body_factor)
        garment_factor = 1/scale if owner == base or owner in group["members"] else 1
        for node, values in self.tracks(source, owner, original_names, duration, garment_factor).items():
            target = garment.setdefault(node, {})
            for controller, value in values.items():
                target.pop(controller.removesuffix("key") if controller.endswith("key") else controller+"key", None)
                target[controller] = value
        # Common body clips retain their original duration/events/controllers;
        # custom-only clips may also supply body motion.
        if body_source is None:
            body = self.tracks(source, owner, body_nodes, duration)
        return header, body, garment

    def bridge(self, key, name):
        group, aliases = self.groups[key], self.aliases(key)
        base = group["base"]
        scale_match = re.search(r"(?im)^\s*setanimationscale\s+(\S+)", self.load(base))
        scale = float(scale_match[1]) if scale_match else 1
        if scale <= 0:
            raise ValueError(f"{base}: invalid body animation scale")
        body_nodes = {n: props for _, n, props in anim.geometry(self.load(base))}
        nodes = {}
        for node, props in body_nodes.items():
            props = {k: v for k, v in props.items() if k in anim.TRANSFORMS or k == "parent"}
            props["parent"] = [props["parent"][0].lower()]
            if props.get("parent") == [base]:
                props["parent"] = [name]
            nodes[name if node == base else node] = props
        for path, alias in aliases.items():
            props = group["paths"][path]
            nodes[alias] = {k: v for k, v in props.items() if k in anim.TRANSFORMS}
            nodes[alias]["parent"] = [aliases[path[:-1]] if path else name]
        ordered = anim.order_nodes(nodes, name)
        output = (f"newmodel {name}\nsetsupermodel {name} {base}\nclassification CHARACTER\n"
                  f"setanimationscale 1\nbeginmodelgeom {name}\n" +
                  "".join(anim.serialize_node(n, p) for n, p in ordered) + f"endmodelgeom {name}\n")
        for clip in sorted(group["clips"]):
            header, body, garment = self.clip_tracks(key, clip, body_nodes, scale)
            values = {node: {"parent": props["parent"], **body.get(node, {})} for node, props in ordered}
            for path, alias in aliases.items():
                values[alias] = {"parent": nodes[alias]["parent"], **(garment.get(path[-1], {}) if path else {})}
            required = {name}
            for node, props in values.items():
                if len(props) == 1:
                    continue
                while node not in required:
                    required.add(node)
                    node = nodes[node]["parent"][0]
            output += f"newanim {clip} {name}\n{header}" + "".join(
                anim.serialize_node(node, values[node]) for node, _ in ordered if node in required) + f"doneanim {clip} {name}\n"
        return (output + f"donemodel {name}\n").encode("latin1")
