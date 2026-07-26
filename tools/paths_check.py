"""Regression test for the bundle/data split -- the thing that stops an update
deleting your practice history.

    .venv\\Scripts\\python.exe tools\\paths_check.py

An installer that updates by replacing the application directory takes everything in
it. If `keys.db` lives there, a routine update destroys every session you ever
recorded, silently, and you find out weeks later. This suite exists because that
failure is invisible until it is irreversible.

It runs the interesting cases in **subprocesses with a fabricated environment**, since
the split is decided at import time from `sys.frozen` and `KEYS_DATA_DIR`. Asserting
against the already-imported module would only ever test the developer's own layout,
which is the one arrangement where the bug cannot happen.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def probe(env_extra: dict[str, str], frozen: bool = False, meipass: str = "") -> dict:
    """Import backend.config in a fresh interpreter and report where it points."""
    code = (
        "import sys\n"
        + (f"sys.frozen = True\nsys._MEIPASS = r'{meipass}'\n" if frozen else "")
        + f"sys.path.insert(0, r'{ROOT}')\n"
        "from backend import config\n"
        "import json; print(json.dumps({\n"
        "  'data': str(config.DATA_DIR), 'bundle': str(config.BUNDLE),\n"
        "  'db': str(config.DB_PATH), 'settings': str(config.SETTINGS_PATH),\n"
        "  'recordings': str(config.RECORDING_DIR), 'frontend': str(config.FRONTEND_DIR),\n"
        "  'presets_write': str(config.PRESET_DIR), 'sf_write': str(config.SOUNDFONT_DIR),\n"
        "  'sf_dirs': [str(d) for d in config.asset_dirs('soundfonts')],\n"
        "  'frozen': config.FROZEN,\n"
        "}))\n"
    )
    env = {**os.environ, **env_extra}
    env.pop("KEYS_DATA_DIR", None)
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


print("1. a source checkout behaves exactly as it always did")
src = probe({})
step("not frozen", src["frozen"] is False)
step("data dir is the checkout", Path(src["data"]) == ROOT, src["data"])
step("keys.db sits next to keys.py", Path(src["db"]) == ROOT / "keys.db")
step("settings next to it too", Path(src["settings"]) == ROOT / "config.local.json")
step("one asset dir, not two", len(src["sf_dirs"]) == 1,
     "bundle == data, so the search path does not double up")

print("2. frozen: writable state leaves the application directory")
appdir = Path(tempfile.mkdtemp(prefix="keys-app-"))
local = Path(tempfile.mkdtemp(prefix="keys-local-"))
(appdir / "soundfonts").mkdir()
(appdir / "presets").mkdir()
(appdir / "frontend").mkdir()
# Both sides of the search path must exist for section 3 to mean anything -- asset_dirs
# drops directories that are not there, so testing against only the bundle would assert
# that a one-element list has at least one element.
(local / "Keys" / "soundfonts").mkdir(parents=True)
frozen = probe({"LOCALAPPDATA": str(local)}, frozen=True, meipass=str(appdir))
step("frozen detected", frozen["frozen"] is True)
step("bundle is the app dir", Path(frozen["bundle"]) == appdir, frozen["bundle"])
step("data dir is %LOCALAPPDATA%\\Keys", Path(frozen["data"]) == local / "Keys",
     frozen["data"])

# The whole point of the exercise, stated as bluntly as it deserves.
step("THE DATABASE IS NOT IN THE APP DIRECTORY",
     appdir not in Path(frozen["db"]).parents and Path(frozen["db"]).parent == local / "Keys",
     frozen["db"])
step("nor are the settings", Path(frozen["settings"]).parent == local / "Keys")
step("nor the recordings", Path(frozen["recordings"]).parent == local / "Keys")
step("nor presets you save", Path(frozen["presets_write"]).parent == local / "Keys")
step("nor soundfonts you add", Path(frozen["sf_write"]).parent == local / "Keys")
step("the frontend IS in the app directory",
     Path(frozen["frontend"]) == appdir / "frontend",
     "shipped and read-only, so it should be replaced by an update")

print("3. frozen: assets are found in both places, yours first")
step("both directories are on the path", len(frozen["sf_dirs"]) == 2, str(frozen["sf_dirs"]))
step("yours is searched first",
     frozen["sf_dirs"][0] == str(local / "Keys" / "soundfonts"),
     "a SoundFont you added survives an update and shadows a shipped one")
step("the bundle's is searched second",
     frozen["sf_dirs"][1] == str(appdir / "soundfonts"))

print("4. KEYS_DATA_DIR overrides everything")
elsewhere = Path(tempfile.mkdtemp(prefix="keys-elsewhere-"))
over = probe({"KEYS_DATA_DIR": str(elsewhere)})
step("data dir honoured", Path(over["data"]) == elsewhere.resolve(), over["data"])
step("db follows it", Path(over["db"]) == elsewhere.resolve() / "keys.db")
over_frozen = probe({"KEYS_DATA_DIR": str(elsewhere), "LOCALAPPDATA": str(local)},
                    frozen=True, meipass=str(appdir))
step("beats LOCALAPPDATA when frozen too", Path(over_frozen["data"]) == elsewhere.resolve())
step("but does not move the bundle", Path(over_frozen["bundle"]) == appdir,
     "shipped assets still ship")

print("5. the search path, live")
work = Path(tempfile.mkdtemp(prefix="keys-search-"))
(work / "presets").mkdir(parents=True)
(work / "presets" / "mine.json").write_text("{}", "utf-8")
(work / "presets" / "grand-piano.json").write_text('{"id":"shadowed"}', "utf-8")
code = (
    f"import sys; sys.frozen=True; sys._MEIPASS=r'{ROOT}'\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "from backend import config\n"
    "import json; print(json.dumps({\n"
    "  'names': [p.name for p in config.list_assets('presets', '*.json')],\n"
    "  'mine': str(config.find_asset('presets', 'mine.json') or ''),\n"
    "  'shadow': str(config.find_asset('presets', 'grand-piano.json') or ''),\n"
    "  'missing': config.find_asset('presets', 'nope.json') is None,\n"
    "}))\n"
)
env = {**os.environ, "KEYS_DATA_DIR": str(work)}
res = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, env=env, check=True).stdout.strip().splitlines()[-1])
step("both directories contribute", "mine.json" in res["names"]
     and any(n not in ("mine.json", "grand-piano.json") for n in res["names"]),
     f"{len(res['names'])} presets from two directories")
step("names are deduplicated", len(res["names"]) == len(set(res["names"])))
step("yours shadows the shipped one",
     Path(res["shadow"]).parent == work / "presets",
     "same file name, your copy wins")
step("a missing asset is None, not an exception", res["missing"])

print("6. the data directory is created, so SQLite does not have to")
fresh = Path(tempfile.mkdtemp(prefix="keys-fresh-")) / "does" / "not" / "exist"
made = probe({"KEYS_DATA_DIR": str(fresh)})
step("created on import", Path(made["data"]).exists(), made["data"])

for path in (appdir, local, elsewhere, work, fresh.parents[2]):
    shutil.rmtree(path, ignore_errors=True)

print("7. today's real layout is intact")
step("app data dir exists", config.DATA_DIR.exists(), str(config.DATA_DIR))
step("the shipped soundfont resolves",
     config.find_asset("soundfonts", config.DEFAULT_SOUNDFONT) is not None,
     str(config.find_asset("soundfonts", config.DEFAULT_SOUNDFONT) or "MISSING"))
step("presets resolve", len(config.list_assets("presets", "*.json")) >= 8,
     f"{len(config.list_assets('presets', '*.json'))} presets")
step("the frontend resolves", (config.FRONTEND_DIR / "app.js").exists())

print()
print("ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
