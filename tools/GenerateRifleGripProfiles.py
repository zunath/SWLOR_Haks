"""Reproduce owned rifle profiles while preserving the original native rifle clips.

The JSON contains authored quaternion keys, pinned baseline banks and compiler
inputs. Private weapon measurements/contact reports are not generation inputs.
"""
from __future__ import annotations
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import numpy as np
import CleanModels
import GenerateRifleHoldAnimations as hold
from RobePoseAudit import Model
from RobeAnimations import validate_animation_parts

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools' / 'RifleGripProfileMotions.json'
ALLOWED_JOINTS = frozenset((*hold.ARM, 'rhand_g'))
ROLES = {'i': 'xbowr', 'r': 'xbowrdy', 's': 'xbowshot'}

@lru_cache(None)
def pinned_source(blob):
    if not re.fullmatch(r'[0-9a-f]{40}', blob):
        raise ValueError('Motion sources require an immutable Git blob')
    return subprocess.check_output(['git', '-C', str(ROOT), 'cat-file', 'blob', blob])


def baseline(record):
    body = record['body']
    expected = 'sw_cr_creature/a_rfl_' + body + '.mdl'
    if not re.fullmatch(r'p[fm][hedoz]0', body) or record['output'] != expected:
        raise ValueError('Rifle profiles require a normal playable body bank')
    raw = pinned_source(record['sourceBlob'])
    if hashlib.sha256(raw).hexdigest() != record['sourceSha256']:
        raise ValueError('Pinned rifle baseline changed')
    model = Model(raw)
    if set(model.clips) != set(hold.CLIPS) or struct.unpack_from('<I', raw, 8)[0]:
        raise ValueError('Rifle baseline must contain only the three stock dummy clips')
    return model


def validate_motions(record, original):
    if record.get('profileHookBasis') != 'idle':
        raise ValueError('Rifle profiles require the pinned idle hook basis')
    idle_hook_quaternion(original)
    seen, groups = set(), {}
    for motion in record['motions']:
        name, source_clip = motion['name'], motion['sourceClip']
        if not re.fullmatch(r'rf[a-z0-9_]{1,12}_[irs]', name) or len(name) > 16 or name in seen:
            raise ValueError('Invalid or duplicate native rifle profile name')
        if source_clip != ROLES[name[-1]]:
            raise ValueError('Rifle profile role differs from its source clip')
        seen.add(name)
        groups.setdefault(name[:-2], set()).add(name[-1])
        rotations = motion['rotations']
        if not set(hold.ARM) <= set(rotations) or set(rotations) - ALLOWED_JOINTS:
            raise ValueError('Rifle profiles may rotate only the support arm and right wrist')
        source_nodes = {n: controllers for n, _, _, controllers, _ in original.clips[source_clip][1]}
        for joint, curve in rotations.items():
            times, values = np.asarray(curve['times'], float), np.asarray(curve['values'], float)
            if joint not in source_nodes or 20 not in source_nodes[joint]:
                raise ValueError('Rifle profile adds a missing native controller')
            if times.ndim != 1 or not len(times) or values.shape != (len(times), 4):
                raise ValueError('Rifle quaternion keys have an invalid shape')
            if not np.isfinite(times).all() or not np.isfinite(values).all():
                raise ValueError('Rifle quaternion keys must be finite')
            if (np.diff(times) <= 0).any() or times[0] < 0 or times[-1] > original.clips[source_clip][0] + 1e-6:
                raise ValueError('Rifle quaternion times must increase within the clip')
            if np.max(np.abs(np.linalg.norm(values, axis=1) - 1)) > 1e-5:
                raise ValueError('Rifle rotation keys must be unit quaternions')
    if not groups or any(roles != set(ROLES) for roles in groups.values()):
        raise ValueError('Every rifle profile requires idle, ready and shot motion')


def idle_hook_quaternion(original):
    """Use the pinned idle attachment basis, without permitting authored hook keys."""
    defaults = next(n[3] for n in original.nodes if n[0] == 'rhand')
    idle = next(n[3] for n in original.clips['xbowr'][1] if n[0] == 'rhand')
    curve = idle.get(20, defaults.get(20))
    if curve is None:
        raise ValueError('Pinned idle hook requires an orientation')
    values = np.asarray(curve[1], dtype='<f4')
    if (values.ndim != 2 or values.shape[1] != 4 or not len(values)
            or not np.isfinite(values).all()
            or not np.array_equal(values, np.tile(values[0], (len(values), 1)))
            or abs(np.linalg.norm(values[0]) - 1) > 1e-5):
        raise ValueError('Pinned idle hook orientation must be constant and unit')
    return values[0].tobytes()


