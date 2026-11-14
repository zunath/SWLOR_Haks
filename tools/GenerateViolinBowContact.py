"""Fit violin bow arms to the native left-hand carry on playable normal bodies.

Only the three right-arm rotation tracks are fitted. The violin, body motion,
bone lengths and shared weapon hook keep their inherited values. Small banks
sit below the existing rifle banks, preserving their clips and the body roots.
Generate ASCII, compile those eight banks with CompileModels.py, then --finalize.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import RifleProfileArchives as rifle_archives
import math
import re
from pathlib import Path
import struct
from types import SimpleNamespace

import numpy as np

import GenerateRifleHoldAnimations as rifle
import RobeSharingAudit as sharing

ROOT = Path(__file__).resolve().parents[1]
BODIES = tuple('p' + sex + family + '0' for sex in 'mf' for family in 'edo') + ('pfh0', 'pfz0')
ARM = ('rbicep_g', 'rforearm_g', 'rhand_g')
MAIN = ('sw_vio_loop', 'sw_vio_low', 'sw_vio_mid', 'sw_vio_high', 'sw_vio_flour')
CLIPS = tuple(clip + phase for clip in MAIN for phase in ('', '_in', '_out'))
HAIR_START = np.array((.0023665, .0244655, .0301885))
HAIR_END = np.array((-.344768, .213982, .4864385))
HAIR_AXIS = (HAIR_END - HAIR_START) / np.linalg.norm(HAIR_END - HAIR_START)
HAIR_NORMAL = np.array((-.6233364, -.76618906, -.15622438))
CONTACT = {'m': np.array((.17881616842359055, .04933464638279063, .245)),
           'f': np.array((.12956423369111436, .036231445128813274, .27))}
STRING_AXIS = {'m': np.array((.5701070601892182, .13283710612481703, .8107602871125295)),
               'f': np.array((.42066334726119664, .13283648450276117, .8974390322779334))}
STROKE = dict(zip(MAIN, (.28, .22, .28, .30, .26)))
SOURCE_HASHES = {
    'sw_weapon/wmgfs_m_161.mdl': 'e5c99f960fd5a33327f62eee21406b871f22a14d442c47c94041bab4e702a47f',
    'sw_item/it_torch_041.mdl': '69b7a199e306b82429fa8086f632e1ca0afc8b61cbd1df262ac9aa28bcf8b964',
    'sw_item/it_torch_042.mdl': '0ec96b2296cee41f11da302d834e5c1b3bf0a5ce60e509971a826bf7b751b858',
    'sw_cr_creature/a_ba_non_combat.mdl': '8cfe67b2e901491cde0042b460c6c25e729cb982fbc3591c8eb88478e8a102f9',
}
# The Droid does not consume robe banks, so its original rifle bank was not in
# the robe manifest. Pin its public pre-correction bytes before adding that input.
UNTRACKED_RIFLE_HASHES = {'pfz0': '0388e8aca3a19efbb17422a9836c15b3ca00f337afe0685b8679c7952512b8ad'}


class Library(rifle.Library):
    """Cache parsed ancestors shared by all fifteen fitted clips."""
    def __init__(self, game_data):
        super().__init__(game_data)
        self.models = {}

    def model(self, name, clips=True):
        key = (name, clips)
        if key not in self.models:
            self.models[key] = super().model(name, clips)
        return self.models[key]

    def clip(self, root, clip, start=None):
        name = start if start is not None else rifle.mdl.supermodel(self.data(root))
        seen = set()
        while name and name not in seen:
            seen.add(name)
            model = self.model(name)
            if clip in model.clips:
                return name, model
            name = model.parent
        raise ValueError('Missing inherited clip: ' + root + '/' + clip)

    def invalidate(self, name):
        self.cache.pop(name, None)
        for clips in (True, False):
            self.models.pop((name, clips), None)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def bank_name(body):
    """A distinct, native-length resource name for this body's violin clips."""
    return 'a_vio_' + body


def rifle_tail(library, body):
    """Find the final owned rifle partition without bypassing any fitted clips."""
    head = 'a_rfl_' + body
    name, seen = head, set()
    while name == head or re.fullmatch(re.escape(head) + r'_b\d{2,4}', name or ''):
        if name in seen:
            raise ValueError('Cyclic owned rifle animation bank chain: ' + body)
        seen.add(name)
        data = library.data(name)
        if data is None or len(data) < 244 or not rifle.mdl.binary(data):
            raise ValueError('Expected a compiled rifle bank partition: ' + name)
        if name == head and set(rifle.Model(data).clips) & set(CLIPS):
            raise ValueError('Expected an unchanged compiled rifle bank: ' + body)
        tail, payload = name, data
        name = rifle.mdl.supermodel(data)
    return tail, payload, name


