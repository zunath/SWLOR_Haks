"""Reject garment-motion regressions using real compiled before/after rigs."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import weakref

import CompileModels as mdl
import RobePoseAudit as poses
import RobeSharingAudit as audit


def node(name, parent, values=""):
    return f"node dummy {name}\n parent {parent}\n{values}\nendnode\n"


class RobeSharingAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.stage = Path(cls.temporary.name)
        cls.compiler, _ = mdl.prepare_compiler(cls.stage)
        (cls.stage / "binary").mkdir()
        cls.names = {"rg_model": "rg_model_2", "rg_tail": "rg_tail_2", "rm_cloth": "rm_cloth"}
        cls.before = cls.compile_fixture("oldwearer", False)
        cls.after = cls.compile_fixture("newwearer", True)
        cls.wrong_parent = cls.compile_fixture("badparent", True, "rootdummy")

    @classmethod
    def compile_fixture(cls, name, shared, tail_parent=None):
        garment = "rg_model_2" if shared else "rg_model"
        tail = "rg_tail_2" if shared else "rg_tail"
        geometry = node(name, "NULL") + node("rootdummy", name, " position 0 0 1")
        geometry += node("impact_a", "rootdummy") + node("impact_b", "rootdummy")
        geometry += node(garment, name, " position 0 0 0")
        geometry += node(tail, tail_parent or garment, " position 0 -0.2 0.5\n orientation 0 0 1 0.125")
        geometry += (f"node trimesh rm_cloth\n parent {tail}\n position 0 0 0.1\n"
                     " selfillumcolor 0.2 0.3 0.4\n alpha 0.9\n bitmap NULL\n"
                     " verts 3\n 0 0 0\n 1 0 0\n 0 1 0\n"
                     " tverts 3\n 0 0 0\n 1 0 0\n 0 1 0\n"
                     " faces 1\n 0 1 2 1 0 1 2 0\nendnode\n")
        if shared:
            geometry += node("rg_other_variant", name, " position 0 0 2")
        clips = ""
        for index, clip in enumerate(("walk", "wave")):
            duration = 1 + index
            tracks = node(name, "NULL") + node("rootdummy", name)
            tracks += node(garment, name)
            tracks += node(tail, tail_parent or garment,
                           f" positionkey 2\n 0 0 -0.2 0.5\n {duration} 0.1 -0.3 0.6\n"
                           f" orientationkey 2\n 0 0 0 1 0.125\n {duration} 1 0 0 0.8")
            clips += (f"newanim {clip} {name}\nlength {duration}\ntranstime 0.2\n"
                      f"animroot {name if index else 'rootdummy'}\nevent 0.25 cast\n"
                      f"{tracks}doneanim {clip} {name}\n")
        source = (f"newmodel {name}\nsetsupermodel {name} NULL\nclassification CHARACTER\n"
                  f"setanimationscale 1\nbeginmodelgeom {name}\n{geometry}endmodelgeom {name}\n"
                  f"{clips}donemodel {name}\n")
        path = cls.stage / f"{name}.mdl"
        path.write_text(source)
        mdl.run_compiler(cls.compiler, cls.stage,
                         ["-cne", str(path), str(cls.stage / "binary") + "/"], f"{name}.log")
        result = bytearray((cls.stage / "binary" / path.name).read_bytes())
        # Stock body subtrees may legitimately repeat helper names. This does
        # not make the distinctly named garment nodes ambiguous.
        for item in poses.Model(result, False).nodes:
            if item[0] == "impact_b":
                result[item[4] + 32:item[4] + 64] = b"impact_a".ljust(32, b"\0")
        return bytes(result)

    @staticmethod
    def controller_location(data, name, kind, clip=None):
        model = poses.Model(data)
        nodes = model.nodes if clip is None else model.clips[clip][1]
        item = next(node for node in nodes if node[0] == name)
        keys = struct.unpack_from("<I", data, item[4] + 84)[0] + 12
        values = struct.unpack_from("<I", data, item[4] + 96)[0] + 12
        for index in range(struct.unpack_from("<I", data, item[4] + 88)[0]):
            offset = keys + index * 12
            controller, rows, times, start, columns = struct.unpack_from("<IHHHB", data, offset)
            if controller == kind:
                return offset, values + times * 4, values + start * 4, rows, columns
        raise AssertionError("Fixture controller missing")

    def test_aliases_preserve_garments_and_ignore_duplicate_body_helpers(self):
        # Real wearable meshes also carry non-transform material controllers.
        self.controller_location(self.before, "rm_cloth", 100)
        self.controller_location(self.before, "rm_cloth", 128)
        audit.validate_wearer(self.before, self.after, self.names)
        self.assertEqual(2, audit.validate_joints(poses.Model(self.before), poses.Model(self.after), self.names))
        self.assertEqual(audit.clip_headers(self.before), audit.clip_headers(self.after))
        # Explicit model-root aliases also exercise the native None parent.
        audit.validate_wearer(self.before, self.after, {**self.names, "oldwearer": "newwearer"})

    def test_bad_alias_maps_fail_without_collapsing_distinct_wearer_nodes(self):
        for names in ({**self.names, "rg_tail": "missing"},
                      {**self.names, "rg_model": "rg_tail_2"},
                      {key: value for key, value in self.names.items() if key != "rg_tail"}):
            with self.subTest(names=names), self.assertRaises(ValueError):
                audit.validate_wearer(self.before, self.after, names)
        with self.assertRaisesRegex(ValueError, "missing a mapped"):
            audit.validate_joints(poses.Model(self.before), poses.Model(self.after), {"rg_tail": "missing"})

    def test_changed_parent_or_local_bind_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "hierarchy or local bind"):
            audit.validate_wearer(self.before, self.wrong_parent, self.names)
        with self.assertRaisesRegex(ValueError, "hierarchy changed"):
            audit.validate_joints(poses.Model(self.before), poses.Model(self.wrong_parent), self.names)
        modified = bytearray(self.after)
        _, _, value, _, _ = self.controller_location(modified, "rg_tail_2", 8)
        struct.pack_into("<f", modified, value, 5)
        with self.assertRaisesRegex(ValueError, "local bind changed"):
            audit.validate_wearer(self.before, modified, self.names)

    def test_changed_animation_key_times_values_and_channels_are_rejected(self):
        descriptor, times, values, _, _ = self.controller_location(self.after, "rg_tail_2", 8, "walk")
        for label, offset, fmt, value in (("time", times + 4, "<f", 0.5),
                                          ("value", values + 12, "<f", 4),
                                          ("unknown channel", descriptor, "<I", 999)):
            with self.subTest(label=label):
                modified = bytearray(self.after)
                struct.pack_into(fmt, modified, offset, value)
                with self.assertRaisesRegex(ValueError, "controllers changed|unsupported"):
                    audit.validate_joints(poses.Model(self.before), poses.Model(modified), self.names)

    def test_header_comparison_ignores_padding_but_keeps_real_events(self):
        modified = bytearray(self.after)
        array = 12 + struct.unpack_from("<I", modified, 132)[0]
        animation = 12 + struct.unpack_from("<I", modified, array)[0]
        event = 12 + struct.unpack_from("<I", modified, animation + 184)[0]
        modified[event + 9:event + 36] = bytes([255]) * 27
        self.assertEqual(audit.clip_headers(self.after), audit.clip_headers(modified))
        audit.validate_joints(poses.Model(self.before), poses.Model(modified), self.names)
        modified[event + 4:event + 9] = b"oops\0"
        with self.assertRaisesRegex(ValueError, "timing, roots, or events"):
            audit.validate_joints(poses.Model(self.before), poses.Model(modified), self.names)
        modified = bytearray(self.after)
        struct.pack_into("<f", modified, animation + 116, 0.8)
        with self.assertRaisesRegex(ValueError, "timing, roots, or events"):
            audit.validate_joints(poses.Model(self.before), poses.Model(modified), self.names)

    def test_controller_shape_changes_are_not_hidden_by_zip(self):
        self.assertFalse(audit.controllers_equal({20: ((0,), [(0, 0, 0, 1)])},
                                                {20: ((0,), [(0, 0, 0)])}))
        self.assertFalse(audit.controllers_equal({8: ((0,), [(0, 0, 0)])}, {}))
        self.assertFalse(audit.controllers_equal({8: ((0,), [(0, 0, 0)])},
                                                {8: ((0, 1), [(0, 0, 0), (0, 0, 0)])}))

    def test_record_grouping_loads_each_family_once_and_releases_previous_models(self):
        records = [{"after": "shared_a", "before": "old_a", "names": self.names},
                   {"after": "shared_a", "before": "old_a", "names": dict(self.names)},
                   {"after": "shared_a", "before": "old_b", "names": self.names},
                   {"after": "shared_b", "before": "old_a", "names": self.names}]
        loaded_before, loaded_after, instances, peak = [], [], [], []
        native_model = poses.Model

        def model(data):
            result = native_model(data)
            instances.append(weakref.ref(result))
            peak.append(sum(item() is not None for item in instances))
            return result

        def before(name):
            loaded_before.append(name)
            return self.before

        def after(name):
            loaded_after.append(name)
            return self.after

        with patch.object(audit.poses, "Model", side_effect=model):
            self.assertEqual(6, audit.validate_records(records, before, after))
        self.assertEqual(["shared_a", "shared_b"], loaded_after)
        self.assertEqual(["old_a", "old_b", "old_a"], loaded_before)
        self.assertLessEqual(max(peak), 2)
        self.assertFalse(any(instance() is not None for instance in instances))


if __name__ == "__main__":
    unittest.main()
