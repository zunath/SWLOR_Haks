"""Runtime selection coverage and native resource guards for qualified gun grips."""
import math
from pathlib import Path
import re
import unittest
from RobePoseAudit import Model

ROOT = Path(__file__).resolve().parents[1]


def table(name):
    lines = (ROOT / 'sw_2da' / (name + '.2da')).read_text().splitlines()
    columns = lines[2].split()
    rows = []
    for line in lines[3:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != len(columns) + 1 or max(map(len, fields)) >= 128:
            raise ValueError('Invalid native ' + name + ' row width')
        rows.append(dict(zip(columns, fields[1:])))
    return rows


class WeaponGripRuntimeTablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selections = table('griprender')
        cls.fits = {}
        for row in table('weaponfits'):
            models = [int(v) for v in row['MODEL'].split(',')]
            columns = [row[n].split(',') for n in ('X', 'Y', 'Z', 'HEADING', 'PITCH', 'ROLL')]
            if any(len(c) not in (1, len(models)) for c in columns):
                raise ValueError('Mismatched native placement columns')
            for i, model in enumerate(models):
                key = row['HAND'].lower(), model, row['HOLD']
                if key in cls.fits or not 0 < model <= 65535:
                    raise ValueError('Duplicate or invalid runtime placement')
                cls.fits[key] = tuple(float(c[0 if len(c) == 1 else i]) for c in columns)

    def test_every_pistol_source_resolves_all_38_models_to_a_full_size_placement(self):
        selections, reverse = {}, {}
        for row in self.selections:
            source, render = row['SOURCE'].lower(), row['RENDER'].lower()
            self.assertRegex(source, r'^p[fm][hedoz]0_hand[rl][0-9]{3}$')
            self.assertRegex(render, r'^' + re.escape(source[:10]) + r'[0-9]{3,5}$')
            self.assertEqual(source, reverse.setdefault(render, source))
            model = 0 if row['MODEL'] == '****' else int(row['MODEL'])
            key = source, row['WEAPON'], model
            self.assertNotIn(key, selections)
            selections[key] = render
        sources = {s for s, weapon, model in selections if weapon == 'Sling' and model == 0}
        models = {model for hand, model, hold in self.fits if hold == 'Pistol'}
        self.assertEqual(920, len(sources))
        self.assertEqual(38, len(models))
        for source in sources:
            for model in models:
                render = selections.get((source, 'Sling', model), selections[source, 'Sling', 0])
                self.assertIn((render, model, 'Pistol'), self.fits, (source, model, render))
        self.assertEqual(34960, sum(hold == 'Pistol' for hand, model, hold in self.fits))
        self.assertTrue(all(math.isfinite(v) for fit in self.fits.values() for v in fit))

    def test_selected_rifle_profiles_have_every_motion_and_both_owned_placements(self):
        for row in table('riflegrips'):
            right, left, model = row['RIGHT'].lower(), row['LEFT'].lower(), int(row['MODEL'])
            self.assertEqual(right[:4], left[:4])
            bank = Model((ROOT / 'sw_cr_creature' / ('a_rfl_' + right[:4] + '.mdl')).read_bytes())
            idle = row['IDLE']
            prefix = idle[:-1]
            for role, column in (('i', 'IDLE'), ('r', 'READY'), ('s', 'SHOT')):
                self.assertEqual(prefix + role, row[column])
            for suffix in ('i', 'r', 's', 'b', 'a', 'a_in', 'a_out'):
                self.assertIn(prefix + suffix, bank.clips)
            for hold in ('RifleIdle', 'RifleReady'):
                self.assertIn((right, model, hold), self.fits)
            self.assertAlmostEqual(1.79999995, bank.clips[prefix + 'a'][0], places=6)


if __name__ == '__main__':
    unittest.main()