def base_parent(library, body, parent):
    """Skip our existing violin layer and reject a link back into the rifle chain."""
    if parent == bank_name(body):
        data = library.data(parent)
        if data is None or len(data) < 244 or not rifle.mdl.binary(data):
            raise ValueError('Expected a compiled violin bank: ' + body)
        parent = rifle.mdl.supermodel(data)
    head = 'a_rfl_' + body
    if parent in (head, bank_name(body)) or re.fullmatch(re.escape(head) + r'_b\d{2,4}', parent or ''):
        raise ValueError('Cyclic violin/rifle animation bank chain: ' + body)
    return parent


def original_parent(library, body):
    """Resolve beyond all rifle partitions and our own layer before regenerating."""
    return base_parent(library, body, rifle_tail(library, body)[2])


def splice_violin(library, body):
    """Insert the violin layer below the final partition, preserving the head link."""
    name, before, parent = rifle_tail(library, body)
    base_parent(library, body, parent)
    data = bytearray(before)
    data[180:244] = bank_name(body).encode('ascii').ljust(64, b'\0')
    path = ROOT / rifle_archives.bank_directory(body) / (name + '.mdl')
    path.write_bytes(data)
    library.invalidate(name)
    return name


def carry_rotations(library, body, parent):
    """The native torch layer overrides the draft's hidden left-arm rotations."""
    _, source = library.clip(body, 'torchl', parent)
    result = {}
    for name, _, _, controllers, _ in source.clips['torchl'][1]:
        if 20 not in controllers:
            continue
        times, values = controllers[20]
        if any(abs(float(np.dot(values[0], value))) < .999999 for value in values):
            raise ValueError('The carry is not static: ' + body + '/' + name)
        result[name] = {'rotation': np.array(values[0])}
    return result


def unit(value):
    return value / np.linalg.norm(value)


def bow_frame(draw, facing):
    """Align measured hair and ribbon normal, keeping the hair across the strings."""
    first = rifle.rotation_between(HAIR_AXIS, draw)
    actual = first @ HAIR_NORMAL
    actual = unit(actual - draw * np.dot(actual, draw))
    desired = unit(facing - draw * np.dot(facing, draw))
    angle = math.atan2(np.dot(draw, np.cross(actual, desired)), np.dot(actual, desired))
    q = np.r_[draw * math.sin(angle / 2), math.cos(angle / 2)]
    return rifle.quat_matrix(q) @ first


