@echo off
setlocal
cd /d "%~dp0"
echo === FreeSub setup ===
echo.

rem FreeSub needs 64-bit Windows and a 64-bit Python 3.11 or 3.12.
set "PYVER=3.12.8"
call :scan
if defined PYCMD goto :havepython

echo No 64-bit Python 3.11 / 3.12 found - installing Python %PYVER% automatically...
call :autoinstall
call :scan
if defined PYCMD goto :havepython

echo.
echo Automatic install did not work (no internet, a proxy, or it was blocked).
echo Please install 64-bit Python 3.12 by hand from:
echo    https://www.python.org/downloads/release/python-3128/
echo  - tick "Add python.exe to PATH" during install
echo Then run setup.bat again.
echo.
pause
exit /b 1

:havepython
echo Using: %PYCMD%

set "HERE=%~dp0"
set "MARK=.venv\freesub_path.txt"
if exist .venv (
    set "OLD="
    if exist "%MARK%" set /p OLD=<"%MARK%"
    call :check_venv
)

if not exist .venv\Scripts\python.exe (
    echo Creating .venv ...
    %PYCMD% -m venv .venv
    if not exist .venv\Scripts\python.exe (echo Could not create the .venv. & pause & exit /b 1)
)
> "%MARK%" echo %HERE%

set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install --upgrade pip
rem a plain onnxruntime (pulled in by an older FreeSub or by hand) hides the DirectML one
"%PY%" -m pip uninstall -y onnxruntime >nul 2>nul
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (echo Install failed. & pause & exit /b 1)
"%PY%" -m pip install --no-deps faster-whisper==1.2.1
if errorlevel 1 (echo Install failed. & pause & exit /b 1)
rem RapidOCR: reads burned-in subtitles (its own deps are in requirements.txt; --no-deps keeps opencv-headless)
"%PY%" -m pip install --no-deps rapidocr==3.9.2
if errorlevel 1 (echo Install failed. & pause & exit /b 1)
rem Piper: offline voice + espeak-ng (also used to read English words the Vietnamese way)
"%PY%" -m pip install --no-deps piper-tts==1.8.0
if errorlevel 1 (echo Install failed. & pause & exit /b 1)

"%PY%" -c "import numpy, PySide6.QtMultimedia" >nul 2>nul
if errorlevel 1 call :ensure_vcredist
"%PY%" -c "import numpy, PySide6.QtMultimedia" >nul 2>nul
if errorlevel 1 (
    echo.
    echo The Python libraries need the Microsoft Visual C++ Runtime, which could not be installed here.
    echo Install it by hand ^(it asks for admin^), then run setup.bat again:
    echo    https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo.
    pause
    exit /b 1
)

echo.
echo Downloading ffmpeg ^(about 100 MB, once^) ...
"%PY%" main.py --get-ffmpeg
if errorlevel 1 (
    echo.
    echo ffmpeg could not be downloaded. FreeSub also uses an ffmpeg found in PATH,
    echo or you can choose its folder in Settings.
)

echo.
"%PY%" main.py --check
echo.
echo Done. Start FreeSub with run.bat
echo (Whisper models are downloaded inside the app the first time you use them.)
pause
exit /b 0

rem =====================================================================
:scan
rem Find a 64-bit Python 3.11-3.12 and put it in PYCMD (quoted if it is a full path).
set "PYCMD="
call :pick py -3.12
if not defined PYCMD call :pick py -3.11
if not defined PYCMD call :pick python
if not defined PYCMD call :pick py
rem freshly-installed per-user Python (PATH is not refreshed in this window): check known folders
if not defined PYCMD call :pickexe "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYCMD call :pickexe "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYCMD call :pickexe "%ProgramFiles%\Python312\python.exe"
if not defined PYCMD call :pickexe "%ProgramFiles%\Python311\python.exe"
exit /b 0

:pick
%* -c "import sys,struct;v=sys.version_info;sys.exit(0 if (3,11)<=(v[0],v[1])<=(3,12) and struct.calcsize('P')==8 else 1)" >nul 2>nul
if not errorlevel 1 set "PYCMD=%*"
exit /b 0

:pickexe
if not exist "%~1" exit /b 0
"%~1" -c "import sys,struct;v=sys.version_info;sys.exit(0 if (3,11)<=(v[0],v[1])<=(3,12) and struct.calcsize('P')==8 else 1)" >nul 2>nul
if not errorlevel 1 set PYCMD="%~1"
exit /b 0

:autoinstall
rem Try winget first (clean); if missing, download the official 64-bit installer from python.org.
where winget >nul 2>nul
if not errorlevel 1 (
    echo Installing with winget ...
    winget install -e --id Python.Python.3.12 --architecture x64 --accept-source-agreements --accept-package-agreements --override "/passive PrependPath=1 Include_launcher=1 Include_pip=1" >nul 2>nul
    call :scan
    if defined PYCMD exit /b 0
)
set "URL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
set "DEST=%TEMP%\freesub_python_%PYVER%.exe"
echo Downloading Python %PYVER% (64-bit) from python.org ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing '%URL%' -OutFile '%DEST%' } catch { exit 1 }"
if not exist "%DEST%" (echo Download failed. & exit /b 1)
echo Installing Python (a progress window will appear) ...
"%DEST%" /passive PrependPath=1 Include_launcher=1 Include_pip=1
del "%DEST%" >nul 2>nul
exit /b 0

:ensure_vcredist
set "VURL=https://aka.ms/vs/17/release/vc_redist.x64.exe"
set "VDEST=%TEMP%\freesub_vcredist_x64.exe"
echo Installing the Microsoft Visual C++ Runtime (x64) - please click Yes if Windows asks ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing '%VURL%' -OutFile '%VDEST%' } catch { exit 1 }"
if not exist "%VDEST%" (echo Could not download the VC++ runtime. & exit /b 1)
"%VDEST%" /install /passive /norestart
del "%VDEST%" >nul 2>nul
exit /b 0

:check_venv
if /i not "%OLD%"=="%HERE%" goto :rebuild
.venv\Scripts\python.exe -c "import sys,struct;v=sys.version_info;sys.exit(0 if (3,11)<=(v[0],v[1])<=(3,12) and struct.calcsize('P')==8 else 1)" >nul 2>nul || goto :rebuild
exit /b 0
:rebuild
echo Old .venv is from another folder or uses an unsupported Python - recreating it...
rmdir /s /q .venv
exit /b 0
