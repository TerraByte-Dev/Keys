"""Paths, measured hardware constants, and persisted user settings.

Every number in HARDWARE was measured on this machine (see docs/FEASIBILITY.md).
They are defaults, not guesses -- but they are also editable at runtime, because the
whole point of `audio_check.py` is that hardware answers change.

User-tweakable state lives in `config.local.json` (gitignored). It is a shallow
overlay: anything absent falls back to the defaults here.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

SOUNDFONT_DIR = ROOT / "soundfonts"
PRESET_DIR = ROOT / "presets"
FRONTEND_DIR = ROOT / "frontend"
RECORDING_DIR = ROOT / "recordings"
DB_PATH = ROOT / "keys.db"
SETTINGS_PATH = ROOT / "config.local.json"

DEFAULT_SOUNDFONT = "GeneralUser-GS.sf2"

# --- The piano ---------------------------------------------------------------
# Yamaha P-71B: 88 keys, A0 (21) through C8 (108). Hardcoded on purpose -- this is
# the only keyboard this app targets and every measurement here is device-specific.
LOW_KEY = 21
HIGH_KEY = 108

# --- Audio (measured; see FEASIBILITY.md section 3) --------------------------
HARDWARE: dict[str, Any] = {
    # float, NOT int. Synth.setting() dispatches on Python type; an int routes to
    # fluid_settings_setint and fails silently.
    "sample_rate": 48000.0,
    # This machine's exclusive-mode floor. 128 is refused ("minimum period is 144").
    # Raise to 256 if you hear crackling; audio.periods does nothing in exclusive mode.
    "period_size": 144,
    # Exclusive mode is what buys the 3 ms. It also takes the output device away from
    # every other app on the machine for as long as Keys runs. Switchable at runtime.
    "exclusive": True,
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
    "reverb": {"room": 0.3, "damping": 0.4, "width": 6.0, "level": 0.55},
    "chorus": {"nr": 3, "level": 1.2, "speed": 0.4, "depth": 6.0, "type": 0},
    "ui": {"key_signature": "C", "theme": "dark", "show_note_names": True},
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
            self._path.write_text(json.dumps(snapshot, indent=2), "utf-8")
        except OSError:
            pass


settings = Settings()
