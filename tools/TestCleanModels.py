"""CleanModels process failures, source defaults, and real EE binary output."""
from pathlib import Path
import json
import math
import os
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import CleanModels as cli
import CompileModels as mdl


class CleanModelsTests(unittest.TestCase):
    def test_only_omitted_geometry_rotations_receive_identity_defaults(self):
        geometry = b"node dummy a\n parent NULL\nendnode\nnode dummy b\n orientation 1 0 0 .5\nendnode\n"
        animation = b"ENDMODELGEOM sample\nnode dummy a\n parent NULL\nendnode\n"
        prepared = cli.explicit_orientations(geometry + animation)
        self.assertEqual(prepared.count(b"orientation 0 0 1 0"), 1)
        self.assertIn(b"orientation 1 0 0 .5", prepared)
        self.assertTrue(prepared.endswith(animation))
        self.assertEqual(prepared, cli.explicit_orientations(prepared))

    def test_json_failures_and_incomplete_reports_fail_even_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.mdl"
            source.write_bytes(b"binary fixture")
            for report in ('{"error":"failed"}', '[]', 'not json', 'null'):
                result = subprocess.CompletedProcess([], 0, report, "diagnostic")
                with self.subTest(report=report), patch.object(cli.subprocess, "run", return_value=result), \
                     self.assertRaises(RuntimeError):
                    cli.run(Path("compiler.exe"), root, "decompile", [source], root, "run.log")
                self.assertIn("diagnostic", (root / "run.log").read_text())

    def test_single_file_gets_an_explicit_destination_and_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.mdl"
            source.write_bytes(b"newmodel sample\n")
            output = root / "output"
            output.mkdir()
            result = subprocess.CompletedProcess([], 0, json.dumps({"file": str(source)}), "")
            with patch.object(cli.subprocess, "run", return_value=result) as run:
                cli.run(Path("compiler.exe"), root, "compile", [source], output, "run.log")
            arguments = run.call_args.args[0]
            self.assertEqual(str(output / source.name), arguments[-1])
            self.assertIn("--resource-dir", arguments)
            self.assertEqual(b"newmodel sample\n", source.read_bytes())

    def test_multiple_inputs_use_a_selected_directory_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "first.mdl", root / "second.mdl"]
            for source in sources:
                source.write_bytes(b"newmodel sample\n")
            output = root / "output"
            output.mkdir()
            result = subprocess.CompletedProcess([], 0, json.dumps([{"file": str(source)} for source in sources]), "")
            with patch.object(cli.subprocess, "run", return_value=result) as run:
                cli.run(Path("compiler.exe"), root, "compile", sources, output, "run.log")
            selection = Path(run.call_args.args[0][-2])
            self.assertTrue(selection.is_dir())
            self.assertEqual({path.name for path in selection.glob("*.mdl")}, {path.name for path in sources})

    def test_unrecognized_compiler_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "cleanmodels.exe"
            executable.write_bytes(b"wrong version")
            with self.assertRaisesRegex(RuntimeError, "Unsupported CleanModels"):
                cli.find_compiler(executable)


@unittest.skipUnless(os.environ.get("CLEANMODELS"), "Set CLEANMODELS for real compiler coverage")
class CleanModelsBinaryTests(unittest.TestCase):
    def test_batch_preserves_material_normals_colors_and_local_head_robe_bones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "binary"
            binary.mkdir()
            inputs = []
            for name in ("pmh0_head001", "pmh0_robe001"):
                source = root / (name + ".mdl")
                source.write_text(f"""newmodel {name}
setsupermodel {name} NULL
classification CHARACTER
beginmodelgeom {name}
node dummy {name}
 parent NULL
endnode
node dummy joint
 parent {name}
 position 0 0 1
endnode
node dummy other
 parent {name}
 position 0 0 2
endnode
node skin panel
 parent {name}
 bitmap diffuse
 materialname normalmat
 verts 6
  0 0 0
  1 0 0
  0 1 0
  0 0 0
  1 0 0
  0 1 0
 tverts 3
  0 0 0
  1 0 0
  0 1 0
 normals 6
  0 .6 .8
  0 .6 .8
  0 .6 .8
  0 .6 .8
  0 .6 .8
  0 .6 .8
 colors 6
  1 0 0
  0 1 0
  0 0 1
  1 0 0
  0 1 0
  0 0 1
 faces 2
  0 1 2 1 0 1 2 0
  3 4 5 1 0 1 2 0
 weights 6
  joint 1
  joint 1
  joint 1
  other 1
  other 1
  other 1
endnode
endmodelgeom {name}
donemodel {name}
""")
                inputs.append(source)
            (root / "normalmat.mtr").write_text("renderhint NormalAndSpecMapped\ntexture0 diffuse\ntexture1 normal\ntexture2 specular\n")
            cli.run(cli.find_compiler(), root, "compile", inputs, binary, "compile.log", [root])
            for source in inputs:
                path = binary / source.name
                cli.validate_binary(path)
                data = path.read_bytes()
                nodes = mdl.binary_nodes(data)
                mesh = next(values for _, _, values in nodes if values is not None)
                self.assertEqual(mesh["weights"], [["joint", 1.0]] * 3 + [["other", 1.0]] * 3)
                self.assertEqual(mesh["colors"], [[1, 0, 0], [0, 1, 0], [0, 0, 1]] * 2)
                for normal in mesh["normals"]:
                    for actual, expected in zip(normal, (0, .6, .8)):
                        self.assertAlmostEqual(actual, expected, places=6)
                offset = mesh["_mesh_offset"]
                self.assertEqual(data[offset + 312:offset + 376].split(b"\0")[0], b"normalmat")
                tangent_pointer = struct.unpack_from("<I", data, offset + 488)[0]
                handedness_pointer = struct.unpack_from("<I", data, offset + 496)[0]
                self.assertNotEqual(tangent_pointer, 0xFFFFFFFF)
                self.assertNotEqual(handedness_pointer, 0xFFFFFFFF)
                raw = 12 + struct.unpack_from("<I", data, 4)[0]
                stride = struct.unpack_from("<I", data, offset + 440)[0]
                for index, normal in enumerate(mesh["normals"]):
                    tangent = struct.unpack_from("<3f", data, raw + tangent_pointer + index * (stride or 12))
                    handedness = struct.unpack_from("<f", data, raw + handedness_pointer + index * (stride or 4))[0]
                    self.assertAlmostEqual(sum(value * value for value in tangent), 1, places=6)
                    self.assertAlmostEqual(sum(a * b for a, b in zip(normal, tangent)), 0, places=6)
                    self.assertIn(handedness, (-1, 1))
                for _, rotations, _ in nodes:
                    for _, quaternion in rotations:
                        self.assertAlmostEqual(math.sqrt(sum(value * value for value in quaternion)), 1, places=6)


if __name__ == "__main__":
    unittest.main()