def solve_pose(rig, carry, sex, clip, time, length, profile):
    """Reach the measured string point using rotation-only two-bone IK."""
    base, seed_z, pole_y, pole_z = profile
    left = rig.world('lhand', time, carry)
    string = left[:3, :3] @ STRING_AXIS[sex]
    draw = np.array((-.95, 0, seed_z))
    draw = unit(draw - string * np.dot(draw, string))
    prop = bow_frame(draw, left[:3, 1])
    cycles = 4 if clip == 'sw_vio_loop' else 3 if clip == 'sw_vio_flour' else 1
    stroke = min(STROKE[clip], base - .11) * (1 - math.cos(2 * math.pi * cycles * time / length)) / 2
    hair = HAIR_START + HAIR_AXIS * (base - stroke)
    hook = rig.local('rhand', time, carry)
    wrist_rotation = prop @ hook[:3, :3].T
    target = left[:3, 3] + left[:3, :3] @ CONTACT[sex] - prop @ hair - wrist_rotation @ hook[:3, 3]
    w1, w2, w3 = (rig.world(name, time, carry) for name in ARM)
    parent_name = rig.nodes[rig.nodes[rig.index[ARM[0]]][1]][0]
    torso = rig.world(parent_name, time, carry)
    shoulder, elbow0, wrist0 = w1[:3, 3], w2[:3, 3], w3[:3, 3]
    upper, lower = np.linalg.norm(elbow0 - shoulder), np.linalg.norm(wrist0 - elbow0)
    distance = np.linalg.norm(target - shoulder)
    if not abs(upper - lower) + .00001 < distance < upper + lower - .00001:
        raise ValueError('Unreachable bow contact')
    direction = (target - shoulder) / distance
    along = (upper * upper - lower * lower + distance * distance) / (2 * distance)
    radius = math.sqrt(max(0, upper * upper - along * along))
    pole = torso[:3, :3] @ np.array((1.3, pole_y, pole_z))
    pole = unit(pole - direction * np.dot(pole, direction))
    elbow = shoulder + direction * along + pole * radius
    d1 = rifle.rotation_between(elbow0 - shoulder, elbow - shoulder)
    r1 = d1 @ w1[:3, :3]
    r2 = rifle.rotation_between(d1 @ (wrist0 - elbow0), target - elbow) @ d1 @ w2[:3, :3]
    # Let the forearm carry roll instead of forcing it through the wrist.
    rest = rig.nodes[rig.index['rhand_g']][2][20][1][0]
    correction = rifle.matrix_quat(wrist_rotation @ (r2 @ rifle.quat_matrix(rest)).T)
    arm_axis = unit(target - elbow)
    twist = np.r_[arm_axis * np.dot(correction[:3], arm_axis), correction[3]]
    if np.linalg.norm(twist) > .0001:
        r2 = rifle.quat_matrix(twist / np.linalg.norm(twist)) @ r2
    rotations = (torso[:3, :3].T @ r1, r1.T @ r2, r2.T @ wrist_rotation)
    override = {**carry, **{name: {'rotation': rifle.matrix_quat(value)} for name, value in zip(ARM, rotations)}}
    actual = rig.world('rhand', time, override)
    error = np.linalg.norm(actual[:3, 3] + actual[:3, :3] @ hair - left[:3, 3] - left[:3, :3] @ CONTACT[sex])
    wrist = rifle.angle_degrees(rotations[2] @ rifle.quat_matrix(rest).T)
    # Outward elbow clearance relative to the shoulder in torso space.
    clearance = (torso[:3, :3].T @ (elbow - shoulder))[0]
    return override, dict(contact_mm=float(error * 1000), wrist_degrees=wrist,
                          hair_fraction=float((base - stroke) / np.linalg.norm(HAIR_END - HAIR_START)),
                          elbow_clearance=float(clearance))


def solve_body(library, body):
    """Choose one continuous bow plane for all five clips of this body."""
    parent = original_parent(library, body)
    carry = carry_rotations(library, body, parent)
    sources = {clip: library.clip(body, clip, parent) for clip in CLIPS}
    rigs = {clip: rifle.Rig(rifle.skeleton_nodes(library.data(body)), source.clips[clip],
                           rifle.animation_scale(library.data(body))) for clip, (_, source) in sources.items()}
    candidates = []
    for base in (.50, .45, .40, .35, .30):
        for seed_z in (.30, .15, 0, -.15, .45):
            for pole_y, pole_z in ((.4, -.4), (.2, 0), (.4, .4), (0, -.4), (-.4, 0)):
                profile = (base, seed_z, pole_y, pole_z)
                reports = []
                try:
                    for clip in MAIN:
                        length = sources[clip][1].clips[clip][0]
                        for time in np.linspace(0, length, 17):
                            _, report = solve_pose(rigs[clip], carry, body[1], clip, time, length, profile)
                            reports.append(report)
                    wrist = max(r['wrist_degrees'] for r in reports)
                    if (wrist <= 60 and min(r['elbow_clearance'] for r in reports) > -.025
                            and min(r['hair_fraction'] for r in reports) >= .15
                            and max(r['hair_fraction'] for r in reports) <= .9):
                        candidates.append((abs(base - .50) * 100 + abs(seed_z - .30) * 15 + wrist * .1, profile))
                except ValueError:
                    continue
        if candidates:
            break
    if not candidates:
        raise ValueError('No reachable, neutral-wrist bow plane: ' + body)
    profile = min(candidates)[1]
    results, reports = [], []
    for clip in MAIN:
        rig = rigs[clip]
        length = sources[clip][1].clips[clip][0]
        tracks = {name: [] for name in ARM}
        for time in np.linspace(0, length, round(length * 60) + 1):
            override, report = solve_pose(rig, carry, body[1], clip, time, length, profile)
            reports.append(report)
            for name in ARM:
                tracks[name].append((float(time), None, override[name]['rotation']))
        results.append(dict(clip=clip, owner=sources[clip][0], source=sources[clip][1], tracks=tracks))
        for suffix in ('_in', '_out'):
            name = clip + suffix
            source = sources[name][1]
            transition = source.clips[name][0]
            posed = rigs[name]
            rows = {joint: [] for joint in ARM}
            for time in np.linspace(0, transition, round(transition * 60) + 1):
                weight = time / transition if suffix == '_in' else 1 - time / transition
                weight = weight * weight * (3 - 2 * weight)
                for joint in ARM:
                    neutral = rifle.matrix_quat(posed.local(joint, 0 if suffix == '_in' else transition)[:3, :3])
                    rows[joint].append((float(time), None, rifle.slerp(neutral, tracks[joint][0][2], weight)))
            results.append(dict(clip=name, owner=sources[name][0], source=source, tracks=rows))
    worst = max(r['wrist_degrees'] for r in reports)
    if (worst > 60 or max(r['contact_mm'] for r in reports) > .01
            or min(r['hair_fraction'] for r in reports) < .15
            or max(r['hair_fraction'] for r in reports) > .9):
        raise ValueError('Full-rate contact validation failed: ' + body)
    return results, dict(body=body, parent=parent, profile=profile, wrist_max=worst,
                         contact_max_mm=max(r['contact_mm'] for r in reports))


