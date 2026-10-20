#!/usr/bin/env python3
"""Refit armor parts onto the playable bodies that had no model for them.

The armor catalog is shared by every playable body, but many parts were only
authored for some of them (necks 15-21 exist only for humans, robe 221 has no
dwarf or half-orc version, and so on), so the appearance editor hid them from
those species. Each missing model is refitted from the donor the native palette
lookup already selects for it (p<g>h0_<part>, then pmh0_<part>), so its tint
mask keeps matching the donor's UVs. A body's own model that borrows that donor's
texture but was laid out for another one (dwarf robe 22 is a diving suit on the
surcoat texture, the dwarf set 75 is stock geometry on a custom texture) renders
white, black or scrambled patches, so it is replaced the same way. Designs authored for only the other gender
are refitted from that gender's model of the same body and keep its palette.
Droid parts come only from the other droid gender.

Every vertex is carried from the donor body to the target body bone by bone:
  * a rigid part lives in its attachment bone's frame;
  * an external skin follows each weighted bone of the wearer's skeleton;
  * a robe's own rig is re-posed onto the target skeleton, garment joints keep
    their local placement under the moved body joints, and the skin follows.
In each bone frame the donor's naked base part (<part>001) is mapped onto the
target's, so armor keeps fitting both limb length and girth. Inverse binds
are rebuilt for the target skeleton; materials, UVs, weights and controllers
other than the re-posed robe joints stay byte-identical.

Run --apply to write the models and manifest, then GenerateTintMapAssets.py
--refresh-model-bindings, GeneratePartSkinModels.py --apply, GenerateNeckModels.py
--apply and GenerateRobeRgbModels.py --apply. --check verifies the outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from pathlib import Path

import numpy as np

import CompileModels as cm
import NeckSkinBindings as nsb
import GenerateTintMapAssets as tint
from RobePoseAudit import Model, multiply, rotate, sample

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/RacialPartVariants.json"
IMPLEMENTATION = ("tools/GenerateRacialPartVariants.py", "tools/NeckSkinBindings.py", "tools/RobePoseAudit.py",
                  "tools/CompileModels.py")
PATTERN = re.compile(r"p([mf])([hedoz])0_([a-z]+?_?)(\d{3})")

# Bone -> naked base part used to measure limb length and girth in that bone's frame.
REFERENCE_PARTS = {
    "torso_g": "chest", "pelvis_g": "pelvis", "neck_g": "neck", "lbicep_g": "bicepl", "rbicep_g": "bicepr",
    "lforearm_g": "forel", "rforearm_g": "forer", "lhand_g": "handl", "rhand_g": "handr", "lthigh_g": "legl",
    "rthigh_g": "legr", "lshin_g": "shinl", "rshin_g": "shinr", "lfoot_g": "footl", "rfoot_g": "footr",
    "lshoulder_g": "shol", "rshoulder_g": "shor",
}
ATTACHMENT = {part: bone for bone, part in REFERENCE_PARTS.items()}
ATTACHMENT["belt"] = "pelvis_g"
SCALE_LIMITS = (0.55, 1.8)
COAT_BANKS = {"a_ba_coat", "a_da_coat", "a_fa_coat", "a_dfa_coat"}


def coat_bank(gender: str, race: str) -> str:
    stocky = race in "do"
    if gender == "m":
        return "a_da_coat" if stocky else "a_ba_coat"
    return "a_dfa_coat" if stocky else "a_fa_coat"


# ---------------------------------------------------------------- math

def q_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def q_norm(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    q = tuple(c / n for c in q)
    return q if q[3] >= 0 else tuple(-c for c in q)


def compose(a, b):
    """World transform of b expressed in a's frame: a * b (position, quaternion)."""
    (ap, aq), (bp, bq) = a, b
    return tuple(x + y for x, y in zip(ap, rotate(aq, bp))), q_norm(multiply(aq, bq))


def invert(t):
    p, q = t
    iq = q_conj(q)
    return tuple(-v for v in rotate(iq, p)), iq


def apply(t, v):
    p, q = t
    return tuple(x + y for x, y in zip(p, rotate(q, v)))


def matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


