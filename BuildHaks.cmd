@echo off
python -B tools/GenerateRobeCloakModels.py --check
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckNwsyncResources.py
if errorlevel 1 exit /b %errorlevel%
call "%~dp0..\tools\SWLOR.CLI\RunCLI.cmd" -k
