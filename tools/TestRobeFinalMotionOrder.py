"""The existing native coat tracks must be restored before preservation is checked."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import GenerateRobeRgbModels as owner
import FillRobeEmoteMotion as emote
import RobeAnimationBanks as banks
import RobePoseAudit as poses
import RobeSharingAudit as sharing

class RobeFinalMotionOrderTests(unittest.TestCase):
    def test_native_emote_preservation_audits_the_final_packaged_program(self):
        manifest = json.loads(owner.MANIFEST.read_text())
        head = 'pfd_ra130'
        raw = banks.join([(owner.ROOT / 'sw_anim_f' / (name + '.mdl')).read_bytes()
                          for name in manifest['animation_bank_sets'][head]['parts']])
        bridge = banks.CompiledBridge(raw)
        clips = {name: clip for name, clip in zip(bridge.clip_names, bridge.clips)}
        original = bytes(bridge.pack(head, bridge.parent, [clips[name] for name in ['custom10end', 'pause1']]))
        rig = emote.Rig(original)
        cloth = {rig.model.nodes[index][0] for index in rig.cloth
                 if rig.model.nodes[index][0].startswith('rg_coat_root')}
        header, events, root = emote.read_clip(clips['custom10end'])
        def clear(node):
            if node.name in cloth:
                node.keys, node.values = b'', b''
            for child in node.children:
                clear(child)
        clear(root)
        cold = bytes(bridge.pack(head, bridge.parent,
                     [emote.write_clip(header, events, root, head), clips['pause1']]))
        names = {name: name for name in cloth}
        with self.assertRaisesRegex(ValueError, 'garment animation controllers changed'):
            sharing.validate_joints(poses.Model(original), poses.Model(cold), names)
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            (stage / 'binary').mkdir()
            path = stage / 'binary' / (head + '.mdl')
            path.write_bytes(cold)
            # Body attachment restoration is independently covered; these fixture tracks are complete.
            with patch.object(owner.body_tracks, 'restore', side_effect=lambda data, library: (data, [])):
                changed = owner.prepare_animation_models(stage, [head], object())
            final = path.read_bytes()
            self.assertEqual(changed[head]['emote_clips'], 1)
            self.assertEqual(sharing.validate_joints(poses.Model(original), poses.Model(final), names), 2)
            with patch.object(owner, 'ROOT', stage / 'owned-root'), patch.object(owner.emote_motion, 'fill', side_effect=AssertionError('Reprocessing audited motion')):
                packaged, records, stale = owner.package_animation_models(stage, [head], {head}, {}, {}, object(), prepared=True)
            self.assertEqual(path.read_bytes(), final)
            self.assertEqual(packaged, {head})
            self.assertEqual(stale, set())

if __name__ == '__main__':
    unittest.main()