# ---------------------------------------------------------------- resources

class Resources:
    def __init__(self, game_data: Path | None):
        self.active = tint.find_active_models()
        self.stock = tint.read_stock_key_models(game_data) if game_data else {}
        self.cache = {}

    def exists(self, model: str) -> bool:
        return model in self.active or model in self.stock

    def load(self, model: str) -> bytes:
        if model not in self.cache:
            if model in self.active:
                self.cache[model] = self.active[model].read_bytes()
            elif model in self.stock:
                self.cache[model] = tint.extract_stock_bif_resource(*self.stock[model])
            else:
                raise KeyError(f"Missing model resource {model}")
        return self.cache[model]


def node_worlds(model: Model):
    """Bind (position, rotation) of every node by traversal index, relative to the model root."""
    result = []
    for name, parent, _, controllers, _ in model.nodes:
        p = sample(controllers[8], 0) if 8 in controllers else (0.0, 0.0, 0.0)
        q = q_norm(sample(controllers[20], 0, True)) if 20 in controllers else (0.0, 0.0, 0.0, 1.0)
        local = (tuple(p), q)
        result.append(local if parent is None else compose(result[parent], local))
    return result


def body_worlds(data: bytes):
    body = nsb.body_model(data)
    worlds = node_worlds(body) if cm.binary(data) else _ascii_worlds(body)
    names = {}
    for index, (name, *_rest) in enumerate(body.nodes):
        names.setdefault(name, index)
    return body, worlds, names


def _ascii_worlds(body):
    result = []
    for name, parent, _, controllers, _ in body.nodes:
        p = controllers[8][1][0] if 8 in controllers else (0.0, 0.0, 0.0)
        q = q_norm(controllers[20][1][0]) if 20 in controllers else (0.0, 0.0, 0.0, 1.0)
        local = (tuple(p), q)
        result.append(local if parent is None else compose(result[parent], local))
    return result


class Mesh:
    """Offsets of one binary trimesh node and its optional skin."""

    def __init__(self, model: Model, index: int):
        self.index = index
        name, parent, _, _, node = model.nodes[index]
        self.name = name
        flags = model.uint(node + 108)
        self.mesh = node + 112 + (92 if flags & 2 else 0) + (216 if flags & 4 else 0) + (68 if flags & 16 else 0)
        self.skin = self.mesh + 512 if flags & 0x40 else None
        self.count = struct.unpack_from("<H", model.data, self.mesh + 448)[0]
        self.stride = model.uint(self.mesh + 440)
        self.raw = 12 + model.uint(4)
        self.verts = model.uint(self.mesh + 444)
        self.normals = model.uint(self.mesh + 468)

    def address(self, pointer, vertex, size):
        return self.raw + pointer + vertex * (self.stride or size)

    def read(self, data, pointer, columns, fmt="f"):
        size = struct.calcsize(fmt) * columns
        return [struct.unpack_from(f"<{columns}{fmt}", data, self.address(pointer, i, size)) for i in range(self.count)]


def meshes(model: Model):
    for index, (_, _, _, _, node) in enumerate(model.nodes):
        flags = model.uint(node + 108)
        if flags & 0x20 and not flags & 0x200:
            mesh = Mesh(model, index)
            if mesh.count and mesh.verts != 0xFFFFFFFF:
                yield mesh


def reference_box(resources: Resources, model_name: str):
    """Bounding box of a naked base part in its attachment bone frame."""
    if not resources.exists(model_name):
        return None
    data = resources.load(model_name)
    if not cm.binary(data):
        return None
    model = Model(data, False)
    worlds = node_worlds(model)
    points = []
    for mesh in meshes(model):
        if not model.uint(mesh.mesh + 108):
            continue  # render 0
        transform = worlds[mesh.index]
        points.extend(apply(transform, v) for v in mesh.read(data, mesh.verts, 3))
    if not points:
        return None
    array = np.array(points)
    return array.min(axis=0), array.max(axis=0)


