"""Paths, measured hardware constants, and persisted user settings.

Every number in HARDWARE was measured on this machine (see docs/HARDWARE.md).
They are defaults, not guesses -- but they are also editable at runtime, because the
whole point of `audio_check.py` is that hardware answers change.

User-tweakable state lives in `config.local.json` (gitignored). It is a shallow
overlay: anything absent falls back to the defaults here.

**Why there are two roots.** An installer that updates by replacing the application
directory would take `keys.db` with it -- your entire practice history, deleted by a
routine update, silently, on a schedule. So read-only assets that ship with the app
resolve against the bundle, and everything writable resolves against a data directory
that no updater ever touches.

In a source checkout the two are the same directory and nothing changes: your database
sits next to `keys.py` exactly as before. They only diverge when frozen, which is the
only situation where they must. Assets are looked up in the data directory *first* and
the bundle second, so a SoundFont or a preset you added yourself survives an update
and can shadow a shipped one; `KEYS_DATA_DIR` overrides the lot.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# PyInstaller sets both of these. _MEIPASS is where --add-data lands; under --onedir
# that is the app's _internal directory, which the updater replaces wholesale.
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT)).resolve()


def _default_data_dir() -> Path:
    if not FROZEN:
        # A checkout keeps its state in the checkout. Anything else would scatter a
        # developer's database somewhere they did not put it.
        return ROOT
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    return Path(base) / "Keys" if base else Path.home() / ".keys"


_override = os.environ.get("KEYS_DATA_DIR")
DATA_DIR = Path(_override).expanduser().resolve() if _override else _default_data_dir()

FRONTEND_DIR = BUNDLE / "frontend"          # shipped, read-only
RECORDING_DIR = DATA_DIR / "recordings"     # yours
DB_PATH = DATA_DIR / "keys.db"              # yours -- the one that must never be lost
SETTINGS_PATH = DATA_DIR / "config.local.json"

# Written here; read from here *and* the bundle. See asset_dirs().
SOUNDFONT_DIR = DATA_DIR / "soundfonts"
PRESET_DIR = DATA_DIR / "presets"

DEFAULT_SOUNDFONT = "GeneralUser-GS.sf2"


try:
    # SQLite will not create its own parent, and neither will Settings. Doing it once
    # here beats a mkdir in front of every writer.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:  # pragma: no cover -- a read-only data dir is the caller's problem
    pass


def asset_dirs(name: str) -> list[Path]:
    """Where to look for a kind of asset, most-specific first.

    Yours before the app's, so replacing a shipped preset is a matter of saving one
    with the same name rather than editing inside the installed application.
    """
    dirs = [DATA_DIR / name]
    if BUNDLE != DATA_DIR:
        dirs.append(BUNDLE / name)
    return [d for d in dirs if d.exists()]


def find_asset(name: str, filename: str) -> Path | None:
    """First `filename` found across asset_dirs(name), or None."""
    for d in asset_dirs(name):
        candidate = d / filename
        if candidate.exists():
            return candidate
    return None


def list_assets(name: str, pattern: str) -> list[Path]:
    """Every matching asset across the search path, deduplicated by file name."""
    seen: dict[str, Path] = {}
    for d in asset_dirs(name):
        for p in sorted(d.glob(pattern)):
            seen.setdefault(p.name, p)      # first directory wins
    return [seen[k] for k in sorted(seen)]

# --- The piano ---------------------------------------------------------------
# Yamaha P-71B: 88 keys, A0 (21) through C8 (108). Hardcoded on purpose -- this is
# the only keyboard this app targets and every measurement here is device-specific.
LOW_KEY = 21
HIGH_KEY = 108

# --- Audio (measured; see docs/HARDWARE.md section 3) --------------------------
HARDWARE: dict[str, Any] = {
    # float, NOT int. Synth.setting() dispatches on Python type; an int routes to
    # fluid_settings_setint and fails silently.
    "sample_rate": 48000.0,
    # This machine's exclusive-mode floor. 128 is refused ("minimum period is 144").
    # Raise to 256 if you hear crackling; audio.periods does nothing in exclusive mode.
    # Only consulted in exclusive mode -- Windows owns the period in shared mode.
    "period_size": 144,
    # OFF by default, and this is a product decision rather than a technical one.
    #
    # Exclusive mode is what buys the 3.00 ms, and it takes the output device away from
    # every other application on the machine for as long as Keys is open. Not "turns
    # them down" -- Spotify goes silent, Discord goes silent, a browser reports an
    # audio rendering error. A practice app you leave open for an hour cannot also be
    # an application that breaks the computer's sound, and 7 ms is not worth that.
    #
    # Shared mode is roughly 10 ms, which is inside the range a piano action already
    # spans between a soft and a hard keystroke. Exclusive is one click away in
    # Settings -> Audio for when you want the tightest possible feel, and pinning Keys
    # to an output Windows is not using gets you 3 ms without taking anything.
    "exclusive": False,
    # A WASAPI endpoint name, or "default". Pinning Keys to an output Windows is not
    # using for anything else is how you get low latency AND keep Discord.
    "device": "default",
    "polyphony": 256,
    "gain": 0.6,
}

# --- Defaults the UI can change ----------------------------------------------
DEFAULTS: dict[str, Any] = {
    "audio": dict(HARDWARE),
    "midi_port": None,           # None = first available port
    "preset": "grand-piano",     # presets/<id>.json loaded at boot
    "idle_seconds": 12,          # practice clock pauses after this long with no note
    "metronome": {
        "bpm": 80,
        "beats_per_bar": 4,
        "subdivision": 1,        # clicks per beat: 1, 2, 3 or 4
        "kit": 0,                # bank-128 program
        "beat_note": 37,         # side stick
        "accent_note": 56,       # cowbell -- downbeat
        "sub_note": 42,          # closed hi-hat -- subdivisions
        "beat_velocity": 92,
        "accent_velocity": 118,
        "sub_velocity": 55,
        "ramp_enabled": False,
        "ramp_bars": 8,
        "ramp_bpm_step": 4,
        "ramp_bpm_max": 160,
    },
    # The loop station. Tempo and meter deliberately absent -- those live in
    # "metronome", because a loop with its own tempo field is a second clock.
    "loop": {"bars": 4, "click": True, "count_in_bars": 1},
    # The only feature in the app that reaches the network, and only when you open a
    # track. Empty until you paste a link.
    "backing": {"tracks": []},
    "reverb": {"room": 0.3, "damping": 0.4, "width": 6.0, "level": 0.55},
    "chorus": {"nr": 3, "level": 1.2, "speed": 0.4, "depth": 6.0, "type": 0},
    # tour_seen lives here rather than in localStorage because localStorage is
    # per-browser-profile and dies to "clear browsing data", which would resurrect the
    # first-run tour on a machine that has been played for months.
    "ui": {"key_signature": "C", "theme": "dark", "show_note_names": True,
           "tour_seen": False},
    "sightread": {
        "clef": "both",
        "key": "C",
        "low": 55,               # G3
        "high": 79,              # G5
        "notes_per_measure": 4,
        "adaptive": True,
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Thread-safe settings overlay persisted to config.local.json."""

    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        if path.exists():
            try:
                self._data = _deep_merge(DEFAULTS, json.loads(path.read_text("utf-8")))
            except (json.JSONDecodeError, OSError):
                # A corrupt local file must never stop the piano making sound.
                pass

    def all(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))  # deep copy, JSON-safe by construction

    def get(self, *path: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            snapshot = json.loads(json.dumps(self._data))
        self._save(snapshot)
        return snapshot

    def _save(self, snapshot: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(snapshot, indent=2), "utf-8")
        except OSError:
            pass


settings = Settings()
