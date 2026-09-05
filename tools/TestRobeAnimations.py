"""Regression checks for garment animation overlays and body attachment tracks."""
import unittest

import CompileModels as mdl
import RobeAnimations as robe


def model(name, parent, nodes, clips=""):
    return (f"newmodel {name}\nsetsupermodel {name} {parent}\nbeginmodelgeom {name}\n"
            f"node dummy {name}\nparent null\nendnode\n{nodes}"
            f"endmodelgeom {name}\n{clips}donemodel {name}\n")


def node(name, parent, fields=""):
    return f"node dummy {name}\nparent {parent}\n{fields}\nendnode\n"


def clip(owner, length, nodes):
    return f"newanim walk {owner}\nlength {length}\ntranstime 0.2\nanimroot rootdummy\nevent 0.5 step\n{nodes}doneanim walk {owner}\n"


class RobeAnimationTests(unittest.TestCase):
    def fixtures(self):
        body_nodes = node("rootdummy", "body") + node("head_g", "rootdummy", "position 0 0 2") + node("lthigh_g", "rootdummy")
        body_tracks = node("body", "null") + node("rootdummy", "body") + node("head_g", "rootdummy", "orientationkey 2\n0 1 0 0 0\n1 1 0 0 0.2") + node("lthigh_g", "rootdummy", "positionkey 2\n0 0 0 0\n1 0 1 0")
        overlay_nodes = node("rootdummy", "coat") + node("coat_tail", "rootdummy")
        overlay_tracks = node("coat", "null") + node("rootdummy", "coat") + node("coat_tail", "rootdummy", "orientationkey 2\n0 0 1 0 0\n2 0 1 0 1")
        return {"body": model("body", "null", body_nodes, clip("body", 1, body_tracks)),
                "coat": model("coat", "body", overlay_nodes, clip("coat", 2, overlay_tracks)),
                "garment": model("garment", "coat", node("rootdummy", "garment"))}

    def test_incomplete_garment_preserves_body_and_coat_tracks(self):
        result = robe.bridge("body", "garment", self.fixtures().get, "combined").decode()
        geometry = {n: p for _, n, p in robe.geometry(result)}
        self.assertEqual(geometry["head_g"]["position"], ["0", "0", "2"])
        animation = next(robe.ANIMATION.finditer(result))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertIn("orientationkey", tracks["head_g"])
        self.assertIn("positionkey", tracks["lthigh_g"])
        self.assertIn("orientationkey", tracks["coat_tail"])
        self.assertEqual(tracks["coat_tail"]["parent"], ["rootdummy"])

    def test_coat_tracks_follow_body_duration_and_events(self):
        result = robe.bridge("body", "garment", self.fixtures().get, "combined").decode()
        animation = next(robe.ANIMATION.finditer(result))
        tracks = {n: p for _, n, p in mdl.parse_nodes(animation[3])}
        self.assertIn("length 1", animation[3])
        self.assertIn("event 0.5 step", animation[3])
        self.assertEqual(tracks["coat_tail"]["orientationkey"][-1], ["1", "0", "1", "0", "1"])

    def test_ordinary_garment_inherits_complete_body_directly(self):
        fixtures = self.fixtures()
        fixtures["garment"] = model("garment", "body", node("rootdummy", "garment"))
        self.assertIsNone(robe.bridge("body", "garment", fixtures.get, "combined"))

    def test_invalid_parent_does_not_silently_retarget_body(self):
        fixtures = self.fixtures()
        fixtures["coat"] = fixtures["coat"].replace("parent coat\n", "parent head_g\n", 1)
        with self.assertRaisesRegex(ValueError, "incompatible animation parent"):
            robe.bridge("body", "garment", fixtures.get, "combined")


if __name__ == "__main__":
    unittest.main()