class BoneFit:
    """Maps the donor's naked part box onto the target's in one bone frame."""

    def __init__(self, donor_box, target_box):
        if donor_box is None or target_box is None:
            self.scale, self.donor_center, self.target_center = np.ones(3), np.zeros(3), np.zeros(3)
            return
        donor_extent = np.maximum(donor_box[1] - donor_box[0], 0.02)
        target_extent = np.maximum(target_box[1] - target_box[0], 0.02)
        self.scale = np.clip(target_extent / donor_extent, *SCALE_LIMITS)
        self.donor_center = (donor_box[0] + donor_box[1]) / 2
        self.target_center = (target_box[0] + target_box[1]) / 2

    def point(self, v):
        return self.target_center + self.scale * (np.asarray(v) - self.donor_center)

    def normal(self, n):
        n = np.asarray(n) / self.scale
        length = np.linalg.norm(n)
        return n / length if length > 1e-12 else np.asarray(n)


# ---------------------------------------------------------------- planning

def donor_for(target: str, resources: Resources) -> str | None:
    gender, race, part, number = PATTERN.fullmatch(target).groups()
    other = "m" if gender == "f" else "f"
    if race == "z":
        # Droid parts only fit droids; both droid bodies share one skeleton.
        candidate = f"p{other}z0_{part}{number}"
        return candidate if resources.exists(candidate) else None
    # The native palette lookup's own donors first; a design authored for only the other
    # gender is refitted from that gender's model of the same body and keeps its palette.
    for candidate in (f"p{gender}h0_{part}{number}", f"pmh0_{part}{number}", f"p{other}{race}0_{part}{number}",
                      f"p{other}h0_{part}{number}"):
        if candidate != target and resources.exists(candidate):
            return candidate
    return None


# ---------------------------------------------------------------- refit

def write_vector(buffer, address, values):
    struct.pack_into(f"<{len(values)}f", buffer, address, *[float(v) for v in values])


