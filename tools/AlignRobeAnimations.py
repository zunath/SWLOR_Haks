#!/usr/bin/env python3
"""Align original robe skeleton motion with the current humanoid body chain.

Stock clothing parents can use a custom animation slot for a different gesture
than SWLOR. Keep their garment helpers, geometry and bindings, but use the body's
timing and joint controllers for clips present in both chains.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import struct

import CompileModels as mdl
import GenerateTintMapAssets as tint
import RobeAnimations as anim
import RobePoseAudit as poses
import RobeSkeleton as skeleton

ROOT = Path(__file__).resolve().parents[1]
ROBE = re.compile(r"p[fm][a-z]0_robe\d{3}")
CHANNELS = anim.TRANSFORMS | {field + "key" for field in anim.TRANSFORMS}


def remap_inherited_parts(data, old_parent, new_parent):
    """Update descendant IDs without re-exporting mesh or inverse-bind data."""
    expected = anim.binary_parts(new_parent)
    if mdl.binary(old_parent):
        previous = anim.binary_parts(old_parent)
    else:
        _, paths = skeleton.hierarchy(old_parent.decode("latin1"))
        previous = {path: None for path in paths.values()}
    actual = anim.binary_parts(data)
    remap = {}
    for path, part in actual.items():
        if not path or part < 0 or path not in previous or path not in expected:
            continue
        if previous[path] is not None and part != previous[path]:
            continue
        target = expected[path]
        if target < 0:
            continue
        if remap.setdefault(part, target) != target:
            raise ValueError(f"Ambiguous inherited part mapping: {path}")
    # An inherited cloth ID can move into a descendant's private ID range.
    # Reallocate only those private collisions, including their local tracks.
    occupied = set(remap.values())
    next_part = max([*actual.values(), *expected.values(), 0]) + 1
    for part in sorted(set(actual.values()) - remap.keys() - {-1}):
        if part in occupied:
            remap[part] = next_part
            next_part += 1
    result = bytearray(data)
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    def visit(pointer):
        offset = 12 + pointer
        part = struct.unpack_from("<i", data, offset + 28)[0]
        if part in remap:
            struct.pack_into("<i", result, offset + 28, remap[part])
        for index in range(uint(offset + 76)):
            visit(uint(12 + uint(offset + 72) + index * 4))
    visit(uint(84))
    for index in range(uint(136)):
        animation = 12 + uint(12 + uint(132) + index * 4)
        visit(uint(animation + 72))
    return bytes(result)


def clip_values(match, owner):
    """Compare native motion numerically, allowing compiler float rounding."""
    header = match[3][:anim.NODE.search(match[3]).start()]
    number = lambda key: float(re.search(r"(?im)^\s*" + key + r"\s+(\S+)", header)[1])
    roots = {name for _, name, props in mdl.parse_nodes(match[3]) if props["parent"][0].lower() == "null"}
    animroot = re.search(r"(?im)^\s*animroot\s+(\S+)", header)[1].lower()
    if animroot in roots:
        animroot = owner
    nodes = {}
    for _, name, props in mdl.parse_nodes(match[3]):
        controllers = {}
        for field, values in props.items():
            if field not in CHANNELS:
                continue
            kind = field.removesuffix("key")
            rows = values if field.endswith("key") else [[0, *values]]
            curves = []
            for row in rows:
                value = tuple(float(v) for v in row[1:])
                if kind == "orientation":
                    value = mdl.quaternion(value)
                    sign = next((v for v in reversed(value) if abs(v) > 1e-8), 1)
                    if sign < 0:
                        value = tuple(-v for v in value)
                curves.append((float(row[0]), value))
            controllers[kind] = curves
        if controllers:
            nodes[owner if name in roots else name] = controllers
    return (number("length"), number("transtime"), animroot,
            [(float(time), event.lower()) for time, event in re.findall(r"(?im)^\s*event\s+(\S+)\s+(\S+)", header)], nodes)


def same_values(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=0, abs_tol=2e-6)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_values(a[key], b[key]) for key in a)
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(same_values(x, y) for x, y in zip(a, b))
    return a == b


def align(text, body, load):
    """Return corrected source and changed clips; preserve independent cloth keys."""
    family = skeleton.Families(load)
    body_clips = family.clips(body)
    geometry = {name: {**props, "parent": [props["parent"][0].lower()]}
                for _, name, props in anim.geometry(text)}
    owner = anim.model_name(text)
    scale_match = re.search(r"(?im)^\s*setanimationscale\s+(\S+)", load(body))
    scale = float(scale_match[1]) if scale_match else 1
    changes, revised = [], []
    for match in anim.ANIMATION.finditer(text):
        clip = match[1].lower()
        if clip not in body_clips:
            continue
        body_owner, body_source = body_clips[clip]
        header = body_source[3][:anim.NODE.search(body_source[3]).start()]
        header = re.sub(r"(?im)^(\s*animroot\s+)" + re.escape(body_owner) + r"\s*$",
                        lambda value: value[1] + owner, header)
        duration = float(re.search(r"(?im)^\s*length\s+(\S+)", header)[1])
        factor = 1 / scale if body_owner == body else 1
        target = family.tracks(body_source, body_owner, anim.BODY_MOTION_NODES, duration, factor)
        garment = family.tracks(match, owner, set(geometry) - anim.BODY_MOTION_NODES, duration)
        original = mdl.parse_nodes(match[3])
        roots = {name for _, name, props in original if props.get("parent", [""])[0].lower() == "null"}
        values = {}
        for kind, name, props in original:
            props = dict(props)
            props["parent"] = [props["parent"][0].lower()]
            if props.get("parent", [""])[0].lower() in roots:
                props["parent"] = [owner]
            values[owner if name in roots else name] = (kind, props)
        for name in anim.BODY_MOTION_NODES & values.keys():
            kind, props = values[name]
            values[name] = (kind, {field: value for field, value in props.items() if field not in CHANNELS})
        for name, controllers in {**garment, **target}.items():
            if name not in geometry or not controllers:
                continue
            required = name
            while required not in values:
                props = geometry[required]
                values[required] = ("dummy", {"parent": props["parent"]})
                required = props["parent"][0].lower()
                if required == "null":
                    break
            kind, props = values[name]
            # Remove both keyed and static versions before copying a channel.
            for field, value in controllers.items():
                props.pop(field.removesuffix("key") if field.endswith("key") else field + "key", None)
                props[field] = value
        try:
            ordered = anim.order_nodes({name: props for name, (_, props) in values.items()}, owner)
        except ValueError as error:
            raise ValueError(f"{owner}/{clip}: {error}") from error
        result = f"newanim {clip} {owner}\n{header.rstrip()}\n" + "".join(
            skeleton.serialize(values[name][0], name, props) for name, props in ordered)
        result += f"doneanim {clip} {owner}"
        if not same_values(clip_values(match, owner), clip_values(next(anim.ANIMATION.finditer(result)), owner)):
            changes.append((match.start(), match.end(), result))
            revised.append(clip)
    for start, end, result in reversed(changes):
        text = text[:start] + result + text[end:]
    return text, revised


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stage", type=Path, default=ROOT / "output" / "robe-animation-alignment")
    args = parser.parse_args()
    stage = args.stage.resolve()
    for folder in ("original", "source", "binary", "decompiled"):
        (stage / folder).mkdir(parents=True, exist_ok=True)
    compiler, _ = mdl.prepare_compiler(stage)
    active, stock = tint.find_active_models(), tint.read_stock_key_models(args.game_data)
    dependencies, missing = {}, set()

    def load(name):
        if not name or name in dependencies or name in missing:
            return
        if name in active:
            data = active[name].read_bytes()
        elif name in stock:
            data = tint.extract_stock_bif_resource(*stock[name])
        else:
            missing.add(name)
            return
        dependencies[name] = data
        load(mdl.supermodel(data))

    originals = {name for name in active if ROBE.fullmatch(name)}
    bodies = {name[:3] + "0" for name in originals}
    for name in originals | bodies:
        load(name)
    for name, data in dependencies.items():
        (stage / f"{name}.mdl").write_bytes(data)
    text_cache = {}

    def load_text(name):
        if name not in dependencies:
            return None
        if name not in text_cache:
            data = dependencies[name]
            destination = stage / "original" / f"{name}.mdl"
            if mdl.binary(data):
                mdl.run_compiler(compiler, stage, ["-de", str(stage / f"{name}.mdl"),
                                 str(stage / "original") + "/"], "decompile.log")
                text_cache[name] = poses.accurate_rotations(destination.read_text(encoding="latin1"), data)
            else:
                text_cache[name] = data.decode("latin1")
        return text_cache[name]

    def chain(name):
        result = []
        while name and name in dependencies:
            if name in result:
                raise ValueError(f"Cyclic animation chain: {name}")
            result.append(name)
            name = mdl.supermodel(dependencies[name])
        return result

    body_chains = {body: set(chain(body)) for body in bodies}
    candidates = {}
    for name in sorted(originals):
        body = name[:3] + "0"
        for owner in chain(name):
            if owner not in body_chains[body]:
                candidates.setdefault(owner, set()).add(body)
    sources, clips, repaired_parents = {}, {}, {}
    for index, (name, references) in enumerate(sorted(candidates.items()), 1):
        text = load_text(name)
        if not anim.ANIMATION.search(text):
            continue
        # A shared clothing library must use the nearest body ancestor shared
        # by all of its wearers, rather than baking one race's local proportions
        # into a library also inherited by other races or genders.
        common = set.intersection(*(body_chains[body] for body in references))
        if not common:
            raise ValueError(f"{name}: clothing wearers have no common body animation ancestor")
        body = max(common, key=lambda value: (len(chain(value)), value))
        corrected, changed = align(text, body, load_text)
        if changed:
            sources[name], clips[name] = corrected.encode("latin1"), changed
            print(f"{name}: aligning {len(changed)} clips for {sorted(references)}", flush=True)
    for name in sorted(originals):
        parent = mdl.supermodel(dependencies[name])
        if parent not in missing:
            continue
        body = name[:3] + "0"
        if body not in dependencies:
            raise ValueError(f"{name}: missing canonical body {body}")
        text = sources.get(name, load_text(name).encode("latin1")).decode("latin1")
        text = re.sub(r"(?im)^(\s*setsupermodel\s+\S+\s+)\S+",
                      lambda match: match[1] + body, text)
        sources[name] = text.encode("latin1")
        repaired_parents[name] = {"before": parent, "after": body}
        print(f"{name}: replacing missing {parent} parent with {body}", flush=True)
    # Compilers assign inherited part IDs against the immediate parent. Stock
    # libraries compiled against the current body can allocate new cloth IDs;
    # rebuild every descendant so ordinary/large robes keep matching those IDs.
    children = {}
    for name, path in active.items():
        with path.open("rb") as stream:
            header = stream.read(244)
            parent = mdl.supermodel(header if mdl.binary(header) else path.read_bytes())
        children.setdefault(parent, set()).add(name)
    authored_changes = set(sources)
    pending = list(sources)
    while pending:
        parent = pending.pop()
        for name in sorted(children.get(parent, ())):
            if name in sources:
                continue
            load(name)
            (stage / f"{name}.mdl").write_bytes(dependencies[name])
            sources[name] = load_text(name).encode("latin1")
            pending.append(name)
    report = {"robes": len(originals), "models": len(sources), "clips": clips,
              "repaired_parents": repaired_parents,
              "missing_supermodels": sorted(missing), "applied": False}
    (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not args.apply:
        print(json.dumps({key: value for key, value in report.items() if key != "clips"}, indent=2))
        return

    compiled = {}
    def compile_one(name):
        if name in compiled or name not in sources:
            return
        parent = mdl.supermodel(sources[name])
        compile_one(parent)
        before = dependencies[name]
        if name not in authored_changes and mdl.binary(before):
            data = remap_inherited_parts(before, dependencies[parent], (stage / f"{parent}.mdl").read_bytes())
            (stage / "binary" / f"{name}.mdl").write_bytes(data)
            (stage / f"{name}.mdl").write_bytes(data)
            compiled[name] = data
            return
        source = stage / "source" / f"{name}.mdl"
        source.write_bytes(sources[name])
        mdl.run_compiler(compiler, stage, ["-cne", str(source), str(stage / "binary") + "/"], "compile.log")
        data = (stage / "binary" / f"{name}.mdl").read_bytes()
        if mdl.binary(before):
            original_model, model = poses.Model(before, False), poses.Model(data, False)
            if [(n, p) for n, p, _, _, _ in original_model.nodes] != [(n, p) for n, p, _, _, _ in model.nodes]:
                raise ValueError(f"{name}: native node hierarchy changed")
            parent_data = (stage / f"{parent}.mdl").read_bytes() if parent else b""
            if mdl.binary(parent_data):
                parent_parts = {n: i for n, _, i, _, _ in poses.Model(parent_data, False).nodes}
                old_parent_data = dependencies.get(mdl.supermodel(before), b"")
                old_parent_parts = ({n: i for n, _, i, _, _ in poses.Model(old_parent_data, False).nodes}
                                    if mdl.binary(old_parent_data) else {})
                for node_index, (node_name, _, part, _, _) in enumerate(model.nodes[1:], 1):
                    inherited = (node_name in old_parent_parts and
                                 original_model.nodes[node_index][2] == old_parent_parts[node_name])
                    if inherited and node_name in parent_parts and part != parent_parts[node_name]:
                        raise ValueError(f"{name}/{node_name}: part ID does not match its compiled parent")
            names = {n: n for n, _, _, _, _ in original_model.nodes}
            data = poses.preserve_skin_bindings(before, data, names)
            poses.validate_garment_bindings(before, data, names)
        (stage / "binary" / f"{name}.mdl").write_bytes(data)
        (stage / f"{name}.mdl").write_bytes(data)
        mdl.run_compiler(compiler, stage, ["-de", str(stage / "binary" / f"{name}.mdl"),
                         str(stage / "decompiled") + "/"], "validate.log")
        mdl.validate_round_trip(sources[name], data,
                               (stage / "decompiled" / f"{name}.mdl").read_text(encoding="latin1"))
        compiled[name] = data
    for name in sorted(sources):
        compile_one(name)
    # All compiled outputs must pass before any HAK source is replaced.
    for name, data in dependencies.items():
        if name in active and active[name].read_bytes() != data:
            raise ValueError(f"Source changed during animation alignment: {active[name]}")
    for name, data in compiled.items():
        target = active.get(name, ROOT / "sw_cr_creature" / f"{name}.mdl")
        if name not in active and target.exists():
            raise ValueError(f"New stock override collides with an existing resource: {target}")
        target.write_bytes(data)
    report["applied"] = True
    (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Aligned {len(compiled)} original robe/animation models; regenerate RGB robe models next.")


if __name__ == "__main__":
    main()
