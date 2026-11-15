"""Pelvis 10 and 11 must light like the parts worn with them.

The tint shaders multiply the dyed color by each mesh's diffuse color and the
area's ambient light by its ambient color. Pelvis 10 and 11 shipped with both at
0.59, so the same dye drew about 40% darker on the pelvis than on the chest and
thighs beside it, and the gap changed with each area's lighting. Highlighting
the wearer (Tab) hid it. Every playable body's model and every part skin alias
generated from it must draw its visible meshes at full ambient and diffuse.

The female models also carried a hidden shadow-only copy of the skin mesh under
the same node name, which the male models never had. It is unlinked so every
body draws and shadows the pelvis from its one skin mesh.
"""
import json
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
BODIES = [f"p{gender}{race}0" for race in "heod" for gender in "mf"]
SOURCES = [f"{body}_pelvis{style}" for body in BODIES for style in ("010", "011")]


def mesh_nodes(data):
    """(name, rendered, diffuse, ambient) for every mesh node linked into a compiled MDL."""
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]
    if len(data) < 244 or uint(0) != 0:
        raise ValueError("expected a compiled MDL")
    result, pending, seen = [], [uint(84)], set()
    while pending:
        pointer = pending.pop()
        if pointer in seen:
            continue
        seen.add(pointer)
        node = 12 + pointer
        if uint(node + 108) & 0x20:
            mesh = node + 112
            result.append((data[node + 32:node + 64].split(b"\0", 1)[0].decode(), bool(uint(mesh + 108)),
                           struct.unpack_from("<3f", data, mesh + 60), struct.unpack_from("<3f", data, mesh + 72)))
        pending.extend(uint(12 + uint(node + 72) + index * 4) for index in range(uint(node + 76)))
    return result


def models():
    aliases = json.loads((ROOT / "tools/PartSkinModels.json").read_text())["models"]
    names = {name: f"sw_pt_pelvis/{name}.mdl" for name in SOURCES}
    names.update({name: record["path"] for name, record in aliases.items() if record["source"] in SOURCES})
    return names


class PelvisMaterialColorTests(unittest.TestCase):
    def test_every_playable_body_and_alias_is_covered(self):
        names = models()
        self.assertEqual(16, len(SOURCES))
        # Each source has at least one robe-body alias; a lost alias would skip the check.
        sources = {json.loads((ROOT / "tools/PartSkinModels.json").read_text())["models"][name]["source"]
                   for name in names if name not in SOURCES}
        self.assertEqual(set(SOURCES), sources)

    def test_visible_meshes_draw_at_full_ambient_and_diffuse(self):
        for name, path in sorted(models().items()):
            with self.subTest(model=name):
                meshes = [mesh for mesh in mesh_nodes((ROOT / path).read_bytes()) if mesh[1]]
                self.assertTrue(meshes, "no visible mesh")
                for node, _, diffuse, ambient in meshes:
                    self.assertEqual((1.0, 1.0, 1.0), diffuse, node)
                    self.assertEqual((1.0, 1.0, 1.0), ambient, node)

    def test_no_hidden_shadow_copy_of_the_skin_mesh(self):
        for name, path in sorted(models().items()):
            with self.subTest(model=name):
                meshes = mesh_nodes((ROOT / path).read_bytes())
                self.assertEqual([True], [rendered for _, rendered, _, _ in meshes])


if __name__ == "__main__":
    unittest.main()