def refit(resources: Resources, donor: str, target: str) -> bytes:
    data = resources.load(donor)
    if not cm.binary(data):
        raise ValueError(f"{donor}: donor must be compiled")
    d_gender, d_race, part, _ = PATTERN.fullmatch(donor).groups()
    t_gender, t_race, _, _ = PATTERN.fullmatch(target).groups()
    d_body_name, t_body_name = f"p{d_gender}{d_race}0", f"p{t_gender}{t_race}0"
    d_body, d_world, d_names = body_worlds(resources.load(d_body_name))
    t_body, t_world, t_names = body_worlds(resources.load(t_body_name))
    fits = {}

    def fit(bone):
        if bone not in fits:
            ref = REFERENCE_PARTS.get(bone)
            fits[bone] = BoneFit(reference_box(resources, f"{d_body_name}_{ref}001") if ref else None,
                                 reference_box(resources, f"{t_body_name}_{ref}001") if ref else None)
        return fits[bone]

    model = Model(data, False)
    buffer = bytearray(data)
    old_worlds = node_worlds(model)
    # Robes and cloak models carry their own body-named rig under the body supermodel.
    is_robe = part in ("robe", "cloak_")

    # Robe rigs: re-pose body-named joints onto the target skeleton.
    new_worlds = list(old_worlds)
    if is_robe:
        for index, (name, parent, _, controllers, node) in enumerate(model.nodes):
            if parent is None:
                continue
            if name in t_names and name in d_names:
                world = t_world[t_names[name]]
            else:
                local = compose(invert(old_worlds[parent]), old_worlds[index])
                world = compose(new_worlds[parent], local)
            new_worlds[index] = world
            local = compose(invert(new_worlds[parent]), world)
            values = 12 + model.uint(node + 96)
            keys = 12 + model.uint(node + 84)
            for k in range(model.uint(node + 88)):
                kind, rows, times, start, columns = struct.unpack_from("<IHHHB", data, keys + k * 12)
                if rows != 1:
                    raise ValueError(f"{donor}/{name}: animated bind controller")
                if kind == 8:
                    write_vector(buffer, values + start * 4, local[0])
                elif kind == 20:
                    write_vector(buffer, values + start * 4, local[1])
        bank = cm.supermodel(data)
        if bank == d_body_name:
            bank = t_body_name
        elif bank in COAT_BANKS:
            bank = coat_bank(t_gender, t_race)
        buffer[180:244] = bank.encode("ascii").ljust(64, b"\0")

    attach = ATTACHMENT.get(re.sub(r"\d+$", "", part))
    external = []
    for mesh in meshes(model):
        positions = mesh.read(data, mesh.verts, 3)
        normals = mesh.read(data, mesh.normals, 3) if mesh.normals != 0xFFFFFFFF else None
        new_positions, new_normals = [], []
        if mesh.skin is None:
            if is_robe:
                continue  # rigid robe pieces follow their re-posed parent joints
            # Rigid part: the part root is the attachment bone frame.
            to_part = old_worlds[mesh.index]
            from_part = invert(to_part)
            bone_fit = fit(attach)
            rotation = matrix(to_part[1])
            for i, v in enumerate(positions):
                moved = bone_fit.point(apply(to_part, v))
                new_positions.append(apply(from_part, tuple(moved)))
                if normals:
                    n = rotation.T @ bone_fit.normal(rotation @ np.array(normals[i]))
                    new_normals.append(n / (np.linalg.norm(n) or 1))
        else:
            skin = mesh.skin
            weights = mesh.read(data, model.uint(skin + 12), 4)
            slots = mesh.read(data, model.uint(skin + 16), 4, "h")
            bone_nodes = struct.unpack_from("<17h", data, skin + 64)
            bone_count = model.uint(skin + 24)
            qptr, tptr = 12 + model.uint(skin + 28), 12 + model.uint(skin + 40)
            internal = bone_count <= len(model.nodes)
            if internal:
                names = [node[0] for node in model.nodes]
                mesh_old = old_worlds[mesh.index]
                mesh_new = new_worlds[mesh.index]
                bone_new = lambda b: new_worlds[b]
            else:
                if attach is None:
                    raise ValueError(f"{donor}: external skin without an attachment bone")
                names = [node[0] for node in d_body.nodes]
                local = old_worlds[mesh.index]
                mesh_new = compose(t_world[t_names[attach]], local)
                bone_new = lambda b: t_world[t_names[names[b]]]
            used = sorted({bone_nodes[s] for w, ss in zip(weights, slots) for wt, s in zip(w, ss) if wt and s >= 0})
            inverse = {}
            for bone in used:
                stored_q = struct.unpack_from("<4f", data, qptr + bone * 16)
                inverse[bone] = (struct.unpack_from("<3f", data, tptr + bone * 12),
                                 q_norm((*stored_q[1:], stored_q[0])))
            to_mesh = invert(mesh_new)
            for i, v in enumerate(positions):
                total = np.zeros(3)
                normal_total = np.zeros(3)
                weight_sum = 0.0
                for wt, s in zip(weights[i], slots[i]):
                    if not wt or s < 0:
                        continue
                    bone = bone_nodes[s]
                    bone_fit = fit(names[bone])
                    local_point = bone_fit.point(apply(inverse[bone], v))
                    world = apply(bone_new(bone), tuple(local_point))
                    total += wt * np.array(apply(to_mesh, world))
                    if normals:
                        local_normal = bone_fit.normal(matrix(inverse[bone][1]) @ np.array(normals[i]))
                        world_normal = matrix(bone_new(bone)[1]) @ local_normal
                        normal_total += wt * (matrix(to_mesh[1]) @ world_normal)
                    weight_sum += wt
                new_positions.append(total / (weight_sum or 1))
                if normals:
                    new_normals.append(normal_total / (np.linalg.norm(normal_total) or 1))
            for bone in used:
                t_bind = invert(bone_new(bone))
                rebound = compose(t_bind, mesh_new)
                write_vector(buffer, tptr + bone * 12, rebound[0])
                q = rebound[1]
                write_vector(buffer, qptr + bone * 16, (q[3], q[0], q[1], q[2]))
            if not internal:
                external.append(mesh)
        for i, v in enumerate(new_positions):
            write_vector(buffer, mesh.address(mesh.verts, i, 12), v)
        for i, n in enumerate(new_normals):
            write_vector(buffer, mesh.address(mesh.normals, i, 12), n)
        if new_positions:
            array = np.array(new_positions)
            low, high = array.min(axis=0), array.max(axis=0)
            write_vector(buffer, mesh.mesh + 20, low)
            write_vector(buffer, mesh.mesh + 32, high)
            centre = array.mean(axis=0)
            struct.pack_into("<f", buffer, mesh.mesh + 44, float(np.linalg.norm(array - centre, axis=1).max()))
            write_vector(buffer, mesh.mesh + 48, centre)

    result = bytes(buffer)
    if external:
        mapping = nsb.mapping_for(result, d_body, t_body)
        result = nsb.remap(result, target, mapping, bone_count=len(t_body.nodes))
    else:
        renamed = bytearray(result)
        renamed[20:84] = target.encode("ascii").ljust(64, b"\0")
        root = model.nodes[0][4]
        renamed[root + 32:root + 64] = target.encode("ascii").ljust(32, b"\0")
        result = bytes(renamed)
    return update_model_bounds(result)


