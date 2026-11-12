"""Append rifle clips to an owned robe rig without changing its cloth or old banks."""
import re
import struct

import FillRobeEmoteMotion as native
import RobeAnimationBanks as banks
import RobePoseAudit as poses

PROFILE = re.compile(r"rf[0-9]+_[0-9]+_(i|r|s|b|a(?:_in|_out)?)\Z")
TEMPLATES = {"i": "xbowr", "r": "xbowrdy", "s": "xbowshot", "b": "pause1",
             "a": "sw_aimedshot", "a_in": "sw_aimedshot_in", "a_out": "sw_aimedshot_out"}


def nodes(root):
    result = {}

    def visit(node):
        if node.name in result:
            raise ValueError("Duplicate native animation joint: " + node.name)
        result[node.name] = node
        for child in node.children:
            visit(child)

    visit(root)
    return result


def validate_controllers(node):
    if len(node.keys) % 12 or len(node.values) % 4:
        raise ValueError("Malformed native body controller arrays")
    count = len(node.values) // 4
    for at in range(0, len(node.keys), 12):
        kind, rows, times, values, columns = struct.unpack_from("<IHHHB", node.keys, at)
        if (kind not in {8, 20, 36} or columns != {8: 3, 20: 4, 36: 1}[kind]
                or times + rows > count or values + rows * columns > count):
            raise ValueError("Invalid native body controller")



def node_paths(root):
    result = {}

    def visit(node, path):
        if path in result:
            raise ValueError("Duplicate native robe clip path")
        result[path] = node
        for child in node.children:
            visit(child, path + (child.name,))

    visit(root, ())
    return result


def geometry_paths(model):
    paths, result = [], {}
    for index, (name, parent, part, _, _) in enumerate(model.nodes):
        path = () if parent is None else paths[parent] + (name,)
        paths.append(path)
        if path in result:
            if model.nodes[result[path]][2] != part:
                raise ValueError("Ambiguous native body geometry path")
        else:
            result[path] = index
    return paths, result


def fitted_clip(template, fitted, geometry, native_body, model_name):
    """Mirror the bare body's numeric/named lookup while preserving own cloth."""
    header, _, root = native.read_clip(template)
    source_header, events, source_root = native.read_clip(fitted)
    target_nodes = node_paths(root)
    geometry_order, indices = geometry_paths(geometry)
    body_order, body_indices = geometry_paths(native_body)
    numbered, named = {}, {}
    for path, index in body_indices.items():
        name, _, part, _, _ = native_body.nodes[index]
        if part >= 0:
            if part in numbered and numbered[part] != path:
                raise ValueError("Native body part is assigned to different joints")
            numbered[part] = path
        else:
            named.setdefault(name, []).append(path)

    def ensure(path):
        if path in target_nodes:
            return target_nodes[path]
        if path not in indices:
            raise ValueError("The owned robe lacks an animated native body joint")
        if not path:
            return root
        ancestor = ensure(path[:-1])
        index = indices[path]
        joint, _, part, _, _ = geometry.nodes[index]
        child = native.Node(native._header(joint, part))
        ancestor.children.append(child)
        target_nodes[path] = child
        return child

    for path in body_indices:
        if path in target_nodes:
            target_nodes[path].keys = target_nodes[path].values = b""
    ignored = []
    for path, node in node_paths(source_root).items():
        validate_controllers(node)
        part = struct.unpack_from("<i", node.header, 28)[0]
        if node is source_root:
            targets = [()]
        elif part >= 0:
            targets = [numbered[part]] if part in numbered else []
        else:
            targets = named.get(node.name, [])
        if not targets:
            if node.keys:
                ignored.append(node.name + ":" + str(part))
            continue
        for target_path in targets:
            target = ensure(target_path)
            index = indices[target_path]
            target_part = geometry.nodes[index][2]
            if target_part != part:
                raise ValueError("Owned robe and native body disagree on an animated part")
            changed = bytearray(target.header)
            struct.pack_into("<i", changed, 28, target_part)
            target.header = bytes(changed)
            target.keys, target.values = node.keys, node.values

    changed = bytearray(header)
    changed[8:72] = source_header[8:72]
    changed[112:120] = source_header[112:120]
    return native.write_clip(bytes(changed), events, root, model_name), sorted(set(ignored))


