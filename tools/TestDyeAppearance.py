#!/usr/bin/env python3
"""Guard CheckDyeAppearance: silent dye changes and specular maps over dyed metal fail.

Run from tools/: python -B -m unittest TestDyeAppearance -v
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import CheckDyeAppearance as c

MTR = """customshaderVS vslit_sm_nm
renderhint NormalAndSpecMapped
texture1 {name}_n
texture2 {spec}
texture3 null
customshaderFS fs_plt_tinter_nm
texture0 plt_white
texture7 {mask}
texture10 plt_palette
parameter float rowMetal1 0.172119
"""


class DyeAppearanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for folder in ("sw_tint_mtr", "sw_item", "sw_shader", "sw_2da", "tools"):
            (self.root / folder).mkdir()
        for path in c.SHARED_INPUTS:
            (self.root / path).write_bytes(b"shared " + path.encode())
        self.catalog("tintmap", ["0 pmh0_legl121 plate 2,4", "1 pmh0_legl121 legacy 2", "2 **** **** ****"])
        self.catalog("cloaktint", ["0 cloak_001 cloaktint 3,5 0"], "MODEL MATERIAL LAYERS INDEX")
        sources = [{"texture": "tm_metal", "layers": [2, 4]}, {"texture": "tm_cloth", "layers": [4, 6]}]
        (self.root / "tools/TintMapSources.json").write_text(json.dumps(sources))
        self.material("legacy", "legacy_s", "tm_metal")
        self.material("plate", "null", "tm_metal")
        self.material("robe", "robe_s", "tm_cloth")
        for name in ("legacy_s", "legacy_n", "plate_n", "robe_s", "robe_n", "tm_metal", "tm_cloth"):
            (self.root / f"sw_item/{name}.dds").write_bytes(name.encode())
        (self.root / "sw_item/tm_metal.txi").write_text("mipmap 0\n")
        self.git("init", "-q")
        self.git("add", "-A")
        for name, value in (("ROOT", self.root), ("BASELINE", self.root / "tools/DyeAppearanceBaseline.json"),
                            ("SOURCES", self.root / "tools/TintMapSources.json")):
            mock = patch.object(c, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        shared, materials, metal, catalogs = c.current_state()
        self.baseline = {"shared": shared, "materials": materials, "legacyMetalSpecular": sorted(metal),
                         "catalogs": catalogs}

    def git(self, *arguments):
        subprocess.run(["git", "-C", str(self.root), *arguments], check=True, capture_output=True)

    def catalog(self, name, rows, header="MODEL MATERIAL LAYERS"):
        (self.root / f"sw_2da/{name}.2da").write_text("2DA V2.0\n\n   " + header + "\n" + "\n".join(rows) + "\n")

    def material(self, name, spec, mask):
        (self.root / f"sw_tint_mtr/{name}.mtr").write_text(MTR.format(name=name, spec=spec, mask=mask))

    def errors(self):
        return c.check(self.baseline, *c.current_state())

    def test_unchanged_state_passes_with_existing_metal_specular_grandfathered(self):
        self.assertEqual(self.baseline["legacyMetalSpecular"], ["legacy"])
        self.assertEqual(self.errors(), [])

    def test_new_metal_material_with_specular_map_fails(self):
        self.material("trooper", "trooper_s", "tm_metal")
        errors = self.errors()
        self.assertEqual(len(errors), 1)
        self.assertIn("trooper: binds a specular/roughness map over dyed metal", errors[0])

    def test_adding_specular_map_to_existing_metal_material_fails(self):
        self.material("plate", "plate_s", "tm_metal")
        errors = "\n".join(self.errors())
        self.assertIn("plate: dye appearance inputs changed", errors)
        self.assertIn("plate: binds a specular/roughness map over dyed metal", errors)

    def test_specular_map_without_metal_layers_is_allowed_when_new(self):
        self.material("cape", "robe_s", "tm_cloth")
        self.assertEqual(self.errors(), [])

    def test_editing_a_bound_texture_or_mask_fails(self):
        (self.root / "sw_item/legacy_s.dds").write_bytes(b"repainted")
        self.assertEqual(self.errors(), ["legacy: dye appearance inputs changed (MTR directives or a bound texture)"])
        self.material("robe", "robe_s", "tm_metal")
        self.assertIn("robe: dye appearance inputs changed (MTR directives or a bound texture)", self.errors())

    def test_editing_or_deleting_a_tint_mask_under_the_same_name_fails(self):
        changed = ["legacy: dye appearance inputs changed (MTR directives or a bound texture)",
                   "plate: dye appearance inputs changed (MTR directives or a bound texture)"]
        (self.root / "sw_item/tm_metal.dds").write_bytes(b"repacked mask")
        self.assertEqual(self.errors(), changed)
        (self.root / "sw_item/tm_metal.dds").write_bytes(b"tm_metal")
        (self.root / "sw_item/tm_metal.txi").write_text("mipmap 1\n")
        self.assertEqual(self.errors(), changed)
        (self.root / "sw_item/tm_metal.txi").write_text("mipmap 0\n")
        self.assertEqual(self.errors(), [])
        (self.root / "sw_item/tm_metal.dds").unlink()
        self.git("rm", "-q", "--cached", "sw_item/tm_metal.dds")
        self.assertEqual(self.errors(), changed)

    def test_rebinding_an_existing_model_fails_and_new_rows_are_allowed(self):
        self.catalog("tintmap", ["0 pmh0_legl121 plate 2,4", "1 pmh0_legl121 legacy 2", "2 pmh0_legl122 plate 2"])
        self.assertEqual(self.errors(), [])
        self.catalog("tintmap", ["0 pmh0_legl121 plate 2,4,6", "1 pmh0_legl121 legacy 2", "2 **** **** ****"])
        self.assertEqual(self.errors(), ["tintmap.2da pmh0_legl121: material or dye layers changed (approved "
                                         "'legacy 2; plate 2,4', now 'legacy 2; plate 2,4,6')"])
        self.catalog("tintmap", ["0 **** **** ****", "1 **** **** ****", "2 **** **** ****"])
        self.catalog("cloaktint", ["0 cloak_001 cloaktint 3,5 1"], "MODEL MATERIAL LAYERS INDEX")
        errors = "\n".join(self.errors())
        self.assertIn("tintmap.2da pmh0_legl121: material or dye layers changed", errors)
        self.assertIn("now 'removed'", errors)
        self.assertIn("cloaktint.2da cloak_001: material or dye layers changed", errors)

    def test_palette_sampling_change_fails(self):
        (self.root / "sw_item/plt_palette.txi").write_text("mipmap 1\n")
        self.assertEqual(self.errors(), ["sw_item/plt_palette.txi: shared tint input changed; "
                                         "every dyed part may change color"])

    def test_shared_shader_change_and_material_removal_fail(self):
        (self.root / "sw_shader/fs_plt_tinter_nm.shd").write_text("// lighting tweak")
        (self.root / "sw_tint_mtr/robe.mtr").unlink()
        errors = self.errors()
        self.assertIn("sw_shader/fs_plt_tinter_nm.shd: shared tint input changed; every dyed part may change color",
                      errors)
        self.assertIn("robe: approved tint material was removed", errors)

    def test_whitespace_comments_and_line_order_do_not_count_as_changes(self):
        path = self.root / "sw_tint_mtr/plate.mtr"
        path.write_text("// note\r\n" + "\r\n".join(reversed(path.read_text().splitlines())) + "\r\n")
        self.assertEqual(self.errors(), [])

    def test_update_never_grandfathers_new_metal_specular(self):
        (self.root / "tools/DyeAppearanceBaseline.json").write_text(json.dumps(self.baseline))
        self.material("trooper", "trooper_s", "tm_metal")
        with patch("sys.argv", ["CheckDyeAppearance.py", "--update"]):
            self.assertEqual(c.main(), 1)
        self.material("trooper", "null", "tm_metal")
        (self.root / "sw_item/legacy_s.dds").write_bytes(b"approved repaint")
        with patch("sys.argv", ["CheckDyeAppearance.py", "--update"]):
            self.assertEqual(c.main(), 0)
        with patch("sys.argv", ["CheckDyeAppearance.py"]):
            self.assertEqual(c.main(), 0)
        self.assertEqual(json.loads((self.root / "tools/DyeAppearanceBaseline.json").read_text())
                         ["legacyMetalSpecular"], ["legacy"])


class CheckedInDyeAppearanceTests(unittest.TestCase):
    def test_repository_matches_its_approved_dye_baseline(self):
        baseline = json.loads(c.BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(c.check(baseline, *c.current_state()), [])
        # The reported Republic Trooper parts stay off the grandfathered list.
        for name in ("pmh0_legl121", "pmh0_bicepl101", "pmh0_forel101", "pmh0_chest099", "helm_037"):
            self.assertIn(name, baseline["materials"])
            self.assertNotIn(name, baseline["legacyMetalSpecular"])

    def test_hak_build_runs_the_check(self):
        build = (c.ROOT / "BuildHaks.cmd").read_text()
        self.assertIn("python -B tools/CheckDyeAppearance.py\nif errorlevel 1 exit /b %errorlevel%",
                      build.replace("\r\n", "\n"))


if __name__ == "__main__":
    unittest.main()
