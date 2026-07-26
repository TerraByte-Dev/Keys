# Packaging

How Keys becomes a Windows application you can hand to someone, and what is still
missing before it becomes one they can update from inside.

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python tools\build_exe.py
```

That produces `dist/Keys/` — about 66 MB, runnable, zippable. The script builds and
then **opens the result and runs it**, because every way this build can be broken is a
way PyInstaller reports success.

## The two roots

An installer that updates by replacing the application directory takes everything in
it. With `keys.db` living beside `keys.py`, a routine update would delete every
session you ever recorded — silently, on a schedule, discovered weeks later.

So `backend/config.py` resolves two roots:

| | Where | Contains |
|---|---|---|
| **Bundle** | `sys._MEIPASS`, i.e. `Keys/_internal/` | `frontend/`, shipped `presets/`, the SoundFont, the DLLs |
| **Data** | `%LOCALAPPDATA%\Keys` | `keys.db`, `config.local.json`, `recordings/`, presets and SoundFonts you added |

In a source checkout the two are the same directory and nothing changes — your database
sits next to `keys.py` exactly as before. They diverge only when frozen, which is the
only case where they must. `KEYS_DATA_DIR` overrides both.

Assets are searched **data first, bundle second**, so a SoundFont or preset you added
survives an update and shadows a shipped one of the same name. `tools/paths_check.py`
runs the frozen layouts in subprocesses with a fabricated `sys.frozen` and
`LOCALAPPDATA`, because the split is decided at import time.

## Why `--onedir`

1. **FluidSynth is LGPL.** The licence requires its libraries ship as replaceable
   shared objects — loose DLLs beside the executable. `--onefile` unpacks to a temp
   directory on every launch, which is not that.
2. `--onefile` re-extracts ~60 MB to `%TEMP%` at every start. On an app whose entire
   pitch is three milliseconds, a multi-second splash screen is the wrong trade.
3. Velopack updates a directory by swapping it, which is exactly the shape `--onedir`
   produces.

## What PyInstaller will not tell you

Every one of these builds cleanly and fails at runtime:

- **Hidden imports.** uvicorn selects its HTTP and websocket implementations by string
  at runtime. The import graph cannot see them, so they must be listed by hand.
- **The ctypes cliff.** `pyfluidsynth` is a pure ctypes binding — nothing in the import
  graph mentions `libfluidsynth-3.dll`. Omit it and you ship an app that starts, shows
  its UI, and is silent.
- **Over-eager excludes.** `numpy` is excluded on purpose: 25 MB, almost all OpenBLAS,
  imported by `pyfluidsynth` only inside `get_samples()` and `raw_audio_string()` —
  the two functions that pull rendered audio into Python, which this app can never call
  because *audio never passes through Python*. That is an architectural invariant, and
  the exclusion is safe exactly as long as it holds.

`tools/build_exe.py` therefore ends by launching the build with a throwaway
`KEYS_DATA_DIR`, playing a three-note chord, and asserting the voice count. **It makes
a sound** is the only check that distinguishes a working build from a silent one.

## Latency

Packaging cannot affect it — FluidSynth renders on a native thread Python is never in.
Two things could:

1. **An entry point that starts a thread before `import backend` runs
   `sys.setswitchinterval(0.0008)`.** Worth ~14 ms, and silent. `keys.py` imports
   `backend` first and says why; keep it that way.
2. **An embedded browser engine grabbing the audio endpoint** before exclusive mode is
   acquired. This is the argument against bundling a webview in v1.

Verified on the frozen build: 3.00 ms buffer, WASAPI exclusive, SoundFont loaded from
the bundle, chord sounded.

## What is done, and what is not

**Done.** A runnable application directory, the bundle/data split, a version in
`backend/version.py`, and a manual update check in **Settings → About** that asks the
GitHub releases API and links to the newest one.

The check is manual on purpose: never on launch, never on a timer, never in the
background. An app that quietly contacts a server every time you open it is not
local-first regardless of what its README says.

**Not done: the installer, and applying an update in place.**

Applying an update means replacing the application directory while it is running, which
is an installer's job. The intended tool is [Velopack](https://velopack.io) — it is
`electron-updater` for non-Electron apps and mirrors the manual check → download →
restart flow TerraPlayer already uses:

```bash
dotnet tool install -g vpk
vpk pack --packId Keys --packVersion 0.3.0 --packDir dist\Keys --mainExe Keys.exe
vpk upload github --repoUrl https://github.com/TerraByte-Dev/Keys --tag v0.3.0
```

Two things to settle before that is worth wiring:

- **Code signing.** Unsigned, SmartScreen shows "Windows protected your PC" to every
  first-time user. A certificate costs real money; the alternative is telling people to
  click through it, which is a bad habit to teach.
- **Velopack's Python support.** The apply-and-restart side has first-class bindings for
  C#, Rust and JS. Driving `Update.exe` from Python is possible but is the part that
  needs testing against a real published release, not a guess.

Half an updater that cannot be tested against a real release would be worse than an
honest link, which is what ships today.