def clip_lines(result, bank):
    """Emit all inherited tracks, replacing only the three arm rotations."""
    name, source = result['clip'], result['source']
    header = rifle.animation_header(source.data, name)
    nodes = source.clips[name][1]
    lines = [f'newanim {name} {bank}', '  length ' + rifle.fmt(header['length']),
             '  transtime ' + rifle.fmt(header['transtime']), '  animroot ' + header['root']]
    lines += [f'  event {rifle.fmt(time)} {event}' for time, event in header['events']]
    for index, (joint, parent, _, controllers, _) in enumerate(nodes):
        lines += [f'  node dummy {bank if index == 0 else joint}',
                  '    parent ' + ('NULL' if parent is None else bank if parent == 0 else nodes[parent][0])]
        if set(controllers) - {8, 20}:
            raise ValueError('Unexpected inherited channel')
        if 8 in controllers:
            times, values = controllers[8]
            lines += rifle.key_lines('position', [(t, *v) for t, v in zip(times, values)])
        if joint in result['tracks']:
            lines += rifle.key_lines('orientation', [(t, *rifle.axis_angle(q)) for t, _, q in result['tracks'][joint]])
        elif 20 in controllers:
            times, values = controllers[20]
            lines += rifle.key_lines('orientation', [(t, *rifle.axis_angle(q)) for t, q in zip(times, values)])
        lines.append('  endnode')
    return lines + [f'doneanim {name} {bank}']


def generate(library, body):
    """Return an ASCII bank and compact proof without modifying active resources."""
    results, report = solve_body(library, body)
    bank, parent = bank_name(body), report['parent']
    lines = [f'# SWLOR native violin contact bank {bank}', f'newmodel {bank}',
             f'setsupermodel {bank} {parent}', 'classification character', 'setanimationscale 1',
             f'beginmodelgeom {bank}']
    lines += rifle.geometry_lines(rifle.bank_geometry(library.model(parent, False), results), bank)
    lines += [f'endmodelgeom {bank}']
    for result in results:
        lines += clip_lines(result, bank)
    return ('\n'.join(lines + [f'donemodel {bank}', ''])).encode('ascii'), report


def restore_tracks(library, body):
    """Restore native part IDs and exact float bits outside our arm rotations."""
    data = bytearray(library.data(bank_name(body)))
    bank = rifle.Model(bytes(data))
    for clip in CLIPS:
        _, source = library.clip(body, clip, original_parent(library, body))
        ours, theirs = bank.clips[clip][1], source.clips[clip][1]
        if [(n[0], n[1]) for n in ours[1:]] != [(n[0], n[1]) for n in theirs[1:]]:
            raise ValueError('Compiled node binding changed')
        for ours_node, source_node in zip(ours[1:], theirs[1:]):
            name, _, _, _, offset = ours_node
            struct.pack_into('<i', data, offset + 28, source_node[2])
            their_controllers = {row[0]: row for row in rifle.raw_controllers(source.data, source_node[4])}
            for kind, rows, times, values, columns, _ in rifle.raw_controllers(data, offset):
                if kind == 20 and name in ARM:
                    continue
                _, sr, st, sv, sc, _ = their_controllers[kind]
                if (rows, columns) != (sr, sc):
                    raise ValueError('Inherited controller layout changed')
                data[times:times+rows*4] = source.data[st:st+rows*4]
                data[values:values+rows*columns*4] = source.data[sv:sv+rows*columns*4]
    return bytes(data)


