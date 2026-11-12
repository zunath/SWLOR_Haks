"""Bounded authored motion packs and lossless native rifle-bank assembly."""
from pathlib import Path
import gzip
import io
import hashlib
import json
import re
import RobeAnimationBanks as banks

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


def archive_record(record, archive, root):
    relative = archive['file']
    if not re.fullmatch(r'tools/rifle_profile_motions/[a-z0-9_-]+\.json\.gz', relative):
        raise ValueError('Invalid authored rifle motion archive path')
    path = root / relative
    if not path.resolve().is_relative_to((root / 'tools/rifle_profile_motions').resolve()):
        raise ValueError('Authored rifle motion archive leaves its source directory')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != archive['sha256']:
        raise ValueError('Authored rifle motion archive changed')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        payload = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError('Authored rifle motion archive exceeds its bounded batch size')
    data = json.loads(payload)
    if set(data) != {'body', 'motions', 'abilities'} or data['body'] != record['body']:
        raise ValueError('Authored rifle motion archive has the wrong body or fields')
    return {**record, 'motions': data['motions'], 'abilities': data['abilities']}


def profile_names(record):
    result = {motion['name'] for motion in record['motions']}
    result.update(motion['name'][:-1] + 'b' for motion in record['motions'] if motion['sourceClip'] == 'xbowr')
    for ability in record['abilities']:
        result.update(ability['name'] + suffix for suffix in ['', '_in', '_out'])
    return result


def records(record, root):
    yield record
    for archive in record.get('profileArchives', []):
        yield archive_record(record, archive, root)


def validate_sources(record, owner):
    original = owner.baseline(record)
    names = set(owner.hold.CLIPS)
    for group in records(record, owner.ROOT):
        # An empty inline group is allowed only when complete groups live in archives.
        if not group['motions'] and group is record and record.get('profileArchives'):
            if group['abilities']:
                raise ValueError('Rifle abilities require their matching inline motions')
            continue
        owner.validate_motions(group, original)
        owner.validate_abilities(group)
        added = profile_names(group)
        if added & names:
            raise ValueError('Duplicate authored rifle profile across motion archives')
        names.update(added)
    return original, names


def bank_directory(body):
    # Keep the three largest normal-body inventories in the numbered overflow HAK.
    return 'sw_cr_creature1' if body in {'pfz0', 'pmz0', 'pmo0'} else 'sw_cr_creature'


def part_records(record):
    parts = record.get('bankParts') or [{'output': record['output'], 'sha256': record['outputSha256']}]
    head = bank_directory(record['body']) + '/a_rfl_' + record['body']
    for index, part in enumerate(parts):
        expected = head + (f'_b{index:02d}' if index else '') + '.mdl'
        if part['output'] != expected or not re.fullmatch('[0-9a-f]{64}', part['sha256']):
            raise ValueError('Invalid owned rifle animation bank partition')
    return parts


def verify_installed(record, owner):
    original, expected = validate_sources(record, owner)
    payloads = []
    for part in part_records(record):
        path = owner.ROOT / part['output']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != part['sha256']:
            raise ValueError('Owned rifle profile bank is missing or stale: ' + part['output'])
        payloads.append(path.read_bytes())
    flat = banks.join(payloads) if len(payloads) > 1 else payloads[0]
    if hashlib.sha256(flat).hexdigest() != record['outputSha256']:
        raise ValueError('Owned rifle bank partitions differ from the authored complete bank')
    if set(owner.Model(flat).clips) != expected:
        raise ValueError('Owned rifle bank differs from its complete authored profile inventory')
    owner.validate_profile_parts(flat, original)


def build_record(record, stage, game_dir, owner, dependencies=()):
    original, expected = validate_sources(record, owner)
    seed = None
    components = {}
    for index, group in enumerate(records(record, owner.ROOT)):
        if not group['motions'] and group is record:
            # Compile the source stock clips through the same native owner.
            base = {**record, 'motions': [], 'abilities': []}
            sources = (original, [(name, original, name) for name in owner.hold.CLIPS])
        else:
            base = group
            sources = None
        group_stage = stage / f'group-{index:04d}'
        group_stage.mkdir(parents=True, exist_ok=True)
        for dependency in dependencies:
            (group_stage / dependency.name).write_bytes(dependency.read_bytes())
        raw = owner.compile_record(base, group_stage, game_dir, source_pair=sources)
        compiled = banks.CompiledBridge(raw)
        if seed is None:
            seed = compiled
        elif seed.pack(seed.name, 'null', []) != compiled.pack(seed.name, 'null', []):
            raise ValueError('Authored rifle motion batch changed the native skeleton or metadata')
        for name, component in zip(compiled.clip_names, compiled.clips):
            if name in components:
                if name not in owner.hold.CLIPS or components[name].relocated(0, seed.name) != component.relocated(0, seed.name):
                    raise ValueError('Authored rifle motion batch changed or duplicated a native clip: ' + name)
            else:
                components[name] = component
    if set(components) != expected:
        raise ValueError('Compiled rifle archive omitted or added an authored profile')
    flat = bytes(seed.pack(seed.name, seed.parent, [components[name] for name in sorted(components)]))
    owner.validate_profile_parts(flat, original)
    if hashlib.sha256(flat).hexdigest() != record['outputSha256']:
        raise ValueError('Rifle archive output differs from its pinned complete payload')
    partitioned = banks.split(flat)
    expected_parts = part_records(record)
    actual = {bank_directory(record['body']) + '/' + name + '.mdl': bytes(data) for name, data in partitioned.items()}
    if set(actual) != {p['output'] for p in expected_parts}:
        raise ValueError('Rifle archive partition inventory differs from its pinned output')
    for part in expected_parts:
        if hashlib.sha256(actual[part['output']]).hexdigest() != part['sha256']:
            raise ValueError('Rifle archive partition differs from its pinned payload')
    return actual
