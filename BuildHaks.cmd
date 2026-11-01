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
