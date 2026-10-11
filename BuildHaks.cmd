@echo off
python -B tools/ModelLodAssets.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/GenerateRobeCloakModels.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/CloakTintAssets.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckNeckModels.py
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckNwsyncResources.py
if errorlevel 1 exit /b %errorlevel%
call "%~dp0..\tools\SWLOR.CLI\RunCLI.cmd" -k
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckPackedLods.py
