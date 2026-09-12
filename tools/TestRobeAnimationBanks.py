"""Exercise lossless bank relocation against the native model compiler/reader."""
from pathlib import Path
from dataclasses import replace
import struct
import tempfile
import unittest

import CompileModels as mdl
import RobeAnimationBanks as banks
import RobeAnimations as animations
import RobePoseAudit as poses


def node(name, parent, values=""):
    return f"node dummy {name}\n parent {parent}\n{values}\nendnode\n"


def uint(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def event_payloads(data):
    result = {}
    for index in range(uint(data, 136)):
        animation = 12 + uint(data, 12 + uint(data, 132) + 4 * index)
        name = data[animation + 8:animation + 72].split(b"\0", 1)[0].decode("ascii")
        events = 12 + uint(data, animation + 184)
        result[name] = (data[animation + 112:animation + 120],
                        data[events:events + 36 * uint(data, animation + 188)])
    return result


class NativeRobeAnimationBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.stage = Path(cls.temporary.name)
        cls.compiler, _ = mdl.prepare_compiler(cls.stage)
        binary = cls.stage / "binary"
        binary.mkdir()

        def compile_model(name, parent, geometry, clips=""):
            source = (f"newmodel {name}\nsetsupermodel {name} {parent}\n"
                      "classification CHARACTER\nsetanimationscale 1\n"
                      f"beginmodelgeom {name}\n{geometry}endmodelgeom {name}\n"
                      f"{clips}donemodel {name}\n")
            path = cls.stage / f"{name}.mdl"
            path.write_text(source)
            mdl.run_compiler(cls.compiler, cls.stage,
                             ["-cne", str(path), str(binary) + "/"], f"{name}.compile.log")
            data = (binary / path.name).read_bytes()
            path.write_bytes(data)
            return data

        # An inherited part-ID space larger than this bridge's own node count
        # catches accidental renumbering when moving animations into parents.
        geometry = node("basebody", "NULL")
        geometry += "".join(node(f"unused{i}", "basebody") for i in range(8))
        geometry += node("rootdummy", "basebody", " position 0 0 1")
        geometry += node("arm", "rootdummy", " position 0.2 0 0.5")
        geometry += node("hand", "arm", " position 0 0.4 0")
        compile_model("basebody", "NULL", geometry)

        name = "pmh_ra001"
        geometry = node(name, "NULL")
        geometry += node("rootdummy", name, " position 0 0 1")
        geometry += node("arm", "rootdummy", " position 0.2 0 0.5")
        geometry += node("hand", "arm", " position 0 0.4 0")
        geometry += node("rg_tail", "rootdummy", " position 0 -0.2 0")
        clips = ""
        for index in range(5):
            duration = 1 + index * 0.25
            root = name if index == 0 else "rootdummy"
            tracks = node(name, "NULL") + node("rootdummy", name)
            tracks += node("arm", "rootdummy", " orientationkey 3\n"
                           f" 0 0 0 1 0.0001\n {duration / 2} 0 1 0 0.7\n {duration} 1 0 0 0.2")
            tracks += node("hand", "arm", f" positionkey 2\n 0 0 0.4 0\n {duration} 0.1 0.5 0.2")
            tracks += node("rg_tail", "rootdummy", f" scalekey 2\n 0 1\n {duration} 0.95")
            clips += (f"newanim clip{index} {name}\nlength {duration}\ntranstime 0.125\n"
                      f"animroot {root}\nevent 0.25 cast\nevent {duration} snd_footstep\n"
                      f"{tracks}doneanim clip{index} {name}\n")
        cls.original = compile_model(name, "basebody", geometry, clips)
        cls.model = banks.CompiledBridge(cls.original)
        cls.target = 244 + len(cls.model.geometry.data) + max(len(clip.data) + 4 for clip in cls.model.clips) + 1
        cls.parts = banks.split(cls.original, cls.target)

    def test_native_compiler_output_splits_and_rejoins_byte_for_byte(self):
        self.assertEqual(5, len(self.parts))
        self.assertEqual(["pmh_ra001", *[f"pmh_ra001_b{i:02d}" for i in range(1, 5)]], list(self.parts))
        self.assertTrue(all(len(data) < self.target for data in self.parts.values()))
        self.assertEqual(self.original, banks.join(list(self.parts.values())))
        banks.validate_split(self.original, list(self.parts.values()))
        self.assertEqual({"pmh_ra001": self.original}, banks.split(self.original))

    def test_named_motion_revision_preserves_every_unselected_native_byte(self):
        clips = list(self.model.clips)
        data = bytearray(clips[2].data)
        struct.pack_into("<f", data, len(data) - 4, .8)
        clips[2] = replace(clips[2], data=bytes(data))
        changed = self.model.pack(self.model.name, self.model.parent, clips)
        banks.validate_motion_revision(self.original, changed, ["clip2"])
        self.assertEqual(changed, banks.revise_motion(self.original, changed, ["clip2"]))
        # The compiler leaves garbage after the event-name terminator. Keep
        # the original padding; meaningful event names and times still matter.
        data = bytearray(clips[1].data)
        event = uint(data, 184) - clips[1].start
        data[event + 4 + len("cast") + 1] ^= 0xFF
        clips[1] = replace(clips[1], data=bytes(data))
        noisy = self.model.pack(self.model.name, self.model.parent, clips)
        self.assertEqual(changed, banks.revise_motion(self.original, noisy, ["clip2"]))
        with self.assertRaisesRegex(ValueError, "unselected animation"):
            banks.revise_motion(self.original, changed, ["clip1"])
        for offset in (event, event + 4):
            data = bytearray(clips[1].data)
            data[offset] ^= 1
            modified = list(clips)
            modified[1] = replace(clips[1], data=bytes(data))
            with self.assertRaisesRegex(ValueError, "unselected animation"):
                banks.revise_motion(self.original,
                    self.model.pack(self.model.name, self.model.parent, modified), ["clip2"])
        for allowed in ([], ["missing"], ["clip1"]):
            with self.subTest(allowed=allowed), self.assertRaises(ValueError):
                banks.validate_motion_revision(self.original, changed, allowed)
        for changed in (self.model.pack(self.model.name, "different", clips),
                        self.model.pack(self.model.name, self.model.parent, clips[:-1])):
            with self.assertRaisesRegex(ValueError, "identity, parent or clip inventory"):
                banks.validate_motion_revision(self.original, changed, ["clip2"])
        model = banks.CompiledBridge(self.original)
        data = bytearray(model.geometry.data)
        struct.pack_into("<f", data, len(data) - 4, .8)
        model.geometry = replace(model.geometry, data=bytes(data))
        with self.assertRaisesRegex(ValueError, "skeleton or an unselected animation"):
            banks.validate_motion_revision(self.original, model.pack(model.name, model.parent, clips), ["clip2"])

    def test_skeleton_only_model_cannot_exceed_the_bank_target(self):
        empty = self.model.pack(self.model.name, self.model.parent, [])
        with self.assertRaisesRegex(ValueError, "skeleton alone exceeds"):
            banks.split(empty, len(empty))

    def test_each_bank_is_readable_by_the_native_decompiler(self):
        output = self.stage / "decompiled"
        output.mkdir(exist_ok=True)
        for name, data in self.parts.items():
            (self.stage / f"{name}.mdl").write_bytes(data)
        for name in self.parts:
            with self.subTest(name=name):
                mdl.run_compiler(self.compiler, self.stage,
                                 ["-d", str(self.stage / f"{name}.mdl"), str(output) + "/"], f"{name}.decompile.log")
                text = (output / f"{name}.mdl.ascii").read_text()
                self.assertEqual(name, animations.model_name(text))
                self.assertEqual(banks.CompiledBridge(self.parts[name]).clip_names,
                                 [match[1] for match in animations.ANIMATION.finditer(text)])
                self.assertIn("event", text)
                self.assertIn("orientationkey", text)

    def test_native_part_ids_and_sampled_poses_are_unchanged(self):
        original = poses.Model(self.original)
        expected_parts = animations.binary_parts(self.original)
        expected_events = event_payloads(self.original)
        self.assertGreater(uint(self.original, 88), len(expected_parts))
        found = {}
        for data in self.parts.values():
            animations.validate_animation_parts(data)
            self.assertEqual(expected_parts, animations.binary_parts(data))
            self.assertEqual(uint(self.original, 88), uint(data, 88))
            model = poses.Model(data)
            for name, payload in event_payloads(data).items():
                self.assertEqual(expected_events[name], payload)
            for name, clip in model.clips.items():
                self.assertNotIn(name, found)
                found[name] = clip
                before = original.clips[name]
                self.assertEqual(before[0], clip[0])
                # Ignore only each bank's renamed root and relocated file offsets.
                self.assertEqual([(part, controllers) for _, _, part, controllers, _ in before[1]],
                                 [(part, controllers) for _, _, part, controllers, _ in clip[1]])
                for fraction in (0, 0.25, 0.5, 0.75, 1):
                    self.assertEqual(original.pose(before, fraction * before[0]),
                                     original.pose(clip, fraction * clip[0]))
        self.assertEqual(set(original.clips), set(found))

    def test_invalid_native_layouts_are_rejected(self):
        modifications = [
            ("raw data", 8, 4),
            ("nonunit scale", 176, struct.unpack("<I", struct.pack("<f", 0.9))[0]),
            ("bad animation offset", 244, len(self.original) + 100),
            ("runtime parent", 12 + uint(self.original, 84) + 68, 1),
            ("mesh node", 12 + uint(self.original, 84) + 108, 33),
            ("out of range node array", 12 + uint(self.original, 84) + 72, 0xffffffff),
        ]
        for label, offset, value in modifications:
            with self.subTest(label=label):
                damaged = bytearray(self.original)
                struct.pack_into("<I", damaged, offset, value)
                with self.assertRaises(ValueError):
                    banks.CompiledBridge(damaged)
        for data in (b"newmodel pmh_ra001", self.original[:243], self.original[:-1], self.original + b"\0"):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                banks.CompiledBridge(data)

    def test_oversized_individual_clips_are_not_truncated(self):
        with self.assertRaisesRegex(ValueError, "one animation exceeds"):
            banks.split(self.original, self.target - 1)
        for target in (0, -1, banks.LIMIT_BYTES):
            with self.subTest(target=target), self.assertRaises(ValueError):
                banks.split(self.original, target)

    def test_broken_or_incomplete_bank_chains_cannot_validate(self):
        values = list(self.parts.values())
        invalid = [[], values[1:], values[::-1], [values[0], values[2], values[1], *values[3:]],
                   [values[0], values[1], values[1], *values[3:]], values[:-1]]
        for parts in invalid:
            with self.subTest(count=len(parts)), self.assertRaises(ValueError):
                banks.validate_split(self.original, parts)
        changed = bytearray(values[1])
        changed[180:244] = b"unrelated".ljust(64, b"\0")
        with self.assertRaisesRegex(ValueError, "Broken or reordered"):
            banks.join([values[0], changed, *values[2:]])

    def test_bank_metadata_and_animation_changes_cannot_validate(self):
        values = list(self.parts.values())
        changed = bytearray(values[1])
        struct.pack_into("<I", changed, 88, uint(changed, 88) + 1)
        with self.assertRaisesRegex(ValueError, "different skeletons or model metadata"):
            banks.join([values[0], changed, *values[2:]])
        changed = bytearray(values[1])
        animation = 12 + uint(changed, 12 + uint(changed, 132))
        struct.pack_into("<f", changed, animation + 112, 9)
        with self.assertRaisesRegex(ValueError, "changed original"):
            banks.validate_split(self.original, [values[0], changed, *values[2:]])


if __name__ == "__main__":
    unittest.main()
