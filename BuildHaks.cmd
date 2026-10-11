@echo off
python -B tools/CheckNeckModels.py
if errorlevel 1 exit /b %errorlevel%
python -B tools/CheckNwsyncResources.py
if errorlevel 1 exit /b %errorlevel%
NWN.FinalFantasy.CLI.exe -k
