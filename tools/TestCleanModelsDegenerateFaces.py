"""Round-trip validation must accept the zero-area faces CleanModels v4.4.2 drops, and only those."""
import unittest

import CompileModels as mdl


def mesh(faces):
    return {"verts": [["0", "0", "0"], ["1", "0", "0"], ["0", "1", "0"], ["1", "1", "0"]],
            "tverts": [["0", "0"], ["1", "0"], ["0", "1"], ["1", "1"]],
            "faces": [[*face[:3], "1", *face[:3], "0"] for face in faces]}


class DegenerateFaceTests(unittest.TestCase):
    def test_faces_repeating_a_vertex_may_be_dropped(self):
        before = mesh([["0", "1", "2"], ["1", "1", "3"], ["1", "3", "2"]])
        mdl.validate_geometry("robe", before, mesh([["0", "1", "2"], ["1", "3", "2"]]))

    def test_faces_whose_duplicate_corners_weld_may_be_dropped(self):
        before = mesh([["0", "1", "2"], ["1", "3", "2"]])
        before["verts"].append(["1", "1", "0"])
        before["tverts"].append(["1", "1"])
        before["faces"].append(["1", "3", "4", "1", "1", "3", "4", "0"])
        mdl.validate_geometry("robe", before, mesh([["0", "1", "2"], ["1", "3", "2"]]))

    def test_binaries_that_kept_zero_area_faces_still_match(self):
        before = mesh([["0", "1", "2"], ["1", "1", "3"], ["1", "3", "2"]])
        mdl.validate_geometry("robe", before, mesh([["0", "1", "2"], ["1", "1", "3"], ["1", "3", "2"]]))

    def test_a_dropped_face_with_area_still_fails(self):
        before = mesh([["0", "1", "2"], ["1", "3", "2"]])
        with self.assertRaisesRegex(ValueError, "face count changed"):
            mdl.validate_geometry("robe", before, mesh([["0", "1", "2"]]))


if __name__ == "__main__":
    unittest.main()
