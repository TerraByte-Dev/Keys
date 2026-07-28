# PyInstaller spec for Keys. Build with tools/build_exe.py, not directly -- that script
# checks the things that fail silently here.
#
# --onedir, not --onefile, for three reasons and only the first is convenience:
#
#   1. FluidSynth is LGPL. The licence requires its libraries ship as replaceable
#      shared objects, which means loose DLLs next to the executable. --onefile
#      unpacks to a temp directory on every launch, which is not that.
#   2. --onefile re-extracts ~60 MB to %TEMP% at every start. On an app whose entire
#      pitch is three milliseconds, a multi-second splash screen is the wrong trade.
#   3. Velopack updates a directory by swapping it. That is exactly the shape of
#      --onedir, and exactly why backend/config.py keeps your database somewhere else.
#
# The SoundFont is bundled on purpose: 31 MB against an app that is useless without it,
# and "download this separately" is the step people bounce off.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
FLUIDSYNTH_BIN = Path(os.environ.get("KEYS_FLUIDSYNTH_BIN", r"C:\tools\fluidsynth\bin"))

binaries = [(str(p), ".") for p in FLUIDSYNTH_BIN.glob("*.dll")]

datas = [
    (str(ROOT / "frontend"), "frontend"),
    (str(ROOT / "presets"), "presets"),
    (str(ROOT / "soundfonts" / "GeneralUser-GS.sf2"), "soundfonts"),
    (str(ROOT / "soundfonts" / "README.md"), "soundfonts"),
    (str(ROOT / "LICENSE"), "."),
]

a = Analysis(
    [str(ROOT / "keys.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    # fluidsynth is a pure-ctypes binding, so PyInstaller's import graph never sees the
    # DLL; rtmidi's backend and uvicorn's protocol implementations are all selected at
    # runtime by string, which the graph cannot follow either.
    hiddenimports=[
        "fluidsynth",
        "rtmidi",
        # pywebview picks its backend at runtime by platform string, so the import
        # graph never reaches winforms. Without this the frozen app falls back to the
        # browser and looks like the window feature was never built.
        "webview",
        "webview.platforms.winforms",
        "clr",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Nothing in the shipped app opens a window or draws a plot; leaving these in adds
    # tens of megabytes of DLL for code that is never reached.
    #
    # numpy is the big one: 25 MB, almost all of it OpenBLAS. pyfluidsynth imports it
    # *inside* get_samples() and raw_audio_string(), the two functions that pull rendered
    # audio into Python -- which this app can never call, because audio never passes
    # through Python at all. That is an architectural invariant, not a coincidence, so
    # the exclusion is safe for as long as the invariant holds. tools/build_exe.py runs
    # the built app and plays a chord, which is what would catch it if it ever stops.
    excludes=["numpy", "tkinter", "matplotlib", "PIL", "playwright",
              "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Keys",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX-packed DLLs trip antivirus heuristics for no real gain
    # Windowed. Keys opens its own window now, and a console flashing up beside it is
    # the difference between an application and a script someone wrapped. The cost is
    # that sys.stdout is None in this build, which would kill the startup banner before
    # the window appeared -- keys.py's attach_output() is what makes print() safe, and
    # what gives --dev a log file in the data directory instead of a terminal.
    console=False,
    disable_windowed_traceback=False,
    # Seven sizes from 16 to 256 -- Explorer and the taskbar reach for the 16,
    # and letting Windows downscale a 256 instead is how an icon becomes a smudge.
    icon=str(ROOT / 'packaging' / 'keys.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Keys",
)
