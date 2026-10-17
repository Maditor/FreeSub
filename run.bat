@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.bat first. & pause & exit /b 1)
rem  run.bat                     open FreeSub (this console shows the log)
rem  run.bat --check             show GPU / ffmpeg / models
rem  run.bat --cli export FOLDER  run the saved recipe (like the Export button) on every video in FOLDER
.venv\Scripts\python.exe main.py %*
if errorlevel 1 pause
