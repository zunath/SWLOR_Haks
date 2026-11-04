"""Playable bodies must not keep a stock mesh under a replaced human design's texture.

Needs the installed game's data directory for the stock meshes: set NWN_GAME_DATA, or install the
Steam edition in its default location.
"""
from functools import lru_cache
import json
import unittest

import GenerateRacialPartVariants as variants
import RacialPartCoverage as coverage


@lru_cache(None)
def resources():
    if not (coverage.DEFAULT_GAME_DATA / 'nwn_base.key').is_file():
        raise AssertionError(f"Set NWN_GAME_DATA to the game's data directory (missing {coverage.DEFAULT_GAME_DATA})")
    return variants.Resources(coverage.DEFAULT_GAME_DATA)


@lru_cache(None)
def gaps():
    return coverage.gaps(resources())


class StaleVariantTests(unittest.TestCase):
    def test_only_known_gaps_remain(self):
        known = set(json.loads(coverage.KNOWN_GAPS.read_text(encoding='utf-8'))['knownGaps'])
        self.assertEqual(sorted(set(gaps()) - known), [], 'refit these with GenerateRacialPartVariants.py')
        self.assertEqual(sorted(known - set(gaps())), [], 'no longer stale: remove from RacialPartCoverage.json')

    def test_female_cathar_parts_follow_the_replaced_designs(self):
        # Pelvis 14, neck 4 and forearms 6 were stock on the half-orc female body alone (bug 1566876353981186110).
        refits = json.loads(variants.MANIFEST.read_text(encoding='utf-8'))['outputs']
        for target in ('pfo0_pelvis014', 'pfo0_neck004', 'pfo0_forel006', 'pfo0_forer006'):
            self.assertNotIn(target, gaps())
            self.assertEqual(refits[target]['donor'], 'pfh0_' + target.split('_', 1)[1])

    def test_a_stock_mesh_under_a_new_design_is_reported(self):
        checker = coverage.Coverage(resources())
        self.assertEqual(checker.compare('pfh0_pelvis014'), 'design')
        self.assertEqual(checker.compare('pfo0_pelvis013'), 'stock')
        # Pelvis 13 keeps its stock design on the human body too, so no body is stale.
        self.assertNotIn('pfo0_pelvis013', gaps())


if __name__ == '__main__':
    unittest.main()