def update_model_bounds(data: bytes) -> bytes:
    model = Model(data, False)
    worlds = node_worlds(model)
    points = []
    for mesh in meshes(model):
        points.extend(apply(worlds[mesh.index], v) for v in mesh.read(data, mesh.verts, 3))
    if not points:
        return data
    array = np.array(points)
    buffer = bytearray(data)
    write_vector(buffer, 148, array.min(axis=0))
    write_vector(buffer, 160, array.max(axis=0))
    struct.pack_into("<f", buffer, 172, float(np.linalg.norm(array, axis=1).max()))
    return bytes(buffer)


# ---------------------------------------------------------------- manifest

def output_path(target: str) -> Path:
    if "_cloak_" in target:
        return ROOT / "sw_pt_cloak" / f"{target}.mdl"
    return tint.stock_model_output_directory(target) / f"{target}.mdl"


def implementation_digest() -> str:
    digest = hashlib.sha256()
    for relative in IMPLEMENTATION:
        digest.update((ROOT / relative).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def without_materials(path: Path, data: bytes) -> bytes:
    """Blank the MTR names that GenerateTintMapAssets.py --refresh-model-bindings owns."""
    neutral = bytearray(data)
    for _, _, offset, _ in tint.read_binary_model_material_fields(path, data):
        neutral[offset:offset + 64] = bytes(64)
    return bytes(neutral)


def plan(manifest: dict, resources: Resources) -> dict[str, str]:
    targets = {}
    for target in manifest["targets"]:
        donor = donor_for(target, resources)
        if donor is None:
            raise ValueError(f"{target}: no human donor exists")
        targets[target] = donor
    return targets


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--game-data", type=Path, help="NWN data directory for stock donors and skeletons")
    parser.add_argument("--only", action="append", default=[], help="limit to these targets (apply only)")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    resources = Resources(args.game_data)
    for target in manifest["targets"]:
        resources.active.pop(target, None)  # outputs never donate to themselves
    targets = plan(manifest, resources)
    failures = []
    outputs = manifest.setdefault("outputs", {})
    for target, donor in sorted(targets.items()):
        if args.only and target not in args.only:
            continue
        data = refit(resources, donor, target)
        path = output_path(target)
        digest = hashlib.sha256(data).hexdigest()
        relative = path.relative_to(ROOT).as_posix()
        if args.apply:
            path.write_bytes(data)
            outputs[target] = {"donor": donor, "path": relative, "sha256": digest}
        elif not path.is_file() or outputs.get(target, {}).get("sha256") != digest \
                or without_materials(path, path.read_bytes()) != without_materials(path, data):
            # The tint binding pass may rename a refit's materials afterwards; nothing else may differ.
            failures.append(target)
    if args.apply:
        manifest["implementationSha256"] = implementation_digest()
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Refitted {len(targets) if not args.only else len(args.only)} racial part variants.")
    else:
        if manifest.get("implementationSha256") != implementation_digest():
            failures.append("implementation changed; rerun --apply")
        if failures:
            raise SystemExit(f"Stale racial part variants: {failures[:20]} ({len(failures)} total)")
        print(f"Validated {len(targets)} racial part variants.")


if __name__ == "__main__":
    main()
