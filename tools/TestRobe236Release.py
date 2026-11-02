"""Robe 236 keeps the models released with the Combat Upgrade, on native and RGB bodies."""
import json
import unittest

import BindRobePanels as bound
import CompileModels as mdl
import MakeRobePanelsRigid as rigid
import RestoreRobe236Release as release
import RobeAnimations as anim
import RobePoseAudit as poses

COVERINGS = ("coat_top", "coat_top2", "hood002", "beltjr")
DANGLY, SKIN = 0x100, 0x40


def coverings(data):
    model = poses.Model(data, False)
    return {name.removeprefix("rm_").removeprefix("rg_"): model.uint(offset + 108)
            for name, _, _, _, offset in model.nodes
            if name.removeprefix("rm_").removeprefix("rg_") in COVERINGS}


class Robe236ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = release.expected_models()
        cls.manifest = json.loads(rigid.MANIFEST.read_text())

    def test_native_models_are_the_restored_release_models(self):
        self.assertEqual(16, len(self.expected))
        for path, data in self.expected.items():
            with self.subTest(model=path.name):
                self.assertEqual(data, path.read_bytes())

    def test_only_part_ids_and_the_repaired_cuff_differ_from_the_release(self):
        for path, data in self.expected.items():
            if path.stem == release.SPLICED_MODEL:
                continue
            original = release.git_blob(release.RELEASE_BLOBS[path.stem])
            with self.subTest(model=path.name):
                self.assertEqual(len(original), len(data))
                released = {name: props for name, _, props in mdl.binary_nodes(original) if props}
                restored = {name: props for name, _, props in mdl.binary_nodes(data) if props}
                if path.stem == release.CUFF_MODEL:
                    released[release.CUFF_MESH].pop("weights")
                    restored[release.CUFF_MESH].pop("weights")
                self.assertEqual(released, restored)
                self.assertEqual(coverings(original), coverings(data))

    def test_female_ewok_robe_has_released_shape_and_aligned_clips(self):
        path = bound.ROOT / "sw_pt_robe" / f"{release.SPLICED_MODEL}.mdl"
        data = self.expected[path]
        original = release.git_blob(release.RELEASE_BLOBS[release.SPLICED_MODEL])
        aligned = release.git_blob(release.ALIGNED_CLIPS_BLOB)
        # Recompiling welds duplicate vertices differently, so compare each mesh's surface.
        def surfaces(model):
            return {name: (sorted({tuple(round(value, 5) for value in vertex) for vertex in props["verts"]}),
                           sorted({tuple(round(value, 5) for value in uv) for uv in props["tverts"]}))
                    for name, _, props in mdl.binary_nodes(model) if props}
        self.assertEqual(surfaces(original), surfaces(data))
        self.assertEqual(coverings(original), coverings(data))
        # Every node, in geometry and in both clips, carries the aligned model's part ID.
        def clip_parts(model):
            header = lambda offset: int.from_bytes(model[offset:offset + 4], "little")
            roots = [header(12 + header(12 + header(132) + 4 * index) + 72) for index in range(header(136))]
            return [anim.binary_parts(model, root) for root in roots]
        self.assertEqual(anim.binary_parts(aligned), anim.binary_parts(data))
        self.assertEqual(clip_parts(aligned), clip_parts(data))

    def test_native_part_ids_follow_the_current_coat_animation_parents(self):
        for path, data in self.expected.items():
            if "22_" in path.name:
                continue
            parent = mdl.supermodel(data)
            expected = anim.binary_parts((bound.ROOT / "sw_cr_creature" / f"{parent}.mdl").read_bytes())
            for node, part in anim.binary_parts(data).items():
                if part >= 0:
                    self.assertEqual(expected[node], part, f"{path.name}/{'/'.join(node)}")

    def test_rgb_roots_are_generated_from_the_released_coverings(self):
        checked = 0
        for root, names in self.manifest["garment_node_names"].items():
            source = next((name for name in names if name.endswith("_robe236")), None)
            if source is None:
                continue
            native = coverings((bound.ROOT / "sw_pt_robe" / f"{source}.mdl").read_bytes())
            generated = coverings((bound.ROOT / "sw_pt_root" / f"{root}.mdl").read_bytes())
            self.assertEqual(native.keys(), generated.keys(), root)
            for name, flags in native.items():
                self.assertEqual(flags & (DANGLY | SKIN), generated[name] & (DANGLY | SKIN), f"{root}/{name}")
            checked += 1
        self.assertEqual(8, checked, "One generated root per normal-body family")

    def test_catalog_panel_repairs_leave_robe_236_alone(self):
        restored = set(rigid.restored_targets(self.manifest))
        # Native phenotype 0, 22 and empty 200 attachments, plus one RGB root, per body family.
        self.assertEqual(32, len(restored))
        self.assertEqual(set(), restored & set(bound.catalog_targets(self.manifest)))
        with self.assertRaises(ValueError):
            rigid.targets(236, self.manifest)


if __name__ == "__main__":
    unittest.main()
