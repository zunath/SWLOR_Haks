"""Regression coverage for externally skinned armor parts on robe skeletons."""
import json
import math
from pathlib import Path
import re
import shutil
import struct
import tempfile
import unittest

import CompileModels as mdl
import GeneratePartSkinModels as parts
import GenerateTintMapAssets as tint
from NeckSkinBindings import body_model, mapping_for, needs_remap
from RobePoseAudit import Model

skins = parts.part_skins


class PartSkinModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = parts.ROOT
        cls.manifest = json.loads((cls.root / parts.MANIFEST).read_text())
        cls.active = parts.active_models(cls.root)
        cls.layouts = {body: int(layout) for body, layout in parts.read_table(cls.root, parts.LAYOUTS)}
        cls.renders = {}
        for layout, source, render in parts.read_table(cls.root, parts.RENDERS):
            cls.renders.setdefault(int(layout), {})[source] = render

    def setUp(self):
        # Per-test caches: retaining every robe body, pose and scanned part across
        # the whole module exhausts memory-limited workers.
        self.donors = {}
        self.sources = {}

    def tearDown(self):
        # unittest keeps finished test instances until the module's suite ends.
        self.donors.clear()
        self.sources.clear()

    def load_body(self, name):
        return body_model(self.active[name].read_bytes())

    def donor(self, name):
        """Base bodies are few; robe bodies are loaded one at a time and dropped."""
        if name not in self.donors:
            body = self.load_body(name)
            self.donors[name] = (body, body.pose())
        return self.donors[name]

    def source(self, name):
        """Keep bytes and skins of external skins only; other parts need no second look."""
        if name not in self.sources:
            data = self.active[name].read_bytes()
            external = skins(data) if mdl.binary(data) else []
            self.sources[name] = (data, external) if external else (None, [])
        return self.sources[name]

    def render(self, body, source):
        return self.renders.get(self.layouts.get(body), {}).get(source)

    def test_reported_pelvis_chest_and_hand_skins_follow_robes_20_21_and_236(self):
        for gender in 'fm':
            for phenotype in (47, 216, 200):
                body = f'p{gender}h{phenotype}'
                for source in (f'p{gender}h0_pelvis010', f'p{gender}h0_chest003',
                               f'p{gender}h0_handl003', f'p{gender}h0_handr003'):
                    with self.subTest(body=body, source=source):
                        render = self.render(body, source)
                        self.assertIsNotNone(render)
                        self.assertEqual(self.manifest['models'][render]['source'], source)
        # The base body and non-robe phenotypes keep authored parts.
        self.assertNotIn('pfh0', self.layouts)
        self.assertNotIn('pmh2', self.layouts)

    def test_every_external_skin_has_an_alias_on_every_robe_body_that_needs_one(self):
        defaults = {}
        for line in (self.root / 'sw_2da/phenotype.2da').read_text().splitlines()[3:]:
            fields = line.split()
            if len(fields) >= 4 and fields[0].isdigit() and fields[3].isdigit():
                defaults[int(fields[0])] = int(fields[3])
        robes = {f'{model[:3]}{phenotype}' for model, phenotype, _ in parts.read_table(self.root, 'sw_2da/roberender.2da')}
        variants = {}
        for name in self.active:
            match = parts.PART.fullmatch(name)
            if match and not parts.OWNED_ELSEWHERE.fullmatch(name):
                variants.setdefault((match[1], match[3], int(match[4])), {})[int(match[2])] = name
        expected = set()
        kinds = set()
        for robe in sorted(robes):
            target = None
            for (family, kind, _), by_phenotype in variants.items():
                if family != robe[:3]:
                    continue
                phenotype = int(robe[3:])
                while phenotype not in by_phenotype and phenotype != 0:
                    phenotype = defaults.get(phenotype, 0)
                source = by_phenotype.get(phenotype)
                if source is None:
                    continue
                data, external = self.source(source)
                if not external:
                    continue
                target = target or self.load_body(robe)
                if needs_remap(mapping_for(data, self.donor(source.split('_')[0])[0], target, external)):
                    expected.add((robe, source))
                    kinds.add(kind)
        actual = {(body, source) for body, layout in self.layouts.items() for source in self.renders[layout]}
        self.assertEqual(expected, actual)
        self.assertEqual({'belt', 'chest', 'pelvis', 'handl', 'handr', 'shinl', 'shinr', 'shol', 'shor'}, kinds)
        self.assertEqual(set(self.manifest['models']), {r for mapping in self.renders.values() for r in mapping.values()})
        self.assertFalse(any(parts.OWNED_ELSEWHERE.fullmatch(source) for _, source in actual))

    def test_all_outputs_preserve_raw_meshes_materials_controllers_and_inverse_binds(self):
        for name, record in self.manifest['models'].items():
            with self.subTest(model=name):
                original, old_skins = self.source(record['source'])
                old = Model(original, False)
                data = (self.root / record['path']).read_bytes()
                new = Model(data, False)
                self.assertEqual(original[12 + old.uint(4):], data[12 + new.uint(4):])
                self.assertEqual(len(old.nodes), len(new.nodes))
                expected = bytearray(original[:12 + old.uint(4)])
                actual = bytearray(data[:len(expected)])
                for start, size in [(4, 4), (20, 64), (old.nodes[0][4] + 32, 32)]:
                    actual[start:start + size] = expected[start:start + size]
                self.assertEqual(len(old_skins), len(skins(data)))
                self.assertEqual(name, data[20:84].split(b'\0', 1)[0].decode())
                for (skin, slots), mapped in zip(old_skins, record['mapping']):
                    for start, size in [(skin + 20, 36), (skin + 64, 34)]:
                        actual[start:start + size] = expected[start:start + size]
                    for slot, before, after in mapped:
                        self.assertIn((slot, before), slots)
                        self.assertEqual(struct.unpack_from('<h', data, skin + 64 + slot * 2)[0], after)
                        for pointer, size in [(28, 16), (40, 12)]:
                            a = 12 + old.uint(skin + pointer) + before * size
                            b = 12 + new.uint(skin + pointer) + after * size
                            self.assertEqual(original[a:a + size], data[b:b + size])
                self.assertEqual(expected, actual)

    def test_bindings_follow_the_same_body_joints_instead_of_shifted_traversal_indices(self):
        extents = {}
        for body_name, layout in sorted(self.layouts.items()):
            body = self.load_body(body_name)
            target_pose = body.pose()
            for source, render in self.renders[layout].items():
                with self.subTest(body=body_name, model=render):
                    record = self.manifest['models'][render]
                    original, external = self.source(source)
                    donor, donor_pose = self.donor(source.split('_')[0])
                    actual = mapping_for(original, donor, body, external)
                    self.assertEqual(json.loads(json.dumps(actual)), record['mapping'])
                    self.assertTrue(any(before != after for mesh in actual for _, before, after in mesh))
                    if render not in extents:
                        output = Model((self.root / record['path']).read_bytes(), False)
                        extents[render] = min(output.uint(skin + 24) for skin, _ in external)
                    self.assertGreaterEqual(extents[render], len(body.nodes))
                    for mesh in actual:
                        for _, before, after in mesh:
                            if before == 0:
                                self.assertEqual(after, 0)  # Root identity changes with the body resref.
                                continue
                            joint = donor.nodes[before][0]
                            self.assertEqual(joint, body.nodes[after][0])
                            self.assertFalse(re.match(r'(rg|rp|cl)', joint), joint)
                            self.assertLess(math.dist(donor_pose[joint][0], target_pose[joint][0]), 2e-5)

    def test_robe_bodies_of_one_family_share_aliases(self):
        # Spacers give every robe body of a base body one layout; only bodies with
        # their own phenotype-specific parts (pmh200 shins) need another.
        families = {}
        for body, layout in self.layouts.items():
            families.setdefault(body[:3], set()).add(layout)
        for family, layouts in families.items():
            self.assertLessEqual(len(layouts), 2 if family == 'pmh' else 1, family)
        self.assertLess(sum(map(len, self.renders.values())), 1000)

    def test_packaging_hashes_and_tint_source_scopes_are_current(self):
        parts.run(self.root)
        self.assertTrue(set(self.manifest['models']).isdisjoint(tint.find_tint_source_models()))


