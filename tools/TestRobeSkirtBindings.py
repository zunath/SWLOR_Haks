"""Robe 236's lower cloth must move with real body bones in ability poses."""
import json
import math
from pathlib import Path
import struct
import unittest

import CompileModels as mdl
import RobePoseAudit as poses
import RobeSkirtBindings as skirts

ROOT = Path(__file__).resolve().parents[1]


def legacy_fixture(source, wearer, names):
    """Restore the original native skirt weights/binds on the unchanged RGB rig."""
    native, target = poses.Model(source, False), poses.Model(wearer, False)
    native_indices = {name:i for i,(name,*_) in enumerate(native.nodes)}
    target_indices = {name:i for i,(name,*_) in enumerate(target.nodes)}
    result = bytearray(wearer)
    for panel in skirts.PANELS:
        old_offset = native.nodes[native_indices[panel]][4]
        new_offset = target.nodes[target_indices[names[panel]]][4]
        oskin, count, ow, oi, ostride = skirts.skin_layout(native, old_offset)
        skin, new_count, nw, ni, stride = skirts.skin_layout(target, new_offset)
        if count != new_count:
            raise ValueError('Source and converted skirt topology differ')
        palette = struct.unpack_from('<17h', source, oskin+64)
        mapped = [target_indices[names[native.nodes[index][0]]] if index >= 0 else -1 for index in palette]
        struct.pack_into('<17h',result,skin+64,*mapped)
        mapping,length=12+target.uint(skin+20),target.uint(skin+24)
        result[mapping:mapping+length*2]=b'\xff\xff'*length
        for slot,index in enumerate(mapped):
            if index < 0:continue
            struct.pack_into('<h',result,mapping+index*2,slot)
            old=palette[slot]
            for field,size in [(28,16),(40,12)]:
                start=12+native.uint(oskin+field)+old*size
                end=12+target.uint(skin+field)+index*size
                result[end:end+size]=source[start:start+size]
        for vertex in range(count):
            for old,new,size in [(ow+vertex*(ostride or 16),nw+vertex*(stride or 16),16),
                                 (oi+vertex*(ostride or 8),ni+vertex*(stride or 8),8)]:
                result[new:new+size]=source[old:old+size]
    return bytes(result)


def wide_stance(model):
    tracks=[]
    for name,_,part,_,_ in model.nodes:
        if name in ('lthigh_g','rthigh_g'):
            angle=.65 if name.startswith('l') else -.65
            tracks.append((name,None,part,{20:((0.,),[(0.,math.sin(angle/2),0.,math.cos(angle/2))])},0))
    return 1.,tracks


class RobeSkirtBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names=json.loads((ROOT/'tools/RobeRgbModels.json').read_text())['garment_node_names']

    def cases(self):
        for source_name in sorted(skirts.SOURCES):
            prefix=source_name[:3];name=prefix+'200'
            source=(ROOT/'sw_pt_robe'/f'{source_name}.mdl').read_bytes()
            wearer=(ROOT/'sw_pt_root'/f'{name}.mdl').read_bytes()
            names=self.names[name]
            yield name,source,wearer,names

    def test_all_eight_normal_body_roots_have_leg_bound_skirts(self):
        rows=[line.split() for line in (ROOT/'sw_2da/roberender.2da').read_text().splitlines()[3:] if line.strip()]
        self.assertEqual(skirts.SOURCES,{row[1] for row in rows if row[1] in skirts.SOURCES and row[2:] == ['200','0']})
        for name,_,wearer,names in self.cases():
            bindings=poses.Model(wearer,False).skin_bindings()
            for panel in skirts.PANELS:
                bones=set(bindings[names[panel]])
                self.assertFalse(any('coat_' in bone for bone in bones),name)
                self.assertIn('pelvis_g',bones,name)
                for side in ('lr' if panel=='skirt_b' else 'l' if panel=='skirt_fl' else 'r'):
                    self.assertTrue({f'{side}thigh_g',f'{side}shin_g'} <= bones,name)

    def test_legacy_repair_preserves_rest_shape_skeleton_and_every_other_byte(self):
        for name,source,wearer,names in self.cases():
            with self.subTest(model=name):
                before=legacy_fixture(source,wearer,names)
                after=skirts.bind_skirts(before,names)
                self.assertEqual(len(before),len(after))
                self.assertLess(skirts.validate_rest_pose(before,after,names),2e-5)
                self.assertEqual(after,skirts.bind_skirts(after,names))
                model=poses.Model(before,False);allowed=set()
                for mesh,_,_,_,offset in model.nodes:
                    if mesh not in {names[p] for p in skirts.PANELS}:continue
                    skin,count,weights,indices,stride=skirts.skin_layout(model,offset)
                    allowed.update(range(skin+64,skin+98))
                    for pointer_field,count_field,size in [(20,24,2),(28,32,16),(40,44,12)]:
                        start=12+model.uint(skin+pointer_field)
                        allowed.update(range(start,start+model.uint(skin+count_field)*size))
                    for vertex in range(count):
                        allowed.update(range(weights+vertex*(stride or 16),weights+vertex*(stride or 16)+16))
                        allowed.update(range(indices+vertex*(stride or 8),indices+vertex*(stride or 8)+8))
                self.assertTrue(all(index in allowed for index,(a,b) in enumerate(zip(before,after)) if a!=b))

    def test_body_only_wide_stance_moves_cloth_with_the_corresponding_leg(self):
        for name,source,wearer,names in self.cases():
            with self.subTest(model=name):
                before=legacy_fixture(source,wearer,names)
                after=skirts.bind_skirts(before,names)
                original,target=poses.Model(before,False),poses.Model(after,False)
                clip=wide_stance(target)
                old_indices={node[0]:node[4] for node in original.nodes}
                new_indices={node[0]:node[4] for node in target.nodes}
                bind,posed=target.pose(),target.pose(clip,.5,target.scale)
                source_geometry={n:v for n,_,v in mdl.binary_nodes(source) if v}
                moved=0.;checked=0
                for panel,side in [('skirt_fl','l'),('skirt_fr','r')]:
                    mesh=names[panel]
                    resting=list(skirts.vertices(original,old_indices[mesh]))
                    old=list(skirts.vertices(original,old_indices[mesh],clip,.5))
                    actual=list(skirts.vertices(target,new_indices[mesh],clip,.5))
                    self.assertLess(max(math.dist(a,b) for a,b in zip(resting,old)),1e-7)
                    for index,row in enumerate(source_geometry[panel]['weights']):
                        # Vertices fully weighted to the distal cloth helper must
                        # follow the real shin, even with no coat tracks at all.
                        if row != [f'coat_f{side}2',1.]:continue
                        bone=f'{side}shin_g';bp,bq,bs=bind[bone];pp,pq,ps=posed[bone]
                        local=poses.rotate((-bq[0],-bq[1],-bq[2],bq[3]),tuple((a-b)/bs for a,b in zip(resting[index],bp)))
                        expected=tuple(a+b for a,b in zip(pp,poses.rotate(pq,tuple(v*ps for v in local))))
                        self.assertLess(math.dist(expected,actual[index]),2e-5)
                        moved=max(moved,math.dist(old[index],actual[index]));checked+=1
                self.assertGreater(checked,10)
                self.assertGreater(moved,.15)


if __name__=='__main__':unittest.main()
