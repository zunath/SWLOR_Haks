"""Shared body animation sources must retain every wearer's independent garment motion."""
from pathlib import Path
import tempfile
import unittest

import CompileModels as mdl
import RobeAnimations as anim
import RobeSkeleton as legacy
import RobePoseAudit as poses
import SharedRobeFamilies as shared
from RobePoseAudit import sample
from TestRobeAnimations import model, node, clip
import TestRobeSkeleton as skeleton_tests


def transforms(props):
    return {key: value for key, value in props.items() if key != "parent"}


def clips(text):
    return {match[1]: (match[3][:anim.NODE.search(match[3]).start()],
                       {name: props for _, name, props in mdl.parse_nodes(match[3])})
            for match in anim.ANIMATION.finditer(text)}


class SharedRobeFamilyTests(unittest.TestCase):
    def fixtures(self):
        fixtures = skeleton_tests.IndependentRobeSkeletonTests().fixtures()
        for name in ("same", "bind", "parent", "moving"):
            fixtures[name] = fixtures["garment"].replace("garment", name)
        fixtures["bind"] = fixtures["bind"].replace("position 2 0 0", "position 3 0 0")
        fixtures["parent"] = fixtures["parent"].replace("parent rootdummy\nposition 2", "parent parent\nposition 2")
        motion = clip("coat", 2, node("coat", "null") + node("rootdummy", "coat") +
                      node("arm_g", "rootdummy", "orientationkey 2\n0 1 0 0 0.0000001234\n2 1 0 0 2"))
        fixtures["coat"] = model("coat", "body", node("rootdummy", "coat") + node("arm_g", "rootdummy"), motion)
        fixtures["moving"] = fixtures["moving"].replace("setsupermodel moving body", "setsupermodel moving coat")
        return fixtures

    def build(self, fixtures, names=None, body_track_names=None):
        result = shared.Families(fixtures.get, body_track_names)
        for name in names or ("garment", "same", "bind", "parent", "moving"):
            key = result.add("body", fixtures[name], name)
        return result, key

    def test_every_member_preserves_original_bind_parents_controllers_headers_and_skin_weights(self):
        fixtures = self.fixtures()
        for scale in (1, 0.5):
            with self.subTest(scale=scale):
                selected = dict(fixtures)
                selected["body"] = selected["body"].replace("beginmodelgeom", f"setanimationscale {scale}\nbeginmodelgeom")
                families, key = self.build(selected)
                output = families.bridge(key, "bridge").decode("latin1")
                geometry = {name: props for _, name, props in anim.geometry(output)}
                combined_clips = clips(output)
                for robe in families.members:
                    # A separate legacy family provides this wearer's authored
                    # bind rather than another member's first-wins bind record.
                    original = legacy.Families(selected.get)
                    old_key = original.add("body", selected[robe], robe)
                    old_geometry = {name: props for _, name, props in anim.geometry(original.bridge(old_key, "bridge").decode())}
                    old_clips = clips(original.bridge(old_key, "bridge").decode())
                    old_wearer, old_names = original.body_root(robe, "wearer", "bridge")
                    old_wearer_geometry = {name: props for _, name, props in anim.geometry(old_wearer.decode())}
                    wearer, names = families.body_root(robe, "wearer", "bridge")
                    wearer_geometry = {name: props for _, name, props in anim.geometry(wearer.decode())}
                    self.assertEqual(len(names), len(set(names.values())))
                    for original_name, old_alias in old_names.items():
                        if old_alias not in old_geometry:
                            continue
                        alias = names[original_name]
                        if wearer_geometry[alias]["parent"] == [original_name]:
                            # Wearer bones and pure copies of body joints are bound
                            # beneath the body joint with no tracks of their own.
                            for name, (_, new_tracks) in combined_clips.items():
                                self.assertEqual({}, transforms(new_tracks.get(alias, {})), (robe, name))
                            continue
                        self.assertEqual(transforms(old_wearer_geometry[old_alias]),
                                         transforms(wearer_geometry[alias]), (robe, original_name))
                        self.assertEqual(wearer_geometry[alias]["parent"],
                                         ["wearer" if geometry[alias]["parent"] == ["bridge"] else geometry[alias]["parent"][0]])
                        for name, (header, old_tracks) in old_clips.items():
                            new_header, new_tracks = combined_clips[name]
                            self.assertEqual(header, new_header)
                            self.assertEqual(transforms(old_tracks.get(old_alias, {})),
                                             transforms(new_tracks.get(alias, {})), (robe, original_name, name))
                    self.assertEqual([[names["arm_g"], "1"]], wearer_geometry[names["sleeve"]]["weights"])
                tiny = families.body_root("moving", "wearer", "bridge")[1]["arm_g"]
                self.assertEqual("0.0000001234", combined_clips["walk"][1][tiny]["orientationkey"][0][-1])

    def test_identical_motion_and_parents_share_aliases_despite_different_binds(self):
        families, key = self.build(self.fixtures())
        aliases = {name: families.body_root(name, "wearer", "bridge")[1] for name in families.members}
        self.assertEqual(aliases["garment"]["arm_g"], aliases["same"]["arm_g"])
        self.assertEqual(aliases["garment"]["arm_g"], aliases["bind"]["arm_g"])
        for name in ("parent", "moving"):
            self.assertNotEqual(aliases["garment"]["arm_g"], aliases[name]["arm_g"], name)
        stats = families.stats()
        self.assertEqual(1, stats["shared_groups"])
        self.assertEqual(5, stats["bases"][0]["wearers"])
        self.assertEqual(len(families.prepared[key]["joints"]), stats["bases"][0]["canonical_garment_nodes"])
        self.assertGreater(stats["bases"][0]["old_garment_paths"], 0)

    def test_body_tracks_are_emitted_once_and_keep_native_part_remapping(self):
        fixtures = self.fixtures()
        remap = lambda base, owner, name: {"hand_g": "hand_g"}
        families, key = self.build(fixtures, body_track_names=remap)
        output = families.bridge(key, "bridge").decode()
        self.assertEqual(1, len(list(anim.ANIMATION.finditer(output))))
        match = next(anim.ANIMATION.finditer(output))
        nodes = mdl.parse_nodes(match[3])
        self.assertEqual(1, sum(name == "hand_g" for _, name, _ in nodes))
        tracks = dict((name, props) for _, name, props in nodes)
        self.assertNotIn("orientationkey", tracks.get("arm_g", {}))
        self.assertIn("position", tracks["hand_g"])
        garment_arm = families.body_root("garment", "wearer", "bridge")[1]["arm_g"]
        self.assertIn("orientationkey", tracks[garment_arm])

    def test_native_wearers_keep_multilevel_defaults_when_shared_tracks_omit_channels(self):
        geometry = node("rootdummy", "body") + node("arm_g", "rootdummy", "position 1 0 0")
        geometry += node("hand_g", "arm_g", "position 0 0 -1")
        motion = clip("body", 1, node("body", "null") + node("rootdummy", "body") +
                      node("arm_g", "rootdummy", "orientationkey 2\n0 0 0 1 0\n1 0 0 1 0.8") +
                      node("hand_g", "arm_g", "scale 1.1"))
        resting = clip("body", 1, node("body", "null") + node("rootdummy", "body")).replace("walk", "rest")
        fixtures = {"body": model("body", "null", geometry, motion + resting)}
        for name, offset in (("coat_a", 1), ("coat_b", 2)):
            garment = node("rootdummy", name, f"position {offset} 0 0\norientation 0 0 1 0.1")
            garment += node("arm_g", "rootdummy", f"position 0 {offset} 0\norientation 0 1 0 {offset/5}")
            garment += node("hand_g", "arm_g", f"position 0 0 -{offset}\norientation 1 0 0 {offset/4}\nscale {offset}")
            fixtures[name] = model(name, "body", garment)
        families, key = self.build(fixtures, ["coat_b", "coat_a"])
        previous = legacy.Families(fixtures.get)
        old_keys = {robe: previous.add("body", fixtures[robe], robe) for robe in ("coat_a", "coat_b")}
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            compiler, _ = mdl.prepare_compiler(stage)
            destination = stage / "binary"
            destination.mkdir()

            def compile_source(name, data):
                path = stage / f"{name}.mdl"
                path.write_bytes(data)
                mdl.run_compiler(compiler, stage, ["-cne", str(path), str(destination) + "/"], name + ".log")
                binary = (destination / path.name).read_bytes()
                path.write_bytes(binary)  # Subsequent wearers inherit this compiled parent.
                return poses.Model(binary)

            compile_source("body", fixtures["body"].encode())
            before_parents = {robe: compile_source("old" + robe, previous.bridge(old_key, "old" + robe))
                              for robe, old_key in old_keys.items()}
            after_parent = compile_source("newbridge", families.bridge(key, "newbridge"))
            alias_maps, resting_positions = [], []
            for robe in ("coat_a", "coat_b"):
                old_source, old_names = previous.body_root(robe, "oldwearer", "old" + robe)
                new_source, new_names = families.body_root(robe, "newwearer", "newbridge")
                before = compile_source("oldwearer", old_source)
                after = compile_source("newwearer", new_source)
                library = poses.Library(lambda name: (stage / f"{name}.mdl").read_bytes())
                self.assertGreater(poses.validate_garment_body_motion(
                    (stage / "newwearer.mdl").read_bytes(), "body", new_names, library), 0)
                alias_maps.append(new_names)
                old_nodes = {item[0]: item for item in before.nodes}
                new_nodes = {item[0]: item for item in after.nodes}
                parent_nodes = {item[0]: item for item in after_parent.nodes}
                for original, old_alias in old_names.items():
                    alias = new_names[original]
                    bound = after.nodes[new_nodes[alias][1]][0] == original
                    if not bound:
                        self.assertEqual(old_nodes[old_alias][3], new_nodes[alias][3], (robe, original))
                    self.assertEqual(parent_nodes[alias][2], new_nodes[alias][2], (robe, original))
                for clip_name in ("walk", "rest"):
                    for time in (0, 0.3, 0.6, 1):
                        new_pose = after.pose(after_parent.clips[clip_name], time)
                        # The bound garment root sits on the wearer's root in every
                        # pose; its descendants keep their own defaults and curves.
                        (bp, bq, _), (gp, gq, _) = new_pose["rootdummy"], new_pose[new_names["rootdummy"]]
                        for left, right in zip(bp, gp):
                            self.assertAlmostEqual(left, right, places=5, msg=(robe, clip_name, time))
                        self.assertTrue(mdl.equivalent_quaternion(bq, gq), (robe, clip_name, time))
                resting_positions.append(after.pose(after_parent.clips["rest"])[new_names["hand_g"]][0])
            # Bound roots carry no motion, so the wearers share one root joint;
            # their descendants keep separate joints for their distinct defaults.
            self.assertEqual(alias_maps[0]["rootdummy"], alias_maps[1]["rootdummy"])
            self.assertNotEqual(resting_positions[0], resting_positions[1])

    def test_latched_root_channels_cannot_separate_the_garment_from_the_body(self):
        """Sitting then pointing: the engine keeps channels a clip omits."""
        geometry = node("rootdummy", "body", "position 0 0 1.2") + node("torso_g", "rootdummy", "position 0 0 0.1")
        geometry += node("rbicep_g", "torso_g", "position 0.2 0 0.3")
        sitting = clip("body", 1, node("body", "null") + node("rootdummy", "body",
                       "positionkey 2\n0 0 0 1.2\n1 0.3 -0.4 0.5\norientationkey 2\n0 1 0 0 0\n1 1 0 0 0.6"))
        # Like custom1lp and the talk clips: rootdummy rotates but omits position.
        pointing = clip("body", 1, node("body", "null") + node("rootdummy", "body", "orientation 0 0 1 0.1") +
                        node("torso_g", "rootdummy") +
                        node("rbicep_g", "torso_g", "orientationkey 2\n0 1 0 0 0\n1 1 0 0 1.4"))
        body = model("body", "null", geometry, sitting.replace("walk", "sitcross") + pointing.replace("walk", "custom1lp"))
        garment = node("rootdummy", "coat", "position 0 0.02 1.21\norientation 0 0 1 0.05")
        garment += node("torso_g", "rootdummy", "position 0 0 0.12") + node("rbicep_g", "torso_g", "position 0.21 0 0.3")
        garment += node("sleeve_g", "rbicep_g", "position 0.1 0 -0.2")
        fixtures = {"body": body, "coat": model("coat", "body", garment)}
        families, key = self.build(fixtures, ["coat"])
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            compiler, _ = mdl.prepare_compiler(stage)
            (stage / "binary").mkdir()

            def compile_source(name, data):
                path = stage / f"{name}.mdl"
                path.write_bytes(data)
                mdl.run_compiler(compiler, stage, ["-cne", str(path), str(stage / "binary") + "/"], name + ".log")
                path.write_bytes((stage / "binary" / path.name).read_bytes())
                return poses.Model(path.read_bytes())

            compile_source("body", body.encode())
            bridge = compile_source("bridge", families.bridge(key, "bridge"))
            source, names = families.body_root("coat", "wearer", "bridge")
            wearer = compile_source("wearer", source)
            library = poses.Library(lambda name: (stage / f"{name}.mdl").read_bytes())
            self.assertEqual(3, poses.validate_garment_body_motion(
                (stage / "wearer.mdl").read_bytes(), "body", names, library))

            state = [(sample(d[8], 0) if 8 in d else (0, 0, 0), sample(d[20], 0, True) if 20 in d else (0, 0, 0, 1),
                      sample(d[36], 0)[0] if 36 in d else 1) for _, _, _, d, _ in wearer.nodes]
            def world():
                result, named = [], {}
                for index, (name, parent, _, _, _) in enumerate(wearer.nodes):
                    p, q, s = state[index]
                    if parent is not None:
                        pp, pq, ps = result[parent]
                        p = tuple(a+b for a, b in zip(pp, poses.rotate(pq, tuple(v*ps for v in p))))
                        q, s = poses.multiply(pq, q), ps*s
                    result.append((p, q, s))
                    named[name] = (p, q, s)
                return named
            def play(clip_name, time):
                tracks = {part: c for _, _, part, c, _ in bridge.clips[clip_name][1] if part >= 0}
                for index, (_, _, part, _, _) in enumerate(wearer.nodes):
                    p, q, s = state[index]
                    c = tracks.get(part, {})
                    state[index] = (sample(c[8], time) if 8 in c else p, sample(c[20], time, True) if 20 in c else q,
                                    sample(c[36], time)[0] if 36 in c else s)
            rest = world()
            play("sitcross", 1)
            play("custom1lp", 0.5)
            posed = world()
            for bone in ("rootdummy", "torso_g", "rbicep_g"):
                # The garment joint keeps its authored offset from the body bone.
                bp, bq, _ = rest[bone]
                gp, _, _ = rest[names[bone]]
                inverse = (-bq[0], -bq[1], -bq[2], bq[3])
                local = poses.rotate(inverse, tuple(a-b for a, b in zip(gp, bp)))
                pp, pq, _ = posed[bone]
                expected = tuple(a+b for a, b in zip(pp, poses.rotate(pq, local)))
                for left, right in zip(expected, posed[names[bone]][0]):
                    self.assertAlmostEqual(left, right, places=4, msg=bone)
            self.assertGreater(abs(posed["rootdummy"][0][2] - rest["rootdummy"][0][2]), 0.5)

    def test_distinct_named_joints_in_one_wearer_never_merge(self):
        fixtures = skeleton_tests.IndependentRobeSkeletonTests().fixtures()
        for name in ("left", "right"):
            fixtures["body"] = fixtures["body"].replace("endmodelgeom body", node(name, "rootdummy") + "endmodelgeom body")
            fixtures["body"] = fixtures["body"].replace("doneanim walk body", node(name, "rootdummy", "position 0 0 1") + "doneanim walk body")
            fixtures["garment"] = fixtures["garment"].replace("endmodelgeom garment", node(name, "rootdummy") + "endmodelgeom garment")
        families, _ = self.build(fixtures, ["garment"])
        _, names = families.body_root("garment", "wearer", "bridge")
        self.assertNotEqual(names["left"], names["right"])

    def test_incompatible_custom_clip_headers_or_body_tracks_fail_closed(self):
        for change in ("length 2", "event 0.75 different", "orientation 1 0 0 0.5"):
            fixtures = skeleton_tests.IndependentRobeSkeletonTests().fixtures()
            original_robe = fixtures["garment"]
            for name in ("coat_a", "coat_b"):
                motion = clip(name, 1, node(name, "null") + node("rootdummy", name) +
                              node("arm_g", "rootdummy", "orientation 1 0 0 0.2")).replace("walk", "custom")
                if name == "coat_b":
                    motion = (motion.replace("length 1", change) if change.startswith("length") else
                              motion.replace("event 0.5 step", change) if change.startswith("event") else
                              motion.replace("orientation 1 0 0 0.2", change))
                fixtures[name] = model(name, "body", node("rootdummy", name), motion)
                robe = "garment" if name == "coat_a" else "other"
                fixtures[robe] = original_robe.replace("garment", robe).replace(
                    "setsupermodel " + robe + " body", "setsupermodel " + robe + " " + name)
            families, key = self.build(fixtures, ["garment", "other"])
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "incompatible shared body"):
                families.bridge(key, "bridge")

    def test_inventory_mismatch_is_not_silently_dropped(self):
        fixtures = self.fixtures()
        fixtures["coat"] = fixtures["coat"].replace("walk", "extra")
        families, key = self.build(fixtures)
        with self.assertRaisesRegex(ValueError, "incompatible shared animation inventories"):
            families.bridge(key, "bridge")

    def test_joint_sharing_compares_later_clips_too(self):
        fixtures = self.fixtures()
        original_walk = next(anim.ANIMATION.finditer(fixtures["body"]))[0]
        fixtures["body"] = fixtures["body"].replace("donemodel body",
            original_walk.replace("walk", "later") + "\ndonemodel body")
        fixtures["coat"] = fixtures["coat"].replace("walk", "later")
        families, key = self.build(fixtures, ["garment", "moving"])
        output = clips(families.bridge(key, "bridge").decode())
        names = {robe: families.body_root(robe, "wearer", "bridge")[1]["arm_g"]
                 for robe in families.members}
        left, right = names["garment"], names["moving"]
        self.assertEqual(transforms(output["walk"][1][left]), transforms(output["walk"][1][right]))
        self.assertNotEqual(transforms(output["later"][1][left]), transforms(output["later"][1][right]))
        self.assertNotEqual(left, right)

    def test_native_bases_remain_separate_and_aliases_are_order_independent(self):
        fixtures = self.fixtures()
        families, key = self.build(fixtures)
        reverse, reversed_key = self.build(fixtures, list(reversed(list(families.members))))
        self.assertEqual(families.bridge(key, "bridge"), reverse.bridge(reversed_key, "bridge"))
        fixtures["small"] = fixtures["body"].replace("body", "small")
        fixtures["smallrobe"] = fixtures["garment"].replace("garment", "smallrobe").replace("body", "small")
        families.add("small", fixtures["smallrobe"])
        self.assertEqual(2, len(families.groups))
        with self.assertRaisesRegex(ValueError, "add every garment"):
            families.add("body", fixtures["same"], "new_member")

    def test_legacy_mapping_and_missing_source_fallback_remain_available(self):
        fixtures = self.fixtures()
        fixtures["garment"] = fixtures["garment"].replace("setsupermodel garment body", "setsupermodel garment missing")
        families, _ = self.build(fixtures, ["garment"])
        _, expected = families.legacy.body_root("garment", "wearer", "legacy")
        self.assertEqual(expected, families.legacy_aliases("garment"))
        self.assertEqual({"garment": ["missing"]}, families.fallbacks)


if __name__ == "__main__":
    unittest.main()
