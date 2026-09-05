"""Guard explicit repairs of malformed legacy model input."""
import unittest

from CompileModels import repair_legacy_input


class ModelCompilerInputTests(unittest.TestCase):
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