def rotation_changes(library, body):
    """Prove that each new clip changes only the three intended quaternion tracks."""
    bank = library.model(bank_name(body))
    if set(bank.clips) != set(CLIPS):
        raise ValueError('Unexpected violin-bank animation inventory')
    changes = {}
    for clip in CLIPS:
        _, source = library.clip(body, clip, original_parent(library, body))
        if rifle.animation_header(bank.data, clip) != rifle.animation_header(source.data, clip):
            raise ValueError('Violin clip metadata changed')
        ours, theirs = bank.clips[clip][1][1:], source.clips[clip][1][1:]
        if len(ours) != len(theirs):
            raise ValueError('Violin clip node inventory changed')
        for left, right in zip(theirs, ours):
            if left[:3] != right[:3] or left[3].keys() != right[3].keys():
                raise ValueError('Violin clip native binding changed')
            for kind, before in left[3].items():
                after = right[3][kind]
                if sharing.controllers_equal({kind: before}, {kind: after}):
                    continue
                if left[0] not in ARM or kind != 20:
                    raise ValueError('Violin fit changed a protected controller: ' + left[0])
                changes.setdefault(clip, {})[left[0]] = (before, after)
    return changes


def violin_robe_extension(manifest):
    """Select the Performance layer that owns every native violin clip."""
    matches = [record for record in manifest.get('animation_extensions', [])
               if set(CLIPS) <= set(record.get('clips', []))]
    if len(matches) != 1:
        raise ValueError('Expected one owned violin robe extension')
    return matches[0]