class CheckPartSkinModelsTests(unittest.TestCase):
    """The packaging check must follow HAK priority and every model input."""

    def fixture(self, root):
        source = parts.ROOT
        for directory in ('sw_2da', 'sw_pt_root', 'sw_pt_pelvis', 'override', 'tools'):
            (root / directory).mkdir()
        config = {'HakList': [{'Path': d} for d in ('sw_2da', 'sw_pt_root', 'sw_pt_pelvis', 'override')]}
        (root / 'hakbuilder.json').write_text(json.dumps(config))
        shutil.copyfile(source / 'sw_2da/phenotype.2da', root / 'sw_2da/phenotype.2da')
        (root / 'sw_2da/roberender.2da').write_text(
            '2DA V2.0\n\n    MODEL PHENOTYPE BASEPHENOTYPE\n0 pfh0_robe020 47 0\n')
        active = parts.active_models(source)
        for name in ('pfh0', 'pfh47', 'pfh0_pelvis010', 'pfh0_pelvis001'):
            shutil.copyfile(active[name], root / active[name].relative_to(source))
        for relative in parts.IMPLEMENTATION:
            shutil.copyfile(source / relative, root / relative)

    def test_apply_then_check_detects_stale_overridden_and_retired_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            parts.run(root, apply=True)
            manifest = json.loads((root / parts.MANIFEST).read_text())
            self.assertEqual(['pfh0_pelvis010'], [r['source'] for r in manifest['models'].values()])
            [(name, record)] = manifest['models'].items()
            parts.run(root)
            # A higher-priority override replaces the audited source.
            shutil.copyfile(root / 'sw_pt_pelvis/pfh0_pelvis001.mdl', root / 'override/pfh0_pelvis010.mdl')
            with self.assertRaisesRegex(ValueError, 'Stale part skin assets'):
                parts.run(root)
            (root / 'override/pfh0_pelvis010.mdl').unlink()
            parts.run(root)
            # A changed robe body changes every alias's bone tables.
            (root / record['path']).write_bytes(b'stale')
            with self.assertRaisesRegex(ValueError, 'Stale part skin assets'):
                parts.run(root)
            parts.run(root, apply=True)
            # A removed robe body retires, deletes and reserves the alias ID.
            (root / 'sw_2da/roberender.2da').write_text('2DA V2.0\n\n    MODEL PHENOTYPE BASEPHENOTYPE\n')
            parts.run(root, apply=True)
            manifest = json.loads((root / parts.MANIFEST).read_text())
            self.assertEqual([name], manifest['retired'])
            self.assertFalse((root / record['path']).exists())
            (root / record['path']).write_bytes(b'old')
            with self.assertRaisesRegex(ValueError, 'retired'):
                parts.run(root)


if __name__ == '__main__':
    unittest.main()
