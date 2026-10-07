"""Exercise head transport assets and exact helmet geometry preservation."""
from pathlib import Path
import struct
import unittest

import CompileModels as mdl
import GenerateHelmetRgbModels as helmets
import GenerateTintMapAssets as tint


def helmet_heads():
    """Pairs of (helm_NNN, [pmh0_head1NNN, pfh0_head1NNN]) for every tintable helmet."""
    registered = sorted({line.split()[1] for line in tint.OUTPUT_2DA.read_text().splitlines()[3:]
                         if len(line.split()) == 4 and helmets.PATTERN.fullmatch(line.split()[1])})
    for name in registered:
        head = helmets.HEAD_BASE + int(helmets.PATTERN.fullmatch(name)[1])
        yield name, [f"p{gender}h0_head{head}" for gender in ("m", "f")]


class HelmetRgbModelTests(unittest.TestCase):
    def test_every_registered_helmet_has_both_human_heads(self):
        outputs = helmets.plan()
        pairs = list(helmet_heads())
        self.assertIn('helm_114', [name for name, _ in pairs])
        self.assertEqual({helmets.DIRECTORY / (target + '.mdl') for _, targets in pairs for target in targets},
                         set(outputs))
        self.assertEqual([], helmets.check())

    def test_all_source_geometry_and_materials_survive_exactly(self):
        active = tint.find_active_models()
        for source, targets in helmet_heads():
            before = active[source].read_bytes()
            for target in targets:
                with self.subTest(source=source, target=target):
                    after = active[target].read_bytes()
                    # Compare geometry, skin weights, controllers, normals, colors,
                    # UVs and raw vertex/index buffers without a lossy decompile.
                    before_nodes = mdl.binary_nodes(before)
                    after_nodes = mdl.binary_nodes(after)
                    expected_nodes = [(target if name == source else name, rotations, geometry)
                                      for name, rotations, geometry in before_nodes]
                    self.assertEqual(expected_nodes, after_nodes)
                    model_end = 12 + struct.unpack_from('<I', before, 4)[0]
                    self.assertEqual(before[model_end:], after[model_end:])
                    before_fields = tint.read_binary_model_material_fields(Path(source + '.mdl'), before)
                    after_fields = tint.read_binary_model_material_fields(Path(source + '.mdl'), after)
                    self.assertEqual(before_fields, after_fields)
                    self.assertEqual(before[180:244], after[180:244])  # supermodel

    def test_proxy_heads_cannot_be_reinterpreted_as_creature_owned_materials(self):
        source = tint.find_tint_source_models()
        for model, targets in helmet_heads():
            self.assertIn(model, source)
            for target in targets:
                self.assertNotIn(target, source)

    def test_malformed_binary_and_overlong_resource_names_fail_closed(self):
        with self.assertRaises(ValueError):
            helmets.rename_model(b'not a model', 'helm_114', 'pmh0_head1114')
        with self.assertRaises(ValueError):
            helmets.rename_model(b'', 'helm_114', 'pmh0_head1114_verylong')


if __name__ == '__main__':
    unittest.main()
