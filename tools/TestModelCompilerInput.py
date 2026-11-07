"""Guard explicit repairs of malformed legacy model input."""
import unittest
import struct

from CompileModels import repair_legacy_input, validate_authored_animation_order


class ModelCompilerInputTests(unittest.TestCase):
    def animation_bank(self, names):
        source = b"# SWLOR authored animations for a_ba\n" + b"".join(
            b"newanim " + name + b" bank\ndoneanim " + name + b" bank\n" for name in names)
        compiled = bytearray(256 + len(names) * 80)
        struct.pack_into("<II", compiled, 132, 232, len(names))
        for index, name in enumerate(names):
            offset = 256 + index * 80
            struct.pack_into("<I", compiled, 244 + index * 4, offset - 12)
            compiled[offset + 8:offset + 8 + len(name)] = name
        return source, bytes(compiled)

    def test_authored_bank_uses_lowercase_ascii_order_including_underscores(self):
        source, compiled = self.animation_bank([b"Sw_Aimedshot", b"sw_crip_defe", b"SW_criplshot"])
        validate_authored_animation_order(source, compiled)

    def test_authored_bank_rejects_appended_or_duplicate_names(self):
        for names in ([b"zebra", b"apple"], [b"apple", b"Apple"]):
            with self.assertRaisesRegex(ValueError, "unique and sorted"):
                validate_authored_animation_order(*self.animation_bank(names))

    def test_authored_bank_checks_the_compiled_table_too(self):
        source, _ = self.animation_bank([b"apple", b"zebra"])
        _, compiled = self.animation_bank([b"zebra", b"apple"])
        with self.assertRaisesRegex(ValueError, "Compiled animation table"):
            validate_authored_animation_order(source, compiled)

    def test_legacy_models_do_not_acquire_authored_bank_requirements(self):
        validate_authored_animation_order(b"newmodel legacy\n", b"")

    def source(self, value="", kind="danglymesh"):
        return (f"newmodel sample\nnode {kind} cloth\n  period {value}\n"
                "  displacement 0.02\n  tightness 3\nendnode\n").encode()

    def test_blank_period_has_no_inferred_default(self):
        data = self.source()
        repaired, changes = repair_legacy_input(data)
        self.assertNotIn(b"period 10", repaired)
        self.assertEqual(changes, [])

    def test_explicit_period_repair_changes_only_the_requested_empty_field(self):
        baseline, _ = repair_legacy_input(self.source())
        repaired, changes = repair_legacy_input(self.source(), {("sample", "cloth"): "10"})
        self.assertEqual(repaired, baseline.replace(b"  period \n", b"  period 10\n"))
        self.assertEqual(changes, ["cloth: filled malformed empty dangly period with explicit value 10"])

    def test_explicit_repair_cannot_overwrite_an_authored_period(self):
        with self.assertRaisesRegex(ValueError, "exactly one empty field"):
            repair_legacy_input(self.source("6"), {("sample", "cloth"): "10"})

    def test_explicit_repair_cannot_target_another_model_or_node(self):
        for key in (("other", "cloth"), ("sample", "other")):
            repaired, changes = repair_legacy_input(self.source(), {key: "10"})
            self.assertNotIn(b"period 10", repaired)
            self.assertEqual(changes, [])

    def test_explicit_repair_cannot_add_dangly_behavior_to_other_meshes(self):
        with self.assertRaisesRegex(ValueError, "requires a dangly mesh"):
            repair_legacy_input(self.source(kind="trimesh"), {("sample", "cloth"): "10"})


if __name__ == "__main__":
    unittest.main()