def robe_templates(before, load=None):
    """Resolve own-cloth templates only through byte-compatible robe ancestors."""
    original = banks.CompiledBridge(before)
    current, seen, result = original, set(), {}
    while True:
        if current.name in seen:
            raise ValueError("Cyclic owned robe template chain")
        seen.add(current.name)
        if (current.pack(original.name, original.parent, []) !=
                original.pack(original.name, original.parent, [])):
            raise ValueError("Inherited robe template changes the owned skeleton")
        for name, component in zip(current.clip_names, current.clips):
            if name not in result:
                result[name] = banks.Component(
                    0, bytes(component.relocated(0, original.name)),
                    component.pointers, component.names)
        if not re.fullmatch(r"p[fm][hedo]_ra[0-9]{3}", current.parent):
            break
        if load is None:
            raise ValueError("An owned robe ancestor loader is required")
        data = load(current.parent)
        if data is None:
            raise ValueError("Missing owned robe template ancestor: " + current.parent)
        parent = banks.CompiledBridge(data)
        if parent.name != current.parent:
            raise ValueError("Owned robe template loader returned the wrong model")
        current = parent
    return result

def build_layer(before, fitted_body, name, load=None, *, native_body=None):
    """Build a new layer inheriting the unchanged prior head and every old clip."""
    old, body = banks.CompiledBridge(before), banks.CompiledBridge(fitted_body)
    if not re.fullmatch(r"p[fm][hedo]_ra[0-9]{3}", name) or name == old.name:
        raise ValueError("A robe rifle layer requires a fresh playable bank name")
    if body.name != "a_rfl_" + old.name[:3] + "0":
        raise ValueError("Robe and fitted rifle body differ")
    if native_body is None:
        raise ValueError("Robe rifle layers require the actual native body skeleton")
    geometry = poses.Model(before, False)
    native_geometry = poses.Model(native_body, False)
    templates = robe_templates(before, load)
    output, inherited, ignored = {}, [], set()
    for clip_name, component in zip(body.clip_names, body.clips):
        match = PROFILE.fullmatch(clip_name)
        if match is None:
            if clip_name.startswith("rf"):
                raise ValueError("Invalid rifle profile clip: " + clip_name)
            continue
        if clip_name in templates:
            inherited.append(clip_name)
            continue
        template_name = TEMPLATES[match[1]]
        if template_name not in templates:
            raise ValueError(old.name + " lacks the own-cloth template " + template_name)
        output[clip_name], omitted = fitted_clip(
            templates[template_name], component, geometry, native_geometry, old.name)
        ignored.update(omitted)
    if not output:
        raise ValueError("The fitted rifle body adds no new robe clips")
    layer = old.pack(name, old.name, [output[key] for key in sorted(output)])
    actual = banks.CompiledBridge(layer)
    if (actual.parent != old.name or
            actual.pack(old.name, old.parent, []) != old.pack(old.name, old.parent, [])):
        raise ValueError("Rifle layer changed the robe skeleton, binds or model metadata")
    if actual.clip_names != sorted(output):
        raise ValueError("Rifle layer omitted or duplicated a fitted clip")
    import RobeAnimations
    RobeAnimations.validate_animation_parts(layer)
    return layer, {"sourceHead": old.name, "layer": name, "addedClips": len(output),
                   "inheritedRifleClips": sorted(inherited),
                   "ignoredUnboundBodyHelpers": sorted(ignored)}

