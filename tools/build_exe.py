"""Build the Windows application directory, and check the things that fail silently.

    .venv\\Scripts\\python.exe tools\\build_exe.py            # build
    .venv\\Scripts\\python.exe tools\\build_exe.py --verify   # verify an existing build

Produces `dist/Keys/`, a directory you can zip, hand to someone, and have work. It is
not an installer -- Velopack turns this directory into one, and its whole model is
"replace the directory", which is why backend/config.py keeps your database elsewhere.

PyInstaller reports success in every one of the ways this build can be broken:

* A missing hidden import is a traceback at *launch*, not at build. uvicorn picks its
  HTTP and websocket implementations by string at runtime, so the import graph cannot
  see them and neither can PyInstaller.
* pyfluidsynth is a pure-ctypes binding. Nothing in the import graph references
  libfluidsynth-3.dll, so leaving it out builds perfectly and produces an application
  that starts and makes no sound.
* A missing SoundFont is a clean exit with a message, which reads like a bug report
  rather than a packaging mistake.

So this script builds, then opens the result and asserts against it.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "keys.spec"
DIST = ROOT / "dist" / "Keys"
# Not 8770: the whole point is to test the build, and colliding with the app you have
# open would either fail confusingly or -- worse -- test the app instead of the build.
SMOKE_PORT = 8791

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def human(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"


def build() -> bool:
    print("1. building (a few minutes)")
    for path in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(path, ignore_errors=True)
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
         str(SPEC)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        step("PyInstaller exited cleanly", False, f"exit {result.returncode}")
        return False
    step("PyInstaller exited cleanly", True)
    return True


def verify() -> None:
    print("2. the directory contains what the app reaches for at runtime")
    step("dist/Keys exists", DIST.exists(), str(DIST))
    if not DIST.exists():
        return
    exe = DIST / "Keys.exe"
    step("Keys.exe", exe.exists(), human(exe.stat().st_size) if exe.exists() else "")

    internal = DIST / "_internal"
    root = internal if internal.exists() else DIST
    step("_internal layout", internal.exists(), "PyInstaller 6 puts data under _internal")

    # The ctypes cliff: nothing in the import graph mentions this file, so leaving it
    # out builds perfectly and ships an app that starts and is silent.
    dll = root / "libfluidsynth-3.dll"
    step("libfluidsynth-3.dll shipped", dll.exists(),
         human(dll.stat().st_size) if dll.exists() else "the build would be silent")
    step("shipped as a loose file, not packed",
         dll.exists() and dll.parent.is_dir(),
         "FluidSynth is LGPL -- its libraries must stay replaceable")
    step("sndfile.dll shipped", (root / "sndfile.dll").exists(),
         "SoundFont loading needs it")

    sf = root / "soundfonts" / "GeneralUser-GS.sf2"
    step("SoundFont shipped", sf.exists(),
         human(sf.stat().st_size) if sf.exists() else "the app exits at startup without it")
    step("frontend shipped", (root / "frontend" / "app.js").exists())
    step("stylesheet shipped", (root / "frontend" / "style.css").exists())
    presets = list((root / "presets").glob("*.json")) if (root / "presets").exists() else []
    step("presets shipped", len(presets) >= 8, f"{len(presets)} presets")
    step("licence shipped", (root / "LICENSE").exists() or (DIST / "LICENSE").exists())

    print("3. nothing writable was baked into the application directory")
    # If any of these shipped, the update-replaces-the-directory model would either
    # destroy them or resurrect a stale copy over the user's own.
    for name in ("keys.db", "config.local.json"):
        found = list(DIST.rglob(name))
        step(f"no {name} in the build", not found,
             str(found[0]) if found else "your data lives in %LOCALAPPDATA%\\Keys")
    step("no recordings/ in the build", not list(DIST.rglob("recordings")))

    print("4. size")
    total = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    step("under 200 MB", total < 200 * 1048576, human(total))
    biggest = sorted((p for p in DIST.rglob("*") if p.is_file()),
                     key=lambda p: p.stat().st_size, reverse=True)[:5]
    for p in biggest:
        print(f"         {human(p.stat().st_size):>9}  {p.relative_to(DIST)}")


def get(port: int, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read())


def smoke() -> None:
    """Launch the built app and make it play a chord.

    Every check above this one passes on a build that starts and is silent -- a missing
    hidden import, an unresolvable DLL and an over-eager exclude all look identical to
    a static inspection. The only honest test is to run it.
    """
    print("5. the built app actually runs")
    exe = DIST / "Keys.exe"
    if not exe.exists():
        step("Keys.exe present to run", False)
        return

    # A throwaway data dir, so the smoke test never touches the practice history --
    # the same rule every other check in this project follows.
    data = Path(tempfile.mkdtemp(prefix="keys-smoke-"))
    env = {**os.environ, "KEYS_DATA_DIR": str(data)}
    proc = subprocess.Popen(
        [str(exe), "--no-browser", "--port", str(SMOKE_PORT)],
        cwd=str(DIST), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        state = None
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                state = get(SMOKE_PORT, "/api/state")
                break
            except (urllib.error.URLError, socket.timeout, ConnectionError):
                time.sleep(0.5)

        if state is None:
            out = (proc.stdout.read() if proc.stdout else "")[-2500:]
            step("app answered on its port", False, f"exit={proc.poll()}")
            print(out)
            return
        step("app answered on its port", True, f"port {SMOKE_PORT}")

        eng = state.get("engine", {})
        step("audio engine started", bool(eng.get("started")),
             f"{eng.get('buffer_ms')} ms, exclusive={eng.get('exclusive')}")
        step("SoundFont loaded from the bundle",
             any(sf.get("loaded") for sf in state.get("soundfonts", [])),
             str([sf.get("file") for sf in state.get("soundfonts", [])]))
        step("presets loaded from the bundle", len(state.get("presets", [])) >= 8,
             f"{len(state.get('presets', []))}")

        # The one that catches a silent build: sound, not settings.
        get(SMOKE_PORT, "/api/preview", {"notes": [60, 64, 67], "velocity": 90, "ms": 1500})
        time.sleep(0.6)
        voices = get(SMOKE_PORT, "/api/state").get("engine", {}).get("voices", 0)
        step("IT MAKES A SOUND", voices >= 3, f"{voices} voices from a three-note chord")

        step("frontend served from the bundle",
             len(urllib.request.urlopen(
                 f"http://127.0.0.1:{SMOKE_PORT}/app.js", timeout=10).read()) > 1000)

        step("wrote its data to KEYS_DATA_DIR, not into the build",
             (data / "keys.db").exists() and not list(DIST.rglob("keys.db")),
             str(data))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
        shutil.rmtree(data, ignore_errors=True)


def main() -> int:
    if "--verify" not in sys.argv and not build():
        return 1
    verify()
    if "--no-smoke" not in sys.argv:
        smoke()
    print()
    if ok:
        print("  BUILD GOOD")
        print(f"  Run it:  {DIST / 'Keys.exe'}")
        print("  Its data will land in %LOCALAPPDATA%\\Keys, not in the build.")
        print()
        print("  This is still a directory, not an installer. Velopack turns it into")
        print("  one; see docs/PACKAGING.md.")
    else:
        print("  FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
