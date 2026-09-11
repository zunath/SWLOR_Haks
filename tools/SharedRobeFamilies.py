"""Share one native body track set while retaining independent authored garment rigs."""
from __future__ import annotations

import hashlib
import json
import re

import RobeAnimations as anim
import RobeSkeleton as skeleton


def signature(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("latin1")


class Families:
    """Group compatible garments by body, sharing only identical joint behavior.

    Add all members of a body before requesting its bridge or wearable roots.
    Canonical identity includes the original joint name, its canonical parent,
    and every resolved animation controller. Each wearer keeps its own local
    defaults and skin binds; they need not match the shared parent's defaults.
    Different names within a wearer never collapse into one skin-binding node.
    """

    def __init__(self, load, body_track_names=None):
        self.load = load
        self.legacy = skeleton.Families(load, body_track_names)
        self.groups = {}
        self.members = {}
        self.fallbacks = self.legacy.fallbacks
        self.member_aliases = {}
        self.prepared = {}

    def add(self, base, robe, resource_name=None):
        key = base + "/shared"
        if key in self.prepared:
            raise ValueError(f"{base}: add every garment before materializing its shared rig")
        name = resource_name or anim.model_name(robe)
        if name in self.members:
            raise ValueError(f"Garment is already registered: {name}")
        old_key = self.legacy.add(base, robe, resource_name)
        group = self.groups.setdefault(key, {"base": base, "members": [], "legacy_keys": set()})
        group["members"].append(name)
        group["legacy_keys"].add(old_key)
        _, nodes, paths = self.legacy.members[name]
        self.members[name] = (key, nodes, paths)
        return key

    def legacy_aliases(self, robe_name):
        """Original garment name -> previous Families alias, for independent audits."""
        key, _, paths = self.legacy.members[robe_name]
        aliases = self.legacy.aliases(key)
        return {node: aliases.get(path, "rm_" + node) for node, path in paths.items()}

    def _prepare(self, key):
        if key in self.prepared:
            return self.prepared[key]
        group = self.groups[key]
        base = group["base"]
        text = self.load(base)
        body_nodes = {name: props for _, name, props in anim.geometry(text)}
        match = re.search(r"(?im)^\s*setanimationscale\s+(\S+)", text)
        scale = float(match[1]) if match else 1
        if scale <= 0:
            raise ValueError(f"{base}: invalid body animation scale")
        old_keys = sorted(group["legacy_keys"])
        clips = sorted(self.legacy.groups[old_keys[0]]["clips"])
        body_signatures = {}
        motion_signatures = {}
        for old_key in old_keys:
            old = self.legacy.groups[old_key]
            if sorted(old["clips"]) != clips:
                raise ValueError(f"{base}: incompatible shared animation inventories")
            original_names = {path[-1] for path in old["paths"] if path}
            hashes = {name: hashlib.sha256() for name in original_names}
            for clip in clips:
                header, body, garment = self.legacy.clip_tracks(old_key, clip, body_nodes, scale)
                expected = hashlib.sha256(signature((header, body))).digest()
                if body_signatures.setdefault(clip, expected) != expected:
                    raise ValueError(f"{base}/{clip}: incompatible shared body controllers or clip headers")
                for name, digest in hashes.items():
                    digest.update(signature((clip, garment.get(name, {}))))
            motion_signatures[old_key] = {name: value.digest() for name, value in hashes.items()}

        canonical, joints = {}, {}
        used = set(body_nodes)

        def allocate(leaf):
            stem = "rg_" + leaf
            index = 1
            while True:
                suffix = "" if index == 1 else f"_{index}"
                name = stem[:31-len(suffix)] + suffix
                if name not in used:
                    used.add(name)
                    return name
                index += 1

        for robe_name in sorted(group["members"]):
            old_key, garment, paths = self.legacy.members[robe_name]
            by_path = {path: node for node, path in paths.items()}
            required = self.legacy.groups[old_key]["paths"].keys() & by_path.keys()
            aliases = {}
            for path in sorted(required, key=lambda value: (len(value), value)):
                node = by_path[path]
                bind = {field: value for field, value in garment[node][1].items() if field in anim.TRANSFORMS}
                parent = aliases[path[:-1]] if path else None
                motion = motion_signatures[old_key][node] if path else b""
                # Every garment has a differently named model root. Its neutral
                # role may be shared, but ordinary joints also require the same
                # original name so no two wearer nodes lose separate native IDs.
                # Inherited controllers replace channels on the wearer's own
                # geometry. Its unanimated channels retain that wearer's bind,
                # so differing local defaults do not require duplicate tracks.
                # Sorted members choose a deterministic parent-geometry bind.
                identity = (bool(path), node if path else "", parent, motion)
                if identity not in canonical:
                    alias = allocate(node if path else "model")
                    canonical[identity] = alias
                    joints[alias] = {"bind": bind, "parent": parent, "source": (old_key, node if path else None)}
                aliases[path] = canonical[identity]
            if len(set(aliases.values())) != len(aliases):
                raise ValueError(f"{robe_name}: shared rig merged distinct garment nodes")
            self.member_aliases[robe_name] = aliases
        result = {"body_nodes": body_nodes, "scale": scale, "clips": clips,
                  "legacy_keys": old_keys, "joints": joints}
        self.prepared[key] = result
        return result

    def body_root(self, robe_name, generated, parent):
        key = self.members[robe_name][0]
        self._prepare(key)
        return self.legacy.body_root(robe_name, generated, parent, aliases=self.member_aliases[robe_name])

    def stats(self):
        """Materialize and summarize union sizes before native compilation."""
        return {"shared_groups": len(self.groups), "bases": [
            {"base": self.groups[key]["base"],
             "legacy_families": len(self.groups[key]["legacy_keys"]),
             "wearers": len(self.groups[key]["members"]),
             "canonical_garment_nodes": len(self._prepare(key)["joints"]),
             "old_garment_paths": sum(len(self.legacy.groups[old]["paths"])
                                      for old in self.groups[key]["legacy_keys"])}
            for key in sorted(self.groups)]}

    def bridge(self, key, name):
        prepared = self._prepare(key)
        base = self.groups[key]["base"]
        nodes = {}
        for node, props in prepared["body_nodes"].items():
            props = {field: value for field, value in props.items() if field in anim.TRANSFORMS or field == "parent"}
            props["parent"] = [props["parent"][0].lower()]
            if props["parent"] == [base]:
                props["parent"] = [name]
            nodes[name if node == base else node] = props
        for alias, joint in prepared["joints"].items():
            nodes[alias] = {**joint["bind"], "parent": [joint["parent"] or name]}
        ordered = anim.order_nodes(nodes, name)
        output = [f"newmodel {name}\nsetsupermodel {name} {base}\nclassification CHARACTER\n"
                  f"setanimationscale 1\nbeginmodelgeom {name}\n",
                  *(anim.serialize_node(node, props) for node, props in ordered), f"endmodelgeom {name}\n"]
        for clip in prepared["clips"]:
            garment_tracks = {}
            header = body = None
            for old_key in prepared["legacy_keys"]:
                old_header, old_body, garment_tracks[old_key] = self.legacy.clip_tracks(
                    old_key, clip, prepared["body_nodes"], prepared["scale"])
                if header is None:
                    header, body = old_header, old_body
            values = {node: {"parent": props["parent"], **body.get(node, {})} for node, props in ordered}
            for alias, joint in prepared["joints"].items():
                source, original = joint["source"]
                values[alias] = {"parent": nodes[alias]["parent"],
                                 **(garment_tracks[source].get(original, {}) if original is not None else {})}
            required = {name}
            for node, props in values.items():
                if len(props) == 1:
                    continue
                while node not in required:
                    required.add(node)
                    node = nodes[node]["parent"][0]
            output.extend([f"newanim {clip} {name}\n{header}",
                           *(anim.serialize_node(node, values[node]) for node, _ in ordered if node in required),
                           f"doneanim {clip} {name}\n"])
        output.append(f"donemodel {name}\n")
        return "".join(output).encode("latin1")
