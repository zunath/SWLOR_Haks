"""Add new clips ahead of immutable robe banks without regenerating existing motion.

The candidate stage comes from GenerateRobeRgbModels (without --apply). Only
explicitly selected new clips are copied. Existing banks stay byte-identical;
wearers change only the fixed-width native supermodel name.
"""
import argparse
import gc
import hashlib
import json
import re
from pathlib import Path
import subprocess
import struct
from types import SimpleNamespace

import GenerateRobeRgbModels as robes
import RobeAnimationBanks as banks
import RobeAnimations as animations
import RobePoseAudit as poses
import RobeSharingAudit as sharing


PLAYABLE_PREFIXES = {f"p{gender}{body}" for gender in "fm" for body in "deho"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def make_layer(before, candidate, names, name):
    old, new = banks.CompiledBridge(before), banks.CompiledBridge(candidate)
    wanted = set(names)
    if not wanted or wanted & set(old.clip_names) or not wanted <= set(new.clip_names):
        raise ValueError("An extension must select only new, present animation clips")
    if old.pack(old.name, old.parent, []) != new.pack(old.name, old.parent, []):
        raise ValueError("An extension cannot change skeletons, binds, native part IDs or model metadata")
    selected = [clip for key, clip in zip(new.clip_names, new.clips) if key in wanted]
    result = old.pack(name, old.name, selected)
    actual = banks.CompiledBridge(result)
    if actual.parent != old.name or actual.pack(old.name, old.parent, []) != old.pack(old.name, old.parent, []):
        raise ValueError("Extension changed its inherited rig")
    for source, target in zip(selected, actual.clips):
        if source.relocated(0, name) != target.relocated(0, name):
            raise ValueError("Extension changed selected controller, timing or event bytes")
    animations.validate_animation_parts(result)
    return result


def reparent(data, before, after):
    if not robes.mdl.binary(data) or robes.mdl.supermodel(data) != before or not banks.is_bank_name(after):
        raise ValueError("Cannot reparent an unexpected wearer")
    result = bytearray(data)
    encoded = after.encode("ascii")
    result[180:244] = encoded.ljust(64, b"\0")
    if result[:180] != data[:180] or result[244:] != data[244:] or robes.mdl.supermodel(result) != after:
        raise ValueError("Reparenting changed data outside the supermodel name")
    return bytes(result)


def read_candidate(stage, name):
    """Accept a full staging bridge or the standard losslessly partitioned output."""
    parts, current = [], name
    while True:
        data = (stage / "binary" / f"{current}.mdl").read_bytes()
        parts.append(data)
        parent = robes.mdl.supermodel(data)
        expected = f"{name}_b{len(parts):02d}"
        if parent != expected:
            break
        current = parent
    return banks.join(parts)


class GitBlobs:
    def __init__(self, ref):
        self.ref = subprocess.check_output(["git", "-C", str(robes.ROOT), "rev-parse", ref], text=True).strip()
        self.process = subprocess.Popen(["git", "-C", str(robes.ROOT), "cat-file", "--batch"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, path):
        self.process.stdin.write(f"{self.ref}:{path}\n".encode("ascii")); self.process.stdin.flush()
        header = self.process.stdout.readline().decode("ascii").split()
        if len(header) != 3 or header[1] != "blob":
            raise ValueError(f"Baseline resource is missing: {path}")
        size = int(header[2]); data = self.process.stdout.read(size)
        if len(data) != size or self.process.stdout.read(1) != b"\n":
            raise ValueError("Truncated baseline Git blob")
        return data

    def close(self):
        self.process.stdin.close(); self.process.stdout.close(); self.process.wait()


def verify_body_additions(before, after, names):
    old, new = poses.Model(before), poses.Model(after)
    if set(new.clips) - set(old.clips) != set(names) or set(old.clips) - set(new.clips):
        raise ValueError("The body bank must add exactly the selected clips")
    if len(old.nodes) != len(new.nodes):
        raise ValueError("The body bank geometry changed")
    for left, right in zip(old.nodes, new.nodes):
        if left[:3] != right[:3] or not sharing.controllers_equal(left[3], right[3]):
            raise ValueError("The body bank skeleton or bind changed")
    headers_before, headers_after = sharing.clip_headers(before), sharing.clip_headers(after)
    for name, clip in old.clips.items():
        proposed = new.clips[name]
        if headers_before[name] != headers_after[name] or len(clip[1]) != len(proposed[1]):
            raise ValueError(f"Existing body clip changed: {name}")
        for left, right in zip(clip[1], proposed[1]):
            if left[:3] != right[:3] or not sharing.controllers_equal(left[3], right[3]):
                raise ValueError(f"Existing body controller changed: {name}/{left[0]}")
    return len(old.clips)


def rotation_changes(before, after, allowed):
    """Recognize quaternion-only corrections with identical nodes and clip headers."""
    old, new = poses.Model(before), poses.Model(after)
    if old.clips.keys() != new.clips.keys() or sharing.clip_headers(before) != sharing.clip_headers(after):
        raise ValueError("A rotation revision cannot change clip inventory, timing or events")
    if len(old.nodes) != len(new.nodes) or any(a[:3] != b[:3] or not sharing.controllers_equal(a[3], b[3])
                                                for a, b in zip(old.nodes, new.nodes)):
        raise ValueError("A rotation revision cannot change the body rig")
    changes = {}
    for clip, (_, nodes) in old.clips.items():
        proposed = new.clips[clip][1]
        if len(nodes) != len(proposed): raise ValueError("Rotation revision changed animation nodes")
        for left, right in zip(nodes, proposed):
            if left[:3] != right[:3] or left[3].keys() != right[3].keys():
                raise ValueError("Rotation revision changed native nodes or channels")
            for kind, value in left[3].items():
                target = right[3][kind]
                if sharing.controllers_equal({kind: value}, {kind: target}): continue
                if clip not in allowed or kind != 20:
                    raise ValueError(f"Unselected or structural body change: {clip}/{left[0]}")
                times, frames = target
                if (not times or len(times) != len(frames) or times[0] < 0 or times[-1] > old.clips[clip][0] + 2e-6 or
                        any(b <= a for a, b in zip(times, times[1:]))):
                    raise ValueError("Invalid revised quaternion sample times")
                changes.setdefault(clip, {})[left[0]] = (value, target)
    if not changes: raise ValueError("No selected rotation changes")
    return changes


def revise_rotations(before, changes):
    """Copy measured body quaternion values into compatible, owned layer channels."""
    model = poses.Model(before)
    bridge = banks.CompiledBridge(before)
    clips = []
    count = 0
    if not set(changes) <= set(model.clips): raise ValueError("Layer lacks revised clip")
    for clip, component in zip(bridge.clip_names, bridge.clips):
        nodes = changes.get(clip)
        if nodes is None:
            clips.append(component)
            continue
        actual = {node[0]: node for node in model.clips[clip][1]}
        data = bytearray(component.relocated(0, bridge.name))
        pointers, names = list(component.pointers), list(component.names)
        for name, (old, new) in nodes.items():
            node = actual.get(name)
            if node is None or 20 not in node[3] or not sharing.controllers_equal({20: old}, {20: node[3][20]}):
                raise ValueError(f"Layer does not match the prior body rotation: {clip}/{name}")
            # Locate again after any earlier controller-array growth relocated this subtree.
            def find(offset):
                if banks._name(data, offset + 32, 32).lower() == name: return offset
                children = banks._uint(data, offset + 72)
                for index in range(banks._uint(data, offset + 76)):
                    found = find(banks._uint(data, children + index*4))
                    if found is not None: return found
                return None
            offset = find(banks._uint(data, 72))
            if offset is None: raise ValueError("Revised node disappeared")
            for index in range(banks._uint(data, offset + 88)):
                descriptor = banks._uint(data, offset + 84) + index*12
                kind, rows, times, start, columns = struct.unpack_from("<IHHHB", data, descriptor)
                if kind != 20: continue
                if columns != 4: raise ValueError("Unsupported quaternion layout")
                values = banks._uint(data, offset + 96)
                proposed_times, rotations = new
                if rows >= len(rotations) and old[0][:len(proposed_times)] == proposed_times:
                    # Reuse the existing array when a moving wrist becomes a neutral grip.
                    struct.pack_into("<H", data, descriptor + 4, len(rotations))
                    for row, value in enumerate(rotations): struct.pack_into("<4f", data, values + 4*(start + row*4), *value)
                else:
                    # A formerly static accent needs more samples. Keep every old float intact
                    # and append only the new orientation track to this node's float array.
                    old_count = banks._uint(data, offset + 100)
                    if old_count + 5*len(rotations) > 65535: raise ValueError("Revised controller exceeds native index space")
                    extra = struct.pack(f"<{len(proposed_times)}f", *proposed_times) + b"".join(struct.pack("<4f", *q) for q in rotations)
                    end = values + old_count*4
                    size = len(extra)
                    original = bytes(data)
                    data[end:end] = extra
                    for pointer in pointers:
                        value = banks._uint(original, pointer)
                        struct.pack_into("<I", data, pointer + (size if pointer >= end else 0), value + (size if value >= end else 0))
                    pointers = [p + (size if p >= end else 0) for p in pointers]
                    names = [(p + (size if p >= end else 0), length) for p, length in names]
                    struct.pack_into("<IHHHB", data, descriptor, 20, len(rotations), old_count, old_count + len(rotations), 4)
                    struct.pack_into("<II", data, offset + 100, old_count + 5*len(rotations), old_count + 5*len(rotations))
                count += 1
        clips.append(banks.Component(0, bytes(data), tuple(pointers), tuple(names)))
    if not count: raise ValueError("No layer quaternion channels revised")
    result = bridge.pack(bridge.name, bridge.parent, clips)
    banks.validate_motion_revision(before, result, set(changes))
    animations.validate_animation_parts(result)
    return result, count


def clip_owners(name, load, wanted):
    result, seen = {}, set()
    while name:
        if name in seen: raise ValueError("Cyclic body animation inheritance")
        seen.add(name)
        data = load(name)
        if data is None: raise ValueError(f"Missing animation ancestor: {name}")
        for clip in sharing.clip_headers(data):
            if clip in wanted and clip not in result: result[clip] = name
        name = robes.mdl.supermodel(data)
    if result.keys() != set(wanted): raise ValueError("Body lacks selected animation owners")
    return result


def apply_rotation_revision(args):
    manifest = json.loads(robes.MANIFEST.read_text())
    original = robes.MANIFEST.read_bytes()
    record = manifest["animation_extensions"][-1]
    wanted = {clip + phase for clip in args.clip for phase in ("", "_in", "_out")}
    if not wanted <= set(record["clips"]): raise ValueError("Only owned extension clips may be revised")
    body_paths = list(record["preserved_body_clips"])
    allowed_errors = {f"Robe RGB input/output changed: {path}; regenerate and validate" for path in body_paths}
    allowed_errors.add("Robe RGB input/output changed: tools/ExtendRobeAnimations.py; regenerate and validate")
    errors = set(robes.check()) - allowed_errors
    if errors: raise ValueError("Catalog is not current: " + "; ".join(sorted(errors)))
    baseline = GitBlobs(args.baseline)
    try:
        if baseline.read("tools/RobeRgbModels.json").replace(b"\r\n", b"\n") != original.replace(b"\r\n", b"\n"):
            raise ValueError("Revision baseline must match the current manifest")
        changes, body_hashes = {}, {}
        for path in body_paths:
            old, new = baseline.read(path), (robes.ROOT / path).read_bytes()
            if digest(old) != manifest["files"][path]: raise ValueError("Body baseline ownership mismatch")
            changes[Path(path).stem] = rotation_changes(old, new, wanted)
            body_hashes[path] = digest(new)
        active = robes.fresh_active_models()
        stock = robes.tint.read_stock_key_models(args.game_data)
        def load_source(name):
            if name in active: return active[name].read_bytes()
            if name in stock: return robes.tint.extract_stock_bif_resource(*stock[name])
            return None
        outputs, bank_records, snapshot, revised_channels = {}, {}, {}, 0
        for name in record["parents"].values():
            before = robes.owned_animation_bytes(name, manifest, active)
            snapshot[name] = digest(before)
            owners = clip_owners(name[:3] + "0", load_source, wanted)
            selected = {}
            for clip, owner in owners.items():
                if owner not in changes: raise ValueError(f"Unexpected revised body source: {owner}")
                if clip in changes[owner]: selected[clip] = changes[owner][clip]
            after, count = revise_rotations(before, selected)
            revised_channels += count
            parts = banks.split(after)
            if list(parts) != manifest["animation_bank_sets"][name]["parts"]:
                raise ValueError("Rotation revision changed the bank layout")
            for part, data in parts.items(): outputs[f"{robes.model_directory(part)}/{part}.mdl"] = data
            bank_records[name] = {"parts": list(parts), "source_sha256": digest(after)}
        def load(name):
            path = f"{robes.model_directory(name)}/{name}.mdl"
            if path in outputs: return outputs[path]
            if name in active: return active[name].read_bytes()
            if name in stock: return robes.tint.extract_stock_bif_resource(*stock[name])
            return None
        samples = 0
        for name in record["parents"].values():
            library = poses.Library(load)
            scoped = SimpleNamespace(model=library.model, owners=library.owners,
                clips=lambda model: {n: c for n, c in library.clips(model).items() if n in record["clips"]})
            samples += poses.validate_body_poses(name, name[:3] + "0", scoped)
            library.model.cache_clear(); library.clips.cache_clear(); library.owners.cache_clear()
            del library, scoped; gc.collect()
        if samples != record["body_pose_samples"]: raise ValueError("Rotation revision pose coverage changed")
        report = {"baseline": baseline.ref, "clips": sorted(wanted), "quaternion_channels": revised_channels,
                  "body_pose_samples": samples, "preservation": "Only selected quaternion tracks changed; rig, clip timing, events, non-rotation channels and other clips retained."}
        args.output.mkdir(parents=True, exist_ok=False)
        for path, data in outputs.items():
            target = args.output / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if args.apply:
            if robes.MANIFEST.read_bytes() != original: raise ValueError("Manifest changed during revision")
            for path, expected in body_hashes.items():
                if digest((robes.ROOT / path).read_bytes()) != expected: raise ValueError("Body changed during revision")
            for name, expected in snapshot.items():
                if digest(robes.owned_animation_bytes(name, manifest, active)) != expected: raise ValueError("Layer changed during revision")
            for path, data in outputs.items():
                (robes.ROOT / path).write_bytes(data); manifest["files"][path] = digest(data)
            for path, value in body_hashes.items(): manifest["files"][path] = value
            manifest["files"]["tools/ExtendRobeAnimations.py"] = robes.file_digest(Path(__file__))
            manifest["animation_bank_sets"].update(bank_records)
            record.setdefault("rotation_revisions", []).append(report)
            robes.MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        baseline.close()


def check_installed(manifest, game_data):
    records = manifest.get("animation_extensions", [])
    if not records:
        raise ValueError("No installed animation extension to check")
    record = records[-1]
    wanted = set(record["clips"])
    active = robes.fresh_active_models()
    stock = robes.tint.read_stock_key_models(game_data)
    baseline = GitBlobs(record["baseline"])
    try:
        prior = json.loads(baseline.read("tools/RobeRgbModels.json"))
        rifle_program = record.get("owner") == "RobeRifleProfiles"
        if rifle_program:
            import RobeRifleProfiles
            recipe = json.loads((robes.ROOT / "tools/RobeRifleProfiles.json").read_text())
            expected_heads = {head for head in prior["animation_bank_sets"]
                              if re.fullmatch(r"p[fm][hedo]_ra[0-9]{3}", head)}
            expected_parents = {item["sourceHead"]: item["head"] for item in recipe["models"]}
            if (set(record["parents"]) != expected_heads or expected_parents != record["parents"] or
                    len(recipe["models"]) != len(expected_heads) or
                    {item["head"] for item in recipe["sources"]} != expected_heads):
                raise ValueError("Rifle extension must preserve every current and retained playable robe head")
            rebuilt = set()
            for item, outputs, _ in RobeRifleProfiles.rebuild(recipe, robes.ROOT):
                for path, data in outputs.items():
                    if (robes.ROOT / path).read_bytes() != data:
                        raise ValueError("Installed rifle layer differs from its pinned body and own-cloth program")
                rebuilt.add(item["sourceHead"])
            if rebuilt != expected_heads:
                raise ValueError("Rifle extension omitted an owned robe program")
        for path in record["preserved_body_clips"]:
            verify_body_additions(baseline.read(path), (robes.ROOT / path).read_bytes(), wanted)
        def load(name):
            if name in active: return active[name].read_bytes()
            if name in stock: return robes.tint.extract_stock_bif_resource(*stock[name])
            return None
        count, samples, garment_checks = 0, 0, 0
        for old, new in record["parents"].items():
            old_parts = prior["animation_bank_sets"][old]["parts"]
            before = banks.join([baseline.read(f"{robes.model_directory(part)}/{part}.mdl") for part in old_parts])
            if before != robes.owned_animation_bytes(old, manifest, active):
                raise ValueError(f"Existing animation bank changed: {old}")
            installed = robes.owned_animation_bytes(new, manifest, active)
            if rifle_program:
                # Exact public reproduction above proves the intentional RF
                # overrides, including own-cloth provenance and native body IDs.
                old_bank, layer = banks.CompiledBridge(before), banks.CompiledBridge(installed)
                if set(layer.clip_names) != wanted:
                    raise ValueError("Rifle extension differs from its complete profile inventory")
                expected = old_bank.pack(new, old, layer.clips)
                animations.validate_animation_parts(installed)
            else:
                expected = make_layer(before, installed, wanted, new)
            if installed != expected:
                raise ValueError(f"Installed extension has unexpected metadata: {new}")
            library = poses.Library(load)
            scoped = SimpleNamespace(model=library.model, owners=library.owners,
                clips=lambda model: {n: c for n, c in library.clips(model).items() if n in wanted})
            if set(scoped.clips(old[:3] + "0")) != wanted:
                raise ValueError("The ordinary body lacks a selected animation")
            samples += poses.validate_body_poses(new, old[:3] + "0", scoped)
            signatures = set()
            for wearer, target in record["wearer_roots"].items():
                if target != new: continue
                before_wearer = baseline.read(f"sw_pt_root/{wearer}.mdl")
                expected = reparent(before_wearer, old, new)
                if expected != active[wearer].read_bytes():
                    raise ValueError(f"Wearer changed beyond its parent bank: {wearer}")
                aliases = manifest["garment_node_names"][wearer]
                signature = tuple(sorted(aliases.items()))
                if signature not in signatures:
                    garment_checks += poses.validate_garment_body_motion(expected, old[:3] + "0", aliases, scoped)
                    signatures.add(signature)
                count += 1
            library.model.cache_clear(); library.clips.cache_clear(); library.owners.cache_clear()
            del library, scoped; gc.collect()
            print(f"Verified installed extension {new} and unchanged parent {old}.", flush=True)
        if count != record["wearers"] or samples != record["body_pose_samples"] or garment_checks != record["garment_body_motion_checks"]:
            raise ValueError("Installed extension validation coverage changed")
        return {"layers": len(record["parents"]), "wearers": count, "body_pose_samples": samples,
                "garment_body_motion_checks": garment_checks}
    finally:
        baseline.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-stage", type=Path)
    parser.add_argument("--baseline", help="Pinned Git revision matching the prior robe manifest")
    parser.add_argument("--game-data", type=Path, required=True)
    parser.add_argument("--clip", action="append", help="New internal name; includes its _in/_out phases")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true", help="Revalidate installed layers and exact baseline preservation")
    parser.add_argument("--revise-rotations", action="store_true", help="Revise only selected existing quaternion channels, without changing rig or keys")
    args = parser.parse_args()
    if args.revise_rotations:
        if args.check or not args.baseline or not args.clip or not args.output:
            parser.error("Rotation revision requires --baseline, --clip and --output; cannot combine with --check")
        apply_rotation_revision(args)
        return
    if args.check:
        manifest = json.loads(robes.MANIFEST.read_text())
        original = robes.MANIFEST.read_bytes()
        allowed = {"Robe RGB input/output changed: tools/ExtendRobeAnimations.py; regenerate and validate"}
        errors = set(robes.check())
        if errors - allowed or errors and not args.apply:
            raise ValueError("Catalog check failed: " + "; ".join(sorted(errors)))
        print(json.dumps(check_installed(manifest, args.game_data), indent=2))
        if args.apply:
            if robes.MANIFEST.read_bytes() != original: raise ValueError("Manifest changed during validation")
            manifest["files"]["tools/ExtendRobeAnimations.py"] = robes.file_digest(Path(__file__))
            robes.MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        return
    if not args.candidate_stage or not args.baseline or not args.clip or not args.output:
        parser.error("Generation requires --candidate-stage, --baseline, --clip and --output")
    wanted = {name + phase for name in args.clip for phase in ("", "_in", "_out")}
    if any(not name or len(name) > 16 or not name.replace("_", "").isalnum() for name in wanted):
        raise ValueError("Invalid animation name")
    manifest = json.loads(robes.MANIFEST.read_text())
    original_manifest = robes.MANIFEST.read_bytes()
    baseline = GitBlobs(args.baseline)
    try:
        if baseline.read("tools/RobeRgbModels.json").replace(b"\r\n", b"\n") != original_manifest.replace(b"\r\n", b"\n"):
            raise ValueError("The baseline must match the complete current robe manifest")
        active = robes.fresh_active_models()
        body_paths = [f"sw_cr_creature/an_a_{sex}a.mdl" for sex in "bf"]
        expected_errors = {f"Robe RGB input/output changed: {path}; regenerate and validate" for path in body_paths}
        unexpected = set(robes.check()) - expected_errors
        if unexpected:
            raise ValueError("Prior catalog is not current: " + "; ".join(sorted(unexpected)))
        preserved_body, input_snapshot = {}, {}
        for path in body_paths:
            before = baseline.read(path)
            if digest(before) != manifest["files"][path]:
                raise ValueError(f"Baseline ownership mismatch: {path}")
            current = (robes.ROOT / path).read_bytes()
            input_snapshot[path] = digest(current)
            preserved_body[path] = verify_body_additions(before, current, wanted)
        pairs, wearers = {}, {}
        for wearer in manifest["garment_node_names"]:
            if wearer[:3] not in PLAYABLE_PREFIXES:
                continue
            path = f"sw_pt_root/{wearer}.mdl"
            before = active[wearer].read_bytes()
            if active[wearer].resolve() != (robes.ROOT / path).resolve():
                raise ValueError(f"Unexpected wearer owner: {wearer}")
            if before != baseline.read(path):
                raise ValueError(f"Wearer differs from pinned baseline: {wearer}")
            old = robes.mdl.supermodel(before)
            candidate = robes.mdl.supermodel((args.candidate_stage / "binary" / f"{wearer}.mdl").read_bytes())
            if old in pairs and pairs[old] != candidate:
                raise ValueError("One installed rig maps to different candidate rigs")
            pairs[old] = candidate; wearers[wearer] = (before, old)
        groups = {}
        candidates = {}
        for old, candidate in sorted(pairs.items()):
            data = read_candidate(args.candidate_stage, candidate)
            model = banks.CompiledBridge(data)
            selected = model.pack(model.name, model.parent,
                                  [clip for name, clip in zip(model.clip_names, model.clips) if name in wanted])
            key = f"extension/{old}/{digest(selected)}"
            groups[key] = {"base": old[:3] + "0"}
            candidates[key] = (old, data)
        allocations = robes.allocate_animation_bridges(groups, active, manifest)
        outputs, roots, records, heads = {}, {}, {}, {}
        stock = robes.tint.read_stock_key_models(args.game_data)
        def load(name):
            path = f"{robes.model_directory(name)}/{name}.mdl"
            if path in outputs: return outputs[path]
            if name in active:
                data = active[name].read_bytes()
                input_snapshot[active[name].relative_to(robes.ROOT).as_posix()] = digest(data)
                return data
            if name in stock: return robes.tint.extract_stock_bif_resource(*stock[name])
            return None
        samples, garment_checks = 0, 0
        for key, name in sorted(allocations.items()):
            old, candidate = candidates[key]
            before = robes.owned_animation_bytes(old, manifest, active)
            complete = make_layer(before, candidate, wanted, name)
            parts = banks.split(complete)
            for part, data in parts.items(): outputs[f"{robes.model_directory(part)}/{part}.mdl"] = data
            heads[old] = name
            records[name] = {"parts": list(parts), "source_sha256": digest(complete)}
            library = poses.Library(load)
            scoped = SimpleNamespace(model=library.model, owners=library.owners,
                clips=lambda model: {n: c for n, c in library.clips(model).items() if n in wanted})
            if set(scoped.clips(old[:3] + "0")) != wanted:
                raise ValueError("The ordinary body lacks a selected new animation")
            samples += poses.validate_body_poses(name, old[:3] + "0", scoped)
            signatures = set()
            for wearer, (data, parent) in wearers.items():
                if parent != old: continue
                aliases = manifest["garment_node_names"][wearer]
                signature = tuple(sorted(aliases.items()))
                if signature in signatures: continue
                garment_checks += poses.validate_garment_body_motion(
                    reparent(data, old, name), old[:3] + "0", aliases, scoped)
                signatures.add(signature)
            library.model.cache_clear(); library.clips.cache_clear(); library.owners.cache_clear()
            del library, scoped; gc.collect()
            print(f"Validated new layer {name} -> {old}; original bank retained unchanged.", flush=True)
        for wearer, (before, old) in wearers.items():
            roots[wearer] = heads[old]
            outputs[f"sw_pt_root/{wearer}.mdl"] = reparent(before, old, heads[old])
        depth = robes.validate_model_chains(roots, load)
        args.output.mkdir(parents=True, exist_ok=False)
        for path, data in outputs.items():
            target = args.output / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        report = {"baseline": baseline.ref, "clips": sorted(wanted), "layers": len(heads),
                  "wearers": len(wearers), "body_pose_samples": samples, "longest_model_chain": depth,
                  "garment_body_motion_checks": garment_checks,
                  "preserved_body_clips": preserved_body, "parents": heads,
                  "wearer_roots": roots,
                  "preservation": "Prior banks untouched; wearer bytes differ only at native supermodel name."}
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if args.apply:
            if robes.MANIFEST.read_bytes() != original_manifest:
                raise ValueError("The robe manifest changed during validation")
            for path, expected in input_snapshot.items():
                if digest((robes.ROOT / path).read_bytes()) != expected:
                    raise ValueError(f"Animation input changed during validation: {path}")
            for wearer, (before, _) in wearers.items():
                if active[wearer].read_bytes() != before: raise ValueError("Wearer changed during validation")
            for path, data in outputs.items():
                (robes.ROOT / path).write_bytes(data)
                manifest["files"][path] = digest(data)
            for path in body_paths: manifest["files"][path] = robes.file_digest(robes.ROOT / path)
            manifest["files"]["tools/ExtendRobeAnimations.py"] = robes.file_digest(Path(__file__))
            manifest["animation_bridges"].update(allocations)
            manifest["animation_bank_sets"].update(records)
            manifest.setdefault("animation_extensions", []).append(report)
            # Header-only reparenting is not a compiler cache hit.
            for name in roots: manifest.get("model_builds", {}).pop(name, None)
            robes.MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        baseline.close()


if __name__ == "__main__":
    main()