def rebuild(manifest, root):
    """Yield exact owned layers from pinned old banks and the installed fitted body."""
    import functools
    import hashlib
    import subprocess

    def sha(data):
        return hashlib.sha256(data).hexdigest()

    if manifest.get("version") != 1:
        raise ValueError("Unsupported robe rifle profile recipe")
    sources = {record["head"]: record for record in manifest["sources"]}
    if len(sources) != len(manifest["sources"]):
        raise ValueError("Duplicate owned robe source")

    def path_for(name):
        if not banks.is_bank_name(name):
            raise ValueError("Invalid owned robe bank resource")
        return ("sw_anim_f" if name[1] == "f" else "sw_anim_m") + "/" + name + ".mdl"

    @functools.lru_cache(maxsize=4)
    def load_source(head):
        record = sources[head]
        raw_parts = []
        for index, part in enumerate(record["parts"]):
            expected = head + (f"_b{index:02d}" if index else "")
            if part["name"] != expected or not re.fullmatch("[0-9a-f]{40}", part["blob"]):
                raise ValueError("Invalid pinned robe source partition")
            raw = subprocess.check_output(
                ["git", "-C", str(root), "cat-file", "blob", part["blob"]])
            if sha(raw) != part["sha256"] or (root / path_for(expected)).read_bytes() != raw:
                raise ValueError("The immutable owned robe source changed")
            raw_parts.append(raw)
        raw = banks.join(raw_parts) if len(raw_parts) > 1 else raw_parts[0]
        if sha(raw) != record["sha256"] or banks.CompiledBridge(raw).name != head:
            raise ValueError("Pinned robe sources do not reproduce the owned head")
        return raw

    @functools.lru_cache(maxsize=1)
    def load_body(body):
        if not re.fullmatch(r"p[fm][hedo]0", body):
            raise ValueError("Robe rifle profiles require a normal playable organic body")
        record = manifest["bodies"][body]
        raw_parts = []
        for index, part in enumerate(record["parts"]):
            expected = "sw_cr_creature/a_rfl_" + body + (f"_b{index:02d}" if index else "") + ".mdl"
            if part["file"] != expected:
                raise ValueError("Invalid fitted body partition")
            raw = (root / expected).read_bytes()
            if sha(raw) != part["sha256"]:
                raise ValueError("Fitted rifle body changed after robe qualification")
            raw_parts.append(raw)
        raw = banks.join(raw_parts) if len(raw_parts) > 1 else raw_parts[0]
        if sha(raw) != record["sha256"]:
            raise ValueError("Fitted body partitions do not reproduce the complete program")
        return raw

    @functools.lru_cache(maxsize=1)
    def load_native_body(body):
        source = manifest["bodies"][body]["nativeBody"]
        if source["model"] != body or not re.fullmatch("[0-9a-f]{40}", source["blob"]):
            raise ValueError("Invalid pinned native robe wearer")
        raw = subprocess.check_output(["git", "-C", str(root), "cat-file", "blob", source["blob"]])
        if sha(raw) != source["sha256"] or (root / "sw_pt_root" / (body + ".mdl")).read_bytes() != raw:
            raise ValueError("Native robe wearer changed after qualification")
        if poses.Model(raw, False).nodes[0][0] != body:
            raise ValueError("Pinned native robe wearer has the wrong identity")
        return raw

    seen = set()
    for record in manifest["models"]:
        head, name, body = record["sourceHead"], record["head"], record["body"]
        if name in seen or name in sources or body != head[:3] + "0":
            raise ValueError("Robe rifle layers require unique fresh matching heads")
        seen.add(name)
        result, proof = build_layer(load_source(head), load_body(body), name, load_source,
                                    native_body=load_native_body(body))
        if sha(result) != record["sha256"]:
            raise ValueError("Robe rifle layer differs from its pinned program")
        parts = banks.split(result)
        banks.validate_split(result, list(parts.values()))
        if list(parts) != [part["name"] for part in record["parts"]]:
            raise ValueError("Robe rifle partition inventory changed")
        outputs = {}
        for part in record["parts"]:
            data = bytes(parts[part["name"]])
            if len(data) >= banks.LIMIT_BYTES or sha(data) != part["sha256"]:
                raise ValueError("Robe rifle partition differs from its bounded pinned output")
            outputs[path_for(part["name"])] = data
        yield record, outputs, proof


def main():
    import argparse
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = json.loads((root / "tools/RobeRifleProfiles.json").read_text())
    count = 0
    for record, outputs, _ in rebuild(manifest, root):
        for relative, data in outputs.items():
            if args.check:
                if (root / relative).read_bytes() != data:
                    raise ValueError("Stale owned robe rifle layer: " + relative)
            else:
                target = args.output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        count += 1
        print("Verified robe rifle layer " + record["head"], flush=True)
    print(f"Robe rifle layers: {count} exact owned programs.", flush=True)


if __name__ == "__main__":
    main()
