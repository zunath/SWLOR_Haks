Neck 152 (Specs Collar) glasses sources

Art by Xisifer. The glasses come from the Armstrong glasses mesh (the two glasses
meshes of armstrong_glasses.mdl, a pmh0_head322 accessory build). Each body's
canonical neck model (p[mf][hedo]0_neck152 in sw_pt_neck) is fitted to that body
and splits the part into three meshes with their own tint materials:

  Mesh             Material (male / female)        Dye layer
  neck152_frames   pmh0_n152fr / pfh0_n152fr        Metal 1
  neck152_lens     pmh0_n152ln / pfh0_n152ln        Leather 1 (mesh alpha 0.28235)
  neck surface     pmh0_n152nk / pfh0_n152nk        Cloth 1

The race variants use their gender's human materials. The frames keep the
authored normal map (sw_pt_neck/neck152_vfx_n.tga) and bind no specular map, so
dyed Metal 1 takes its reflectance from the palette. The lens materials cull back
faces (twosided 0) so the closed lens shell does not blend twice.

The lens materials also set "transparency 1" and "sample_framebuffer 1". That
draws the lenses after the head, so heads with alpha-cutout surfaces (hair, fur)
no longer show a white or opaque fill through the lenses. Keep the lenses a
separate mesh with their node alpha; the generator keeps both directives.

Conversion inputs (GenerateTintMapAssets.py --generate-preserving, copied into
sw_pt_neck under the material names above):
  frames.plt          -> p?h0_n152fr  (1024x512)
  lenses.plt          -> p?h0_n152ln  (1024x512)
  m-neck-cloth1.plt   -> pmh0_n152nk  (64x64)
  f-neck-cloth1.plt   -> pfh0_n152nk  (128x128)

TintMapSources.json marks these six entries explicitMeshMaterial, so the
generator keeps each mesh's own material instead of the part's native palette.
Neck render aliases are generated from the canonical models by GenerateNeckModels.py.
