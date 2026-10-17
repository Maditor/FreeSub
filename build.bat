@echo off
setlocal
cd /d "%~dp0"
rem Makes dist\FreeSub\FreeSub.exe (one folder) and dist\FreeSub-<version>-win64.zip for a GitHub release.
rem Needs the .venv from setup.bat. ffmpeg, Piper and the NVIDIA libraries are NOT put in the exe:
rem FreeSub downloads them when the user asks (keeps the download small and the licences clean).
echo === FreeSub build ===
echo.
if not exist .venv\Scripts\python.exe (echo Run setup.bat first. & pause & exit /b 1)
set "PY=.venv\Scripts\python.exe"

set /p CUR=<VERSION
set "VER="
set /p VER=Version (Enter = %CUR%): 
"%PY%" tools\build_helper.py version "%VER%"
if errorlevel 1 (pause & exit /b 1)
echo.

echo [1/4] PyInstaller ...
"%PY%" -m pip install --quiet "pyinstaller>=6.10,<7"
if errorlevel 1 (echo Could not install PyInstaller. & pause & exit /b 1)

echo [2/4] Preparing ...
"%PY%" tools\build_helper.py prepare
if errorlevel 1 (pause & exit /b 1)

echo [3/4] Building the exe (a few minutes) ...
if exist dist\FreeSub rmdir /s /q dist\FreeSub
"%PY%" -m PyInstaller --noconfirm --clean --log-level WARN FreeSub.spec
if errorlevel 1 (echo Build failed: see the messages above. & pause & exit /b 1)

echo [4/4] Licences and zip ...
"%PY%" tools\build_helper.py finish
if errorlevel 1 (pause & exit /b 1)

echo.
echo Done:
echo   dist\FreeSub\FreeSub.exe            try it here
echo   dist\FreeSub-*-win64.zip            upload this to the GitHub release
echo.
pause
