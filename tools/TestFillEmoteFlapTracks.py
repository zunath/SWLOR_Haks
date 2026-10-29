"""Custom emotes (FillEmoteFlapTracks.py) must hold the belt flaps at rest instead of the idle's lean.

Needs the installed game's data directory for the stock animations: set NWN_GAME_DATA, or
install the Steam edition in its default location.
"""
import hashlib
import json
import math
from pathlib import Path
import struct
import unittest

import ChestSkirtBindings as skirts
import CompileModels as mdl
import FillEmoteFlapTracks as flaps
import GenerateTintMapAssets as tint
from NeckSkinBindings import body_model
from RobePoseAudit import Model, rotate
from TestChestSkirtBindings import held_clip, library

ROOT = Path(__file__).resolve().parents[1]
# Playable roots on the normal phenotype; all of them play the emote libraries' custom emotes.
WEARERS = ('pmh0', 'pfh0', 'pme0', 'pfe0', 'pmo0', 'pfo0', 'pmd0', 'pfd0')


def flap_lean(pose: dict, name: str) -> float:
    """Degrees between a joint's hanging direction and the root's."""
    down = rotate(pose['rootdummy'][1], (0., 0., -1.))
    hang = rotate(pose[name][1], (0., 0., -1.))
    return math.degrees(math.acos(max(-1., min(1., sum(a * b for a, b in zip(down, hang))))))


class EmoteFlapTrackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = json.loads(flaps.MANIFEST.read_text())['models']
        cls.joints = flaps.stock_joints()
        cls.parts = flaps.flap_parts(cls.joints)

    def test_libraries_rebuild_and_every_custom_emote_holds_the_flaps(self):
        for name, entry in self.models.items():
            with self.subTest(model=name):
                data = (ROOT / entry['path']).read_bytes()
                self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest())
                rebuilt, changed = flaps.build(entry, self.joints)
                self.assertEqual(data, rebuilt)
                self.assertEqual(entry['clips'], changed)
                model = Model(data)
                # The skeleton carries the stock belt and flap joints, so the engine maps their tracks.
                self.assertEqual(self.parts, {node[0]: node[2] for node in model.nodes if node[0] in self.parts})
                for clip, (_, nodes) in model.clips.items():
                    if flaps.EMOTE.fullmatch(clip):
                        tracked = {node[2] for node in nodes if node[3]}
                        self.assertLessEqual(set(self.parts.values()), tracked, clip)

    def test_wearers_play_the_filled_emotes_and_only_humans_and_bothans_reach_the_flaps(self):
        for root in WEARERS:
            with self.subTest(root=root):
                owners = library().owners(mdl.supermodel(tint.find_active_models()[root].read_bytes()))
                self.assertEqual('a_ba_non_combat', owners['custom52lp'])
                self.assertTrue({clip for clip, owner in owners.items() if owner in self.models and flaps.EMOTE.fullmatch(clip)})
                body = body_model(tint.find_active_models()[root].read_bytes())
                numbered = {node[2]: node[0] for node in body.nodes if node[2] in self.parts.values()}
                if root[2] in 'he':
                    self.assertEqual({part: name for name, part in self.parts.items()}, numbered)
                else:
                    # The dwarf and half-orc roots number their flaps apart, so the new tracks reach nothing there.
                    self.assertEqual({}, numbered)

    def test_only_the_rebound_skirts_hang_from_the_flaps(self):
        """The rest keys change only parts skinned to the flaps: the skirts ChestSkirtBindings rebinds.

        Belt 226 and pelvis 198, restored to their original leg weights, no longer use them.
        """
        rebound = set(json.loads(skirts.MANIFEST.read_text())['models'])
        users = set()
        for name, path in tint.find_active_models().items():
            if not (name[:2] in ('pm', 'pf') and name[2] in 'he' and name[3] == '0' and name[4:5] == '_'):
                continue
            data = path.read_bytes()
            if not mdl.binary(data) or not mdl.external_skeleton_name(data):
                continue
            try:
                bones, _ = skirts.skeleton(data)
                skin_list = list(skirts.skins(data, bones))
            except ValueError:
                continue  # A bone table that does not match its skeleton (Bothan female chest 310) binds nothing reliably.
            for skin in skin_list:
                # Read the bones straight from the palette: some exports' weights do not sum to one.
                weighted = {skin['bones'][slot]
                            for vertex in range(skin['count'])
                            for weight, slot in zip(struct.unpack_from('<4f', data, skin['weights'] + vertex * (skin['stride'] or 16)),
                                                    struct.unpack_from('<4h', data, skin['indices'] + vertex * (skin['stride'] or 8)))
                            if weight and 0 <= slot < 17}
                if weighted & set(self.parts):
                    users.add(name)
        self.assertTrue(users)
        self.assertLessEqual(users, rebound)
        self.assertFalse({name for name in users if name.endswith(('_belt226', '_pelvis198'))})

    def test_parade_rest_hangs_the_flaps_straight_under_the_hips(self):
        """In /paraderest the body stands straight; the flaps must hang from it, not lean as in the idle before it."""
        for root in ('pmh0', 'pfh0'):
            with self.subTest(root=root):
                body = Model(tint.find_active_models()[root].read_bytes(), False)
                idle = body.pose(held_clip(root, 'pause1'), 0.)
                parade = body.pose(held_clip(root, 'custom52lp'), 0.)
                # The idle leans the flaps (female pause1: 13 degrees); parade rest no longer holds that.
                self.assertGreater(max(flap_lean(idle, name) for name in self.parts), 3)
                for name in self.parts:
                    self.assertLess(flap_lean(parade, name), .1, name)

    def test_other_clips_and_bytes_are_unchanged(self):
        """Only the node counts and the root dummies' child tables may move; every other byte stays."""
        for name, entry in self.models.items():
            with self.subTest(model=name):
                before, after = skirts.source(entry['sourceBlob']), (ROOT / entry['path']).read_bytes()
                flaps.validate(before, after, entry['clips'])
                size = struct.unpack_from('<I', before, 4)[0]
                model = before[12:12 + size]
                allowed = set()
                headers = [0] + [start for start in flaps.clip_offsets(model)
                                 if model[start + 8:start + 72].split(b'\0', 1)[0].decode() in entry['clips']]
                for header in headers:
                    allowed.update(range(header + 76, header + 80))
                    for node in flaps.nodes(model, struct.unpack_from('<I', model, header + 72)[0]):
                        if flaps.node_name(model, node) == 'rootdummy':
                            allowed.update(range(node + 72, node + 84))
                changed = {offset for offset in range(size) if before[12 + offset] != after[12 + offset]}
                self.assertLessEqual(changed, allowed)
                self.assertEqual(before[:4], after[:4])
                self.assertEqual(before[8:12], after[8:12])


if __name__ == '__main__':
    unittest.main()
