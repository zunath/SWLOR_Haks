"""Copy missing root position tracks from compiled edits without reserializing other motion.

The native decompiler rounds rotations. Recompiling an entire legacy model can
therefore alter unrelated clips. This appends only the selected compiled position
controllers to the original model section; geometry, raw data and all existing
controller bytes are retained. Inputs and output are compiled NWN MDLs.
"""
import argparse
from pathlib import Path
import struct


def add_root_tracks(original, candidate, animations, root='rootdummy'):
    def uint(data, offset):
        return struct.unpack_from('<I', data, offset)[0]

    def inventory(data):
        if len(data) < 244 or data[:4] != bytes(4) or 12 + uint(data, 4) + uint(data, 8) != len(data):
            raise ValueError('Expected a complete compiled NWN model')
        result = {}
        for index in range(uint(data, 136)):
            animation = 12 + uint(data, 12 + uint(data, 132) + index * 4)
            name = data[animation + 8:animation + 72].split(b'\0', 1)[0].decode('ascii')
            result[name] = animation
        return result

    def find_node(data, animation):
        pending = [uint(data, animation + 72)]
        visited = set()
        matches = []
        while pending:
            pointer = pending.pop()
            if pointer in visited:
                raise ValueError('Repeated native animation node')
            visited.add(pointer)
            node = pointer + 12
            if node + 112 > 12 + uint(data, 4):
                raise ValueError('Animation node is outside model data')
            name = data[node + 32:node + 64].split(b'\0', 1)[0].decode('ascii')
            if name == root:
                matches.append(node)
            pending.extend(uint(data, 12 + uint(data, node + 72) + index * 4)
                           for index in range(uint(data, node + 76)))
        if len(matches) != 1:
            raise ValueError('Expected exactly one animation root: ' + root)
        return matches[0]

    def controllers(data, node):
        start, count, allocated = struct.unpack_from('<3I', data, node + 84)
        values, floats, capacity = struct.unpack_from('<3I', data, node + 96)
        end = uint(data, 4)
        if count != allocated or floats != capacity or start + count * 12 > end or values + floats * 4 > end:
            raise ValueError('Unexpected native controller arrays')
        return ([data[12 + start + i * 12:12 + start + (i + 1) * 12] for i in range(count)],
                data[12 + values:12 + values + floats * 4])

    old_clips, new_clips = inventory(original), inventory(candidate)
    if original[20:84] != candidate[20:84] or original[180:244] != candidate[180:244]:
        raise ValueError('Candidate model identity or parent changed')
    if not animations or set(old_clips) != set(new_clips) or not set(animations) <= old_clips.keys():
        raise ValueError('Expected existing named animations in the same model')
    model_end = 12 + uint(original, 4)
    result = bytearray(original[:model_end])
    for name in sorted(set(animations)):
        old_node = find_node(original, old_clips[name])
        new_node = find_node(candidate, new_clips[name])
        old_keys, old_values = controllers(original, old_node)
        new_keys, new_values = controllers(candidate, new_node)
        if any(uint(key, 0) == 8 for key in old_keys):
            raise ValueError('Original already has a root position track: ' + name)
        positions = [key for key in new_keys if uint(key, 0) == 8]
        if len(positions) != 1:
            raise ValueError('Candidate must have one root position track: ' + name)
        kind, rows, times, values, columns, padding = struct.unpack('<IHHHBB', positions[0])
        if columns != 3 or rows == 0 or max(times + rows, values + rows * 3) * 4 > len(new_values):
            raise ValueError('Expected ordinary three-column position keys')
        count = len(old_values) // 4
        if count + rows + rows * 3 > 65535:
            raise ValueError('Root controller exceeds native float index capacity')
        added_key = struct.pack('<IHHHBB', kind, rows, count, count + rows, columns, padding)
        keys = sorted([*old_keys, added_key], key=lambda key: uint(key, 0))
        key_offset = len(result) - 12
        result.extend(b''.join(keys))
        value_offset = len(result) - 12
        floats = (old_values + new_values[times * 4:(times + rows) * 4] +
                  new_values[values * 4:(values + rows * 3) * 4])
        result.extend(floats)
        struct.pack_into('<3I', result, old_node + 84, key_offset, len(keys), len(keys))
        struct.pack_into('<3I', result, old_node + 96, value_offset, len(floats) // 4, len(floats) // 4)
    struct.pack_into('<I', result, 4, len(result) - 12)
    result.extend(original[model_end:])
    return bytes(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--animation', action='append', required=True)
    args = parser.parse_args()
    args.output.write_bytes(add_root_tracks(args.original.read_bytes(), args.candidate.read_bytes(), args.animation))