def update_robes(library, output, apply):
    """Copy verified arm quaternions to seven owned robe layers without rebuilding rigs."""
    import ExtendRobeAnimations as extension
    import GenerateRobeRgbModels as robes
    import RobeAnimationBanks as banks
    import RobePoseAudit as poses

    original = robes.MANIFEST.read_bytes()
    manifest = json.loads(original)
    violin_extension = violin_robe_extension(manifest)
    allowed = {f'Robe RGB input/output changed: {rifle_archives.bank_directory(body)}/{rifle_tail(library, body)[0]}.mdl; regenerate and validate'
               for body in BODIES}
    allowed.add('Robe RGB input/output changed: tools/GenerateViolinBowContact.py; regenerate and validate')
    errors = set(robes.check()) - allowed
    if errors:
        raise ValueError('Unrelated robe inputs changed: ' + '; '.join(sorted(errors)))
    hashes, snapshots, outputs, records = {}, {}, {}, {}
    count = 0
    active = robes.fresh_active_models()
    for body in BODIES:
        tail, data, parent = rifle_tail(library, body)
        path = rifle_archives.bank_directory(body) + '/' + tail + '.mdl'
        if parent != bank_name(body):
            raise ValueError('Finalize the compiled violin bank before updating robes: ' + body)
        before = bytearray(data)
        before[180:244] = base_parent(library, body, parent).encode('ascii').ljust(64, b'\0')
        expected = manifest['files'].get(path)
        if expected is None:
            profiles = json.loads((ROOT / 'tools/RifleGripProfileMotions.json').read_text())
            record = next((row for row in profiles['models'] if row['body'] == body), None)
            expected = next((part['sha256'] for part in rifle_archives.part_records(record)
                             if part['output'] == path), None) if record else UNTRACKED_RIFLE_HASHES.get(body)
        if expected is None or digest(data) != expected and digest(before) != expected:
            raise ValueError('The rifle bank changed beyond its parent: ' + body)
        hashes[path] = digest(data)
        bank_path = 'sw_cr_creature/' + bank_name(body) + '.mdl'
        hashes[bank_path] = digest(library.data(bank_name(body)))
        targets = [name for name in violin_extension['parents'].values()
                   if name[:3] == body[:3]]
        if body == 'pfz0' and not targets:
            rotation_changes(library, body)
            continue  # Droid appearances do not use wearable robe render phenotypes.
        if len(targets) != 1:
            raise ValueError('Expected one owned robe extension per body')
        name = targets[0]
        before = robes.owned_animation_bytes(name, manifest, active)
        snapshots[name] = digest(before)
        wanted = rotation_changes(library, body)
        actual = poses.Model(before)
        selected = {}
        for clip, nodes in wanted.items():
            current = {node[0]: node[3] for node in actual.clips[clip][1]}
            for joint, (old, new) in nodes.items():
                value = current[joint][20]
                if sharing.controllers_equal({20: value}, {20: new}):
                    continue
                if not sharing.controllers_equal({20: value}, {20: old}):
                    raise ValueError('Unexpected prior robe arm motion: ' + name + '/' + clip)
                selected.setdefault(clip, {})[joint] = (old, new)
        after, channels = extension.revise_rotations(before, selected) if selected else (before, 0)
        count += channels
        parts = banks.split(after)
        if list(parts) != manifest['animation_bank_sets'][name]['parts']:
            raise ValueError('The robe bank partition changed')
        for part, payload in parts.items():
            outputs[f'{robes.model_directory(part)}/{part}.mdl'] = payload
        records[name] = {'parts': list(parts), 'source_sha256': digest(after)}
    def load(name):
        path = f'{robes.model_directory(name)}/{name}.mdl'
        return outputs[path] if path in outputs else library.data(name)
    samples = 0
    for name in records:
        posed = poses.Library(load)
        scoped = SimpleNamespace(model=posed.model, owners=posed.owners,
            clips=lambda model: {key: value for key, value in posed.clips(model).items() if key in CLIPS})
        samples += poses.validate_body_poses(name, name[:3] + '0', scoped)
        posed.model.cache_clear(); posed.clips.cache_clear(); posed.owners.cache_clear()
        del scoped, posed
        gc.collect()
    report = dict(input_manifest_sha256=digest(original), bodies=list(BODIES),
                  quaternion_channels=count, body_pose_samples=samples,
                  preservation='Only three right-arm rotation tracks changed in existing robe clips; rifle banks changed only their parent; body roots and shared weapon hooks are unchanged.')
    output.mkdir(parents=True, exist_ok=False)
    for path, data in outputs.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if apply:
        if robes.MANIFEST.read_bytes() != original:
            raise ValueError('Robe manifest changed during validation')
        for name, expected in snapshots.items():
            if digest(robes.owned_animation_bytes(name, manifest, active)) != expected:
                raise ValueError('Robe layer changed during validation')
        for path, expected in hashes.items():
            if digest((ROOT / path).read_bytes()) != expected:
                raise ValueError('Body bank changed during validation')
        for path, data in outputs.items():
            (ROOT / path).write_bytes(data)
            hashes[path] = digest(data)
        hashes['tools/GenerateViolinBowContact.py'] = robes.file_digest(Path(__file__))
        manifest['files'].update(hashes)
        manifest['animation_bank_sets'].update(records)
        violin_extension.setdefault('family_contact_revisions', []).append(report)
        robes.MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


def main():
    """Generate staged banks or finalize compiled banks without changing rifle motion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--body', action='append', choices=BODIES)
    parser.add_argument('--finalize', action='store_true')
    parser.add_argument('--update-robes', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and not args.update_robes:
        parser.error('--apply is only for --update-robes; generation writes staged ASCII')
    for path, expected in SOURCE_HASHES.items():
        if digest((ROOT / path).read_bytes()) != expected:
            raise ValueError('Measured prop or native carry changed: ' + path)
    library = Library(args.game_data)
    if args.update_robes:
        if args.body or args.finalize:
            parser.error('--update-robes requires all bodies and finalized banks')
        update_robes(library, args.output, args.apply)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for body in args.body or BODIES:
        if args.finalize:
            path = ROOT / 'sw_cr_creature' / (bank_name(body) + '.mdl')
            path.write_bytes(restore_tracks(library, body))
            library.invalidate(bank_name(body))
            rotation_changes(library, body)
            splice_violin(library, body)
            print('finalized', body, flush=True)
        else:
            data, report = generate(library, body)
            (args.output / (bank_name(body) + '.mdl')).write_bytes(data)
            reports.append(report)
            print(json.dumps(report), flush=True)
    if reports:
        (args.output / 'report.json').write_text(json.dumps(reports, indent=2) + '\n')


if __name__ == '__main__':
    main()
