@echo off
python -B tools/ModelLodAssets.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/GenerateRobeCloakModels.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/CloakTintAssets.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckDyeAppearance.py
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckNeckModels.py
if errorlevel 1 exit /b %errorlevel%
python -B tools/GenerateShoulderModels.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/PelvisSkirtBindings.py --check
if errorlevel 1 exit /b %errorlevel%
rem These compiled-mesh and shared-flap guards need HAK sources, not installed stock art.
python -B tools/TestPelvisSkirtBindings.py ^
  PelvisSkirtBindingTests.test_recipes_reproduce_the_reviewed_binary ^
  PelvisSkirtBindingTests.test_release_provenance_is_pinned ^
  PelvisSkirtBindingTests.test_only_local_geometry_metadata_and_canonical_identity_change ^
  PelvisSkirtBindingTests.test_clearance_is_local_and_keeps_rear_waist_hem_and_width ^
  PelvisSkirtBindingTests.test_clearance_does_not_collapse_or_invert_authored_faces ^
  PelvisSkirtBindingTests.test_bounds_normals_and_face_planes_match_the_new_positions
if errorlevel 1 exit /b %errorlevel%
python -B tools/TestFillEmoteFlapTracks.py ^
  EmoteFlapTrackTests.test_libraries_rebuild_and_every_custom_emote_holds_the_flaps ^
  EmoteFlapTrackTests.test_only_registered_skirts_hang_from_the_flaps ^
  EmoteFlapTrackTests.test_other_clips_and_bytes_are_unchanged
if errorlevel 1 exit /b %errorlevel%
python -B tools/GeneratePartSkinModels.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/GenerateRobeShinModels.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/SkinPickBounds.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckNwsyncResources.py
if errorlevel 1 exit /b %errorlevel%
set "PACKER=%~dp0..\tools\SWLOR.CLI\RunCLI.cmd"
if exist "%PACKER%" goto pack
rem The bundled NWN.FinalFantasy.CLI.exe drops the .lod model redirects, so never fall back to it.
echo BuildHaks.cmd must run from the SWLOR_NWN checkout that contains this submodule; its tools\SWLOR.CLI\RunCLI.cmd packs the .lod model redirects.
exit /b 1
:pack
call "%PACKER%" -k
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckPackedLods.py
