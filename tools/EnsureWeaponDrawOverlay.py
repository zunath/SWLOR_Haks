"""Keep native weapon attachment events without the cosmetic draw arm motion."""
from pathlib import Path
import argparse
import struct
from RobePoseAudit import Model
import EnsureWeaponCarryOverlay as carry

NAME = "sw_gundraw"
LENGTH = 0.667
EVENT_TIME = 0.3
OWNER = carry.OWNER


def header(model, name):
    for index in range(model.uint(136)):
        offset = 12 + model.uint(12 + model.uint(132) + index * 4)
        if model.data[offset+8:offset+72].split(b"\0", 1)[0].decode().lower() == name:
            return offset
    raise ValueError("Missing weapon draw animation: " + name)


def validate(data):
    model = Model(data)
    offset = header(model, NAME)
    length, nodes = model.clips[NAME]
    if abs(length-LENGTH) > 1e-6 or len(nodes) != 1 or nodes[0][0] != OWNER:
        raise ValueError("Weapon draw must retain stock duration and one empty root")
    node = nodes[0][4]
    if model.uint(node+108) != 1 or nodes[0][2] != 0:
        raise ValueError("Weapon draw root must be a dummy with part ID zero")
    if any(model.uint(node+x) for x in range(64, 108, 4)):
        raise ValueError("Weapon draw must not move skeleton joints")
    if struct.unpack_from("<f", data, offset+116)[0] != 0:
        raise ValueError("Weapon draw must have zero transition time")
    if model.uint(offset+188) != 2 or model.uint(offset+192) != 2:
        raise ValueError("Both stock draw weapon events are required")
    for index in range(2):
        event = 12 + model.uint(offset+184) + index*36
        time = struct.unpack_from("<f", data, event)[0]
        name = data[event+4:event+36].split(b"\0", 1)[0]
        if abs(time-EVENT_TIME) > 1e-6 or name != b"draw_weapon":
            raise ValueError("Weapon draw attachment event changed")
    carry.validate_overlay(data)
    return model


def ensure(data):
    old, source_header, source_node = carry.validate_overlay(data)
    if NAME in old.clips:
        validate(data)
        return data
    size, raw_size = struct.unpack_from("<II", data, 4)
    if len(data) != 12+size+raw_size:
        raise ValueError("Invalid compiled model section sizes")
    count = old.uint(136)
    array = data[12+old.uint(132):12+old.uint(132)+count*4]
    new_header = size+(count+1)*4
    new_node = new_header+196
    new_events = new_node+112
    animation = bytearray(data[source_header:source_header+196])
    animation[8:72] = NAME.encode().ljust(64, b"\0")
    struct.pack_into("<I", animation, 72, new_node)
    struct.pack_into("<f", animation, 112, LENGTH)
    struct.pack_into("<III", animation, 184, new_events, 2, 2)
    events = struct.pack("<f32s", EVENT_TIME, b"draw_weapon")*2
    addition = array+struct.pack("<I", new_header)+animation+data[source_node:source_node+112]+events
    result = bytearray(data[:12+size]+addition+data[12+size:])
    struct.pack_into("<I", result, 4, size+len(addition))
    struct.pack_into("<III", result, 132, size, count+1, count+1)
    after = validate(bytes(result))
    # Existing geometry, controller data, clip headers and raw bytes remain exact.
    preserved = bytearray(result[:12+size])
    for start, length in ((4, 4), (132, 12)):
        preserved[start:start+length] = data[start:start+length]
    if preserved != data[:12+size] or data[12+size:] != result[12+after.uint(4):]:
        raise ValueError("Existing model bytes changed")
    if old.nodes != after.nodes or any(after.clips.get(k) != v for k, v in old.clips.items()):
        raise ValueError("Existing skeleton or motion changed")
    if set(after.clips) != set(old.clips) | {NAME}:
        raise ValueError("Unexpected animation inventory change")
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1]/"sw_cr_creature"/(OWNER+".mdl")
    before = target.read_bytes()
    after = ensure(before)
    if args.check and after != before:
        raise ValueError("Weapon draw overlay is missing")
    if args.apply and after != before:
        if target.read_bytes() != before:
            raise ValueError("Common animation tail changed during generation")
        target.write_bytes(after)
    print("Weapon draw overlay validated; added bytes:", len(after)-len(before))


if __name__ == "__main__":
    main()
