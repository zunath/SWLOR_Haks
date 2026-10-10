"""Stripped robe banks must keep the packed layout the bank parser and native compiler share."""
from pathlib import Path
import struct
import tempfile
import unittest

import CompileModels as mdl
import RobeAnimationBanks as banks
import RobePoseAudit as poses
import StripInheritedBoneTracks as strip_tool

NAME = "pmh_ra001"
ROTATION = " orientationkey 2\n 0 0 0 1 0.1\n 1 0 1 0 0.7\n"
# A bone transform before, after, between and instead of the rotation it must not disturb.
BONE_TRACKS = {
    "torso_g": " positionkey 2\n 0 0 0 0.5\n 1 0 0.1 0.6\n" + ROTATION,
    "neck_g": ROTATION + " scalekey 2\n 0 1\n 1 0.9\n",
    "head_g": " positionkey 1\n 0 0 0 0.2\n" + ROTATION + " scalekey 1\n 0 1.1\n",
    "lbicep_g": " positionkey 2\n 0 0.2 0 0\n 1 0.3 0 0\n",
}
OTHER_TRACKS = {"rootdummy": " positionkey 2\n 0 0 0 1\n 1 0 0.5 1\n", "rhand": " positionkey 1\n 0 0 0.1 0\n" + ROTATION}


def node(name, parent, values=""):
    return f"node dummy {name}\n parent {parent}\n{values}endnode\n"


def source(bone_tracks):
    parents = {"rootdummy": NAME, "torso_g": "rootdummy", "neck_g": "torso_g", "head_g": "neck_g",
               "lbicep_g": "torso_g", "rhand": "torso_g"}
    geometry = node(NAME, "NULL") + "".join(node(name, parent, " position 0 0 0.1\n")
                                            for name, parent in parents.items())
    clips = ""
    for clip in ("clip0", "clip1"):
        tracks = node(NAME, "NULL") + "".join(
            node(name, parent, {**OTHER_TRACKS, **bone_tracks}.get(name, "")) for name, parent in parents.items())
        clips += (f"newanim {clip} {NAME}\nlength 1\ntranstime 0.25\nanimroot {NAME}\n"
                  f"event 0.5 hit\n{tracks}doneanim {clip} {NAME}\n")
    return (f"newmodel {NAME}\nsetsupermodel {NAME} NULL\nclassification CHARACTER\nsetanimationscale 1\n"
            f"beginmodelgeom {NAME}\n{geometry}endmodelgeom {NAME}\n{clips}donemodel {NAME}\n")


def without_padding(data):
    """Zero the bytes the native compiler leaves uninitialized: key padding and event name tails."""
    result = bytearray(data)
    for _, animation_node in strip_tool.animation_nodes(data):
        start, count, _ = struct.unpack_from("<3I", data, animation_node + 84)
        for key in range(count):
            result[12 + start + key * 12 + 9:12 + start + key * 12 + 12] = bytes(3)
    for index in range(strip_tool.uint(data, 136)):
        animation = 12 + strip_tool.uint(data, 12 + strip_tool.uint(data, 132) + index * 4)
        events, count = struct.unpack_from("<II", data, animation + 184)
        for event in range(count):
            begin = 12 + events + event * 36 + 4
            zero = data.index(0, begin, begin + 32)
            result[zero:begin + 32] = bytes(begin + 32 - zero)
    return bytes(result)


class RobeBankStrippingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        stage = Path(cls.temporary.name)
        compiler, _ = mdl.prepare_compiler(stage)

        def compile_model(directory, text):
            (stage / directory / "binary").mkdir(parents=True)
            path = stage / directory / f"{NAME}.mdl"
            path.write_text(text)
            mdl.run_compiler(compiler, stage, ["-cne", str(path), str(stage / directory / "binary") + "/"],
                             f"{directory}.log")
            return (stage / directory / "binary" / path.name).read_bytes()

        cls.keyed = compile_model("keyed", source(BONE_TRACKS))
        rotations = {name: ROTATION if "orientationkey" in tracks else "" for name, tracks in BONE_TRACKS.items()}
        cls.expected = compile_model("expected", source(rotations))

    def test_in_place_strip_alone_is_not_a_packed_bank(self):
        stripped = strip_tool.strip(self.keyed)
        self.assertEqual(len(self.keyed), len(stripped))
        with self.assertRaisesRegex(ValueError, "Unexpected native array layout"):
            banks.CompiledBridge(stripped)

    def test_packed_strip_matches_a_native_compile_without_the_bone_transforms(self):
        packed = strip_tool.pack(strip_tool.strip(self.keyed))
        self.assertEqual([], strip_tool.offenders(packed))
        self.assertEqual(without_padding(self.expected), without_padding(packed))
        self.assertEqual(poses.Model(self.expected).clips, poses.Model(packed).clips)

    def test_packed_bank_splits_joins_and_leaves_other_nodes_alone(self):
        packed = strip_tool.pack(strip_tool.strip(self.keyed))
        self.assertEqual(packed, banks.join(list(banks.split(packed).values())))
        self.assertIs(packed, strip_tool.pack(packed))
        self.assertIs(self.keyed, strip_tool.pack(self.keyed))
        kept = lambda data: [track for track in strip_tool._tracks(data)
                             if strip_tool.name_at(track[1], 32) not in strip_tool.BONES]
        self.assertEqual(kept(self.keyed), kept(packed))

    def test_pack_rejects_offsets_into_removed_data(self):
        stripped = bytearray(strip_tool.strip(self.keyed))
        # Point the first clip's event array into a vacated key slot.
        animation = 12 + strip_tool.uint(stripped, 12 + strip_tool.uint(stripped, 132))
        for _, animation_node in strip_tool.animation_nodes(bytes(stripped)):
            start, count, _ = struct.unpack_from("<3I", stripped, animation_node + 84)
            floats = strip_tool.uint(stripped, animation_node + 96)
            if floats - start > count * 12:
                struct.pack_into("<I", stripped, animation + 184, start + count * 12 + 4)
                break
        with self.assertRaisesRegex(ValueError, "removed controller data"):
            strip_tool.pack(bytes(stripped))


if __name__ == "__main__":
    unittest.main()
