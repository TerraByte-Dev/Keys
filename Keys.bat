@echo off
REM Keys -- double-click launcher.
REM
REM Exists so nobody has to remember a virtualenv path. It checks the three things that
REM actually go wrong on a fresh machine and says which one it is, instead of flashing a
REM traceback and closing.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   No virtual environment found.
    echo.
    echo   Run this once from a terminal in this folder:
    echo.
    echo       python -m venv .venv
    echo       .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "soundfonts\GeneralUser-GS.sf2" (
    echo.
    echo   No SoundFont found at soundfonts\GeneralUser-GS.sf2
    echo.
    echo   Download GeneralUser GS 2.0.3 and save it there:
    echo       https://github.com/mrbumpy409/GeneralUser-GS
    echo.
    pause
    exit /b 1
)

REM FluidSynth is found through PATH by ctypes, and only through PATH -- see
REM backend\__init__.py. KEYS_FLUIDSYNTH_BIN overrides the default location.
if "%KEYS_FLUIDSYNTH_BIN%"=="" set "KEYS_FLUIDSYNTH_BIN=C:\tools\fluidsynth\bin"
if not exist "%KEYS_FLUIDSYNTH_BIN%\libfluidsynth-3.dll" (
    echo.
    echo   FluidSynth not found at: %KEYS_FLUIDSYNTH_BIN%
    echo.
    echo   Download the -cpp11 zip from
    echo       https://github.com/FluidSynth/fluidsynth/releases
    echo   extract it, and either put it at C:\tools\fluidsynth
    echo   or set KEYS_FLUIDSYNTH_BIN to its bin folder.
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe keys.py %*
if errorlevel 1 pause
