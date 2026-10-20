"""Keep static chest lights and Leather 2 gauntlets through tint regeneration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import GenerateTintMapAssets as tint
import HavocGlowAssets as glow


class HavocGlowTests(unittest.TestCase):
    def test_regeneration_keeps_gauntlet_shader_and_single_leather2_parameter(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pmh0_forer282.mtr'
            path.write_text('renderhint NormalAndSpecMapped\ncustomshaderVS vslit_sm_nm\ntexture1 bracer_n\ntexture5 bracer_i\nparameter float glowLayer 5.0\nparameter float glowLayer 7.0\n')
            with patch.object(tint,'source_mtr_paths',return_value=[]), patch.object(tint,'source_shader_config_lines',return_value=[]):
                text=tint.tint_material_text(path,path.stem,'mask',746,746)
            self.assertIn('customshaderFS fs_hav_emit\n',text)
            self.assertEqual(text.count('parameter float glowLayer'),1)
            self.assertIn('parameter float glowLayer 7.0\n',text)
            self.assertIn('texture1 bracer_n\n',text)
            self.assertIn('texture5 bracer_i\n',text)
            path.write_text(text)
            self.assertEqual(tint.check_tint_mtr_structure(path),[])

    def test_chest_and_unrelated_mapped_materials_keep_standard_shader(self):
        for name in ('pmh0_chest316','pfh0_chest316','pfh0_chest317','pmh0_forer281'):
            self.assertEqual(tint.tint_fragment_shader(name,['texture1 normal']),tint.TINT_MAPPED_FRAGMENT_SHADER)

    def test_overlay_catalog_requires_explicit_binding_and_havoc_slot(self):
        models={name:Path(name) for name in ('pmh0_forer282','pfh0_forel282','pmo0_forer281','pmh0_chest316','pma0_forer282','pfg0_forel282')}
        def bindings(path):
            return [('troopbracer',None)] if path.name=='pfh0_forel282' else [('troopbracer','troopbracer')]*2
        self.assertEqual(glow.model_rows(models,bindings),[('pmh0_forer282','troopbracer',(7,))])

    def test_static_chest_shader_uses_authored_emission_without_dye_lookup(self):
        root=Path(__file__).resolve().parents[1]
        shader=(root/'sw_shader/fs_hav_static.shd').read_text().split('void main ()',1)[1]
        self.assertIn('texture2D(texUnit5, vTexCoords.xy).rgb * LampRegion',shader)
        self.assertNotIn('ResolveTintColor',shader)
        self.assertEqual(glow.validate(root),[])


if __name__=='__main__': unittest.main()