def profile_model(original, motion):
    """Copy owned rotations and normalize the custom ready/shot attachment basis."""
    raw = original.data
    output = bytearray(raw)
    def append(data):
        output.extend(bytes((-len(output)) % 4))
        pointer = len(output) - 12
        output.extend(data)
        return pointer
    hook_value = idle_hook_quaternion(original)
    for joint, _, _, _, offset in original.clips[motion['sourceClip']][1]:
        normalize_hook = joint == 'rhand' and motion['sourceClip'] != 'xbowr'
        if joint not in motion['rotations'] and not normalize_hook:
            continue
        curve = motion['rotations'].get(joint)
        key_pointer = 12 + struct.unpack_from('<I', raw, offset + 84)[0]
        keys, data = [], bytearray()
        for index, (kind, rows, times, values, columns, _) in enumerate(hold.raw_controllers(raw, offset)):
            if kind == 20 and normalize_hook:
                if columns != 4:
                    raise ValueError('Rifle hook orientation must be a quaternion')
                time_bytes = raw[times:times + rows * 4]
                value_bytes = hook_value * rows
            elif kind == 20:
                rows = len(curve['times'])
                time_bytes = np.asarray(curve['times'], dtype='<f4').tobytes()
                value_bytes = np.asarray(curve['values'], dtype='<f4').tobytes()
            else:
                time_bytes = raw[times:times + rows * 4]
                value_bytes = raw[values:values + rows * columns * 4]
            time_index = len(data) // 4
            data.extend(time_bytes)
            value_index = len(data) // 4
            data.extend(value_bytes)
            if max(rows, time_index, value_index) >= 65536:
                raise ValueError('Rifle curve exceeds native controller index capacity')
            key = bytearray(raw[key_pointer + index * 12:key_pointer + (index + 1) * 12])
            struct.pack_into('<HHH', key, 4, rows, time_index, value_index)
            keys.append(key)
        data_pointer, new_keys = append(data), append(b''.join(keys))
        struct.pack_into('<III', output, offset + 84, new_keys, len(keys), len(keys))
        struct.pack_into('<III', output, offset + 96, data_pointer, len(data) // 4, len(data) // 4)
    struct.pack_into('<I', output, 4, len(output) - 12)
    return Model(bytes(output))


def sources(record):
    original = baseline(record)
    validate_motions(record, original)
    clips = [(name, original, name) for name in hold.CLIPS]
    clips += [(motion['name'], profile_model(original, motion), motion['sourceClip'])
              for motion in record['motions']]
    return original, sorted(clips, key=lambda item: item[0])


def ascii_source(record, original, clips):
    name = Path(record['output']).stem
    lines = [f'newmodel {name}', f'setsupermodel {name} {original.parent}',
             'classification character', 'setanimationscale 1', f'beginmodelgeom {name}']
    for node, parent, _, controllers, _ in original.nodes:
        if set(controllers) - {8, 20, 36}:
            raise ValueError('Unexpected rifle skeleton default controller')
        parent_name = 'NULL' if parent is None else original.nodes[parent][0]
        lines += [f'node dummy {node}', f' parent {parent_name}']
        for kind, label in [(8, 'position'), (20, 'orientation'), (36, 'scale')]:
            if kind in controllers:
                values = controllers[kind][1][0]
                values = hold.axis_angle(values) if kind == 20 else values
                lines.append(' ' + label + ' ' + ' '.join(hold.fmt(v) for v in values))
        lines.append('endnode')
    lines.append(f'endmodelgeom {name}')
    for clip_name, model, source_clip in clips:
        emitted = hold.clip_lines({'source': model, 'clip': source_clip, 'tracks': {}, 'owner': name}, name)
        emitted[0], emitted[-1] = f'newanim {clip_name} {name}', f'doneanim {clip_name} {name}'
        lines += emitted
    lines.append(f'donemodel {name}')
    return '\n'.join(lines) + '\n'


def restore_native(binary, original, clips):
    """Restore source IDs and float bytes after the native compiler's conversion."""
    output, writes = bytearray(binary), {}
    compiled = Model(binary)
    groups = [(compiled.nodes, original.nodes, original.data)]
    groups += [(compiled.clips[name][1], model.clips[source_clip][1], model.data)
               for name, model, source_clip in clips]
    for ours, theirs, source in groups:
        if [(n, p) for n, p, _, _, _ in ours] != [(n, p) for n, p, _, _, _ in theirs]:
            raise ValueError('Compiled rifle node hierarchy changed')
        for (joint, _, _, _, at), (_, _, part, _, old_at) in zip(ours, theirs):
            struct.pack_into('<i', output, at + 28, part)
            our_controllers = hold.raw_controllers(binary, at)
            old_controllers = hold.raw_controllers(source, old_at)
            if [(k, r, c) for k, r, _, _, c, _ in our_controllers] != [(k, r, c) for k, r, _, _, c, _ in old_controllers]:
                raise ValueError('Compiled rifle controller layout changed: ' + joint)
            for (_, rows, times, values, columns, _), (_, _, old_times, old_values, _, _) in zip(our_controllers, old_controllers):
                for target, start, count in [(times, old_times, rows * 4), (values, old_values, rows * columns * 4)]:
                    for index, value in enumerate(source[start:start + count]):
                        if target + index in writes and writes[target + index] != value:
                            raise ValueError('Native rifle compiler aliased different controller values')
                        writes[target + index] = value
                        output[target + index] = value
    restored = Model(output)
    if [(n, p, i, c) for n, p, i, c, _ in restored.nodes] != [(n, p, i, c) for n, p, i, c, _ in original.nodes]:
        raise ValueError('Rifle skeleton defaults or native bindings changed')
    for name, model, source_clip in clips:
        if [(n, p, i, c) for n, p, i, c, _ in restored.clips[name][1]] != [(n, p, i, c) for n, p, i, c, _ in model.clips[source_clip][1]]:
            raise ValueError('Rifle motion changed during native restoration: ' + name)
        if hold.animation_header(bytes(output), name) != hold.animation_header(model.data, source_clip):
            raise ValueError('Rifle clip timing, root or events changed')
    struct.pack_into('<I', output, 88, max(struct.unpack_from('<I', output, 88)[0], max(n[2] for n in restored.nodes) + 1))
    validate_animation_parts(output)
    return bytes(output)


def build(manifest, stage, game_dir):
    if manifest['compilerSha256'] != CleanModels.COMPILER_SHA256:
        raise ValueError('Rifle profiles require the pinned native compiler')
    stage.mkdir(parents=True, exist_ok=True)
    for dependency in manifest['dependencies']:
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,15}\.mdl', dependency['name']):
            raise ValueError('Invalid rifle compiler dependency name')
        raw = pinned_source(dependency['blob'])
        if hashlib.sha256(raw).hexdigest() != dependency['sha256']:
            raise ValueError('Pinned rifle compiler dependency changed')
        (stage / dependency['name']).write_bytes(raw)
    output, generated = stage / 'binary', {}
    output.mkdir(exist_ok=True)
    compiler = CleanModels.find_compiler()
    for record in manifest['models']:
        original, clips = sources(record)
        path = stage / Path(record['output']).name
        path.write_text(ascii_source(record, original, clips), encoding='ascii')
        CleanModels.run(compiler, stage, 'compile', [path], output, 'compile-' + record['body'] + '.log', game_dir=game_dir)
        target = output / path.name
        raw = restore_native(target.read_bytes(), original, clips)
        if hashlib.sha256(raw).hexdigest() != record['outputSha256']:
            raise ValueError('Rifle profile output differs from its pinned payload: ' + record['body'])
        target.write_bytes(raw)
        CleanModels.validate_binary(target)
        generated[record['output']] = raw
    return generated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', type=Path)
    parser.add_argument('--output', type=Path, help='Stage generated models outside the repository')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    if args.check:
        for record in manifest['models']:
            original = baseline(record)
            validate_motions(record, original)
            target = ROOT / record['output']
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != record['outputSha256']:
                raise ValueError('Owned rifle profile bank is missing or stale: ' + record['output'])
        print('Owned rifle profile sources and installed payloads verified.')
        return
    if not args.game_dir:
        parser.error('--game-dir is required to generate native rifle profiles')
    with tempfile.TemporaryDirectory(prefix='swlor-rifle-profiles-') as folder:
        generated = build(manifest, Path(folder), args.game_dir)
        for relative, raw in generated.items():
            target = ROOT / relative if args.apply else (args.output or Path(folder)) / Path(relative).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            print('verified', relative, hashlib.sha256(raw).hexdigest())
        if not args.apply and not args.output:
            print('Verification completed; pass --output or --apply to retain the verified models.')


if __name__ == '__main__':
    main()
