"""FluidSynth: the only thing in this app that makes sound.

Design rules that are not negotiable (see CLAUDE.md):

* ``note_on`` / ``note_off`` / ``control`` / ``bend`` are the **hot path**. They are
  called from rtmidi's callback thread. No I/O, no logging, no locks, no dict
  building. Everything they need is precomputed.
* Zone changes never mutate the routing table in place. A whole new immutable table
  is built and then **rebound in one atomic assignment**, so the callback thread
  either sees the old table or the new one and never a half-written one. That is
  why there is no lock anywhere near the hot path.
* Which channels a held note was routed to is remembered at note-on, so changing
  zones while a key is down still releases the right voices.

Audio settings live in ``config.HARDWARE`` and every one of them was measured.
The obvious-looking alternatives are silently wrong -- read docs/FEASIBILITY.md
before "fixing" any of them.
"""

from __future__ import annotations

import ctypes
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import config  # noqa: F401  (sets PATH + switch interval via backend/__init__)

import fluidsynth  # noqa: E402  -- must come after the package import above

DRUM_BANK = 128
GM_CHANNELS = 16


# --- output device enumeration ----------------------------------------------
# FluidSynth publishes the valid values for a string setting as an option list, which
# is the only trustworthy source for these names -- they are Windows endpoint strings
# like "Speakers (Realtek(R) Audio)" and guessing them gets you silence, not an error.
_OPTION_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)


def list_audio_devices() -> list[str]:
    """Every WASAPI output FluidSynth will accept, plus "default"."""
    try:
        lib = ctypes.CDLL(fluidsynth.find_libfluidsynth())
        lib.fluid_settings_foreach_option.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, _OPTION_CB,
        ]
        settings = fluidsynth.new_fluid_settings()
    except Exception:  # noqa: BLE001
        return ["default"]
    found: list[str] = []

    def collect(_data, _name, option) -> None:
        if option:
            found.append(option.decode("utf-8", "replace"))

    try:
        fluidsynth.fluid_settings_setstr(settings, b"audio.driver", b"wasapi")
        lib.fluid_settings_foreach_option(settings, b"audio.wasapi.device", None,
                                          _OPTION_CB(collect))
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            fluidsynth.delete_fluid_settings(settings)
        except Exception:  # noqa: BLE001
            pass
    if "default" not in found:
        found.append("default")
    return sorted(found, key=lambda s: (s != "default", s.lower()))


# --- velocity curves ---------------------------------------------------------
# Precomputed 128-entry lookup tables. The hot path does `curve[vel]` and nothing
# else. Index 0 stays 0 because velocity 0 means note-off on the wire.
def _build_curve(fn) -> tuple[int, ...]:
    return (0,) + tuple(max(1, min(127, int(round(fn(v))))) for v in range(1, 128))


CURVES: dict[str, tuple[int, ...]] = {
    "linear": _build_curve(lambda v: v),
    # exponents < 1 lift quiet notes -> easier to sound loud, less dynamic range
    "soft": _build_curve(lambda v: 127 * (v / 127) ** 0.65),
    "softer": _build_curve(lambda v: 127 * (v / 127) ** 0.45),
    # exponents > 1 demand a harder strike for the same loudness
    "hard": _build_curve(lambda v: 127 * (v / 127) ** 1.45),
    "harder": _build_curve(lambda v: 127 * (v / 127) ** 2.10),
    # squeeze everything into a mezzo band -- useful for pads and organ
    "compress": _build_curve(lambda v: 45 + (v / 127) * 70),
}
_FIXED_CACHE: dict[int, tuple[int, ...]] = {}


def curve_for(name: str, fixed_velocity: int = 100) -> tuple[int, ...]:
    if name == "fixed":
        v = max(1, min(127, int(fixed_velocity)))
        if v not in _FIXED_CACHE:
            _FIXED_CACHE[v] = (0,) + (v,) * 127
        return _FIXED_CACHE[v]
    return CURVES.get(name, CURVES["linear"])


CURVE_NAMES = list(CURVES) + ["fixed"]


# --- zones -------------------------------------------------------------------
@dataclass
class Zone:
    """One key range routed to one synth channel.

    Overlap *is* the layering mechanism: two zones covering the same key both fire.
    Each zone owns its own channel, because gain/pan/reverb are per-channel MIDI
    controllers -- two zones sharing a channel would fight over them.
    """

    id: str = "main"
    name: str = ""
    lo: int = config.LOW_KEY
    hi: int = config.HIGH_KEY
    channel: int = 0
    soundfont: str = config.DEFAULT_SOUNDFONT
    bank: int = 0
    program: int = 0
    transpose: int = 0
    gain: float = 1.0
    pan: float = 0.5
    reverb: float = 0.30
    chorus: float = 0.0
    curve: str = "linear"
    fixed_velocity: int = 100
    enabled: bool = True

    @property
    def is_drums(self) -> bool:
        return self.bank == DRUM_BANK

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_drums"] = self.is_drums
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Zone":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        clean = {k: v for k, v in d.items() if k in known}
        z = cls(**clean)
        z.lo, z.hi = int(z.lo), int(z.hi)
        if z.lo > z.hi:
            z.lo, z.hi = z.hi, z.lo
        z.channel = max(0, min(GM_CHANNELS - 1, int(z.channel)))
        z.transpose = max(-48, min(48, int(z.transpose)))
        z.gain = max(0.0, min(1.0, float(z.gain)))
        z.pan = max(0.0, min(1.0, float(z.pan)))
        z.reverb = max(0.0, min(1.0, float(z.reverb)))
        z.chorus = max(0.0, min(1.0, float(z.chorus)))
        return z


@dataclass
class Preset:
    """A named set of zones. The simple instrument picker is just a one-zone preset."""

    id: str
    name: str
    zones: list[Zone] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "zones": [z.to_dict() for z in self.zones],
        }


EMPTY_ROUTES: tuple[tuple, ...] = tuple(() for _ in range(128))


class Engine:
    """Owns the Synth. One instance per process."""

    def __init__(self, settings: config.Settings | None = None) -> None:
        self.settings = settings or config.settings
        self.fs: fluidsynth.Synth | None = None
        self.sequencer: fluidsynth.Sequencer | None = None
        self.seq_dest: int | None = None

        # --- hot-path state ---------------------------------------------------
        # routes[note] -> tuple of (channel, out_note, curve) triples.
        # Rebound wholesale on zone change; never mutated in place.
        self.routes: tuple[tuple, ...] = EMPTY_ROUTES
        # What each held key was routed to, so a mid-hold zone change still releases.
        self._held: list[tuple | None] = [None] * 128
        # Channels with an enabled zone -- CC and pitch bend broadcast to these.
        self.active_channels: tuple[int, ...] = (0,)

        # --- cold state (server thread only) ----------------------------------
        self._lock = threading.Lock()
        self._sfids: dict[str, int] = {}
        self._preset_cache: dict[str, list[dict[str, Any]]] = {}
        self.zones: list[Zone] = []
        self.preset_id: str = ""
        self.preset_name: str = ""
        self.started = False
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        if self.started:
            return
        audio = self.settings.get("audio", default=config.HARDWARE) or config.HARDWARE
        rate = float(audio.get("sample_rate", 48000.0))
        period = int(audio.get("period_size", 144))

        fs = fluidsynth.Synth(samplerate=rate)
        fs.setting("audio.driver", "wasapi")
        # NOT "audio.wasapi.exclusive" -- the wrong name is silently ignored.
        #
        # Exclusive mode is why this app hits 3 ms, and also why nothing else can play
        # through the same output while it runs -- Discord, a browser, Spotify all go
        # silent. That is WASAPI working as designed, not a conflict. Shared mode gives
        # the device back at the cost of Windows choosing the buffer (~10 ms), and
        # pinning to a device Windows is not using for anything else gets you both.
        fs.setting("audio.wasapi.exclusive-mode", 1 if audio.get("exclusive", True) else 0)
        device = str(audio.get("device", "default") or "default")
        if device != "default":
            fs.setting("audio.wasapi.device", device)
        # Sole latency knob in exclusive mode; ignored entirely in shared mode, where
        # the Windows engine period decides and audio.periods is what matters.
        fs.setting("audio.period-size", period)
        # Must be a float. An int routes to fluid_settings_setint and fails silently.
        fs.setting("synth.sample-rate", rate)
        fs.setting("synth.polyphony", int(audio.get("polyphony", 256)))
        fs.setting("synth.gain", float(audio.get("gain", 0.6)))
        # start() unconditionally builds a MIDI driver (fluidsynth.py:834). Left alone
        # it opens the same winmidi port our rtmidi callback owns and every note sounds
        # TWICE. Pointing it at a device that cannot exist is the fix; the resulting
        # 'Device "__none__" does not exists' line on the console is intentional.
        fs.setting("midi.driver", "winmidi")
        fs.setting("midi.winmidi.device", "__none__")

        self.fs = fs
        self.load_soundfont(config.DEFAULT_SOUNDFONT)
        fs.start()

        self.apply_reverb(self.settings.get("reverb", default={}) or {})
        self.apply_chorus(self.settings.get("chorus", default={}) or {})

        # use_system_timer=False + register_fluidsynth means the *audio render thread*
        # advances the sequencer clock. Verified on this machine: self-advancing,
        # drift-free, and the same clock the sound comes out of. The system-timer mode
        # is deprecated in FluidSynth 2.x and did not deliver callbacks here at all.
        self.sequencer = fluidsynth.Sequencer(time_scale=1000, use_system_timer=False)
        self.seq_dest = self.sequencer.register_fluidsynth(fs)

        self.started = True

    def _suspend_hot_path(self) -> None:
        """Make the MIDI callback a no-op before the Synth is torn down.

        The callback thread is still live during a restart and reaches self.fs on every
        note. Emptying the routing table and the channel list first means it returns at
        its very first branch instead of calling into a freed Synth. Order matters:
        these three assignments are each atomic, and all of them happen before delete().
        """
        self.routes = EMPTY_ROUTES
        self.active_channels = ()
        for i in range(128):
            self._held[i] = None

    def restart(self, audio_patch: dict[str, Any] | None = None) -> list[str]:
        """Rebuild the Synth on new audio settings, keeping zones and presets.

        Sample rate, buffer size, exclusive mode and output device are all negotiated
        when the WASAPI stream opens, so none of them can be changed on a live stream --
        the device has to be closed and reopened. Everything above the Synth (zones,
        soundfonts, the loaded preset) is restored afterwards.
        """
        zones, preset_id, preset_name = list(self.zones), self.preset_id, self.preset_name
        if audio_patch:
            merged = dict(self.settings.get("audio", default=config.HARDWARE) or {})
            merged.update(audio_patch)
            self.settings.update({"audio": merged})

        self.stop()
        self._sfids.clear()          # sfids belong to the Synth that just went away
        self._preset_cache.clear()
        self.started = False
        self.start()
        if not self.started:
            return ["audio engine did not restart"]
        warnings = self.set_zones(zones, preset_id, preset_name) if zones else []
        return warnings

    def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        self._suspend_hot_path()
        try:
            self.panic()
        except Exception:  # noqa: BLE001 -- shutdown must not raise
            pass
        if self.sequencer is not None:
            try:
                self.sequencer.delete()
            except Exception:  # noqa: BLE001
                pass
            self.sequencer = None
        if self.fs is not None:
            try:
                self.fs.delete()
            except Exception:  # noqa: BLE001
                pass
            self.fs = None

    # ------------------------------------------------------------- soundfonts
    def load_soundfont(self, name: str) -> int:
        """Load (once) and return the sfid for a file in soundfonts/."""
        with self._lock:
            if name in self._sfids:
                return self._sfids[name]
            path = config.SOUNDFONT_DIR / name
            if not path.exists() or self.fs is None:
                return -1
            sfid = self.fs.sfload(str(path))
            if sfid != -1:
                self._sfids[name] = sfid
            return sfid

    def list_soundfonts(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(config.SOUNDFONT_DIR.glob("*")):
            if p.suffix.lower() in (".sf2", ".sf3") and p.is_file():
                out.append({
                    "file": p.name,
                    "size": p.stat().st_size,
                    "loaded": p.name in self._sfids,
                })
        return out

    def list_presets(self, soundfont: str | None = None) -> list[dict[str, Any]]:
        """Every (bank, program, name) the SoundFont actually contains.

        Enumerated rather than assumed -- GeneralUser GS has 173 melodic presets
        across banks 0/8/16/24/120 plus 13 drum kits, which no GM table would tell you.
        """
        name = soundfont or config.DEFAULT_SOUNDFONT
        if name in self._preset_cache:
            return self._preset_cache[name]
        sfid = self.load_soundfont(name)
        if sfid == -1 or self.fs is None:
            return []
        found: list[dict[str, Any]] = []
        for bank in range(129):
            for prog in range(128):
                label = self.fs.sfpreset_name(sfid, bank, prog)
                if label:
                    found.append({
                        "bank": bank,
                        "program": prog,
                        "name": label.strip(),
                        "drums": bank == DRUM_BANK,
                    })
        self._preset_cache[name] = found
        return found

    # -------------------------------------------------------------- fx (global)
    def apply_reverb(self, r: dict[str, Any]) -> None:
        if self.fs is None:
            return
        self.fs.set_reverb(
            roomsize=float(r.get("room", 0.3)),
            damping=float(r.get("damping", 0.4)),
            width=float(r.get("width", 6.0)),
            level=float(r.get("level", 0.55)),
        )

    def apply_chorus(self, c: dict[str, Any]) -> None:
        if self.fs is None:
            return
        self.fs.set_chorus(
            nr=int(c.get("nr", 3)),
            level=float(c.get("level", 1.2)),
            speed=float(c.get("speed", 0.4)),
            depth=float(c.get("depth", 6.0)),
            type=int(c.get("type", 0)),
        )

    # ------------------------------------------------------------------ zones
    def set_zones(self, zones: Iterable[Zone], preset_id: str = "", preset_name: str = "") -> list[str]:
        """Apply a new zone set. Returns any warnings worth showing the user."""
        zones = list(zones)
        warnings: list[str] = []
        if self.fs is None:
            return ["engine not started"]

        seen_channels: set[int] = set()
        for z in zones:
            if not z.enabled:
                continue
            if z.channel in seen_channels:
                warnings.append(
                    f"zone '{z.id}' shares channel {z.channel} with another zone; "
                    "gain/pan/reverb will fight. Give it its own channel."
                )
            seen_channels.add(z.channel)

            sfid = self.load_soundfont(z.soundfont)
            if sfid == -1:
                warnings.append(f"zone '{z.id}': soundfont '{z.soundfont}' not found")
                continue
            # Drum kits are selected by an explicit bank-128 program_select. Sending
            # bank 128 over the wire as CC0 does nothing under FluidSynth's default
            # gs bank-select mode -- "bank 128" is an SF2 file convention, not a wire value.
            if self.fs.program_select(z.channel, sfid, z.bank, z.program) == -1:
                warnings.append(
                    f"zone '{z.id}': no preset {z.bank}:{z.program} in {z.soundfont}; "
                    "falling back to Grand Piano"
                )
                self.fs.program_select(z.channel, sfid, 0, 0)
            self.fs.cc(z.channel, 7, int(round(z.gain * 127)))     # channel volume
            self.fs.cc(z.channel, 10, int(round(z.pan * 127)))     # pan
            self.fs.cc(z.channel, 91, int(round(z.reverb * 127)))  # reverb send
            self.fs.cc(z.channel, 93, int(round(z.chorus * 127)))  # chorus send

        # Silence channels that just lost their zone.
        for ch in {z.channel for z in self.zones} - seen_channels:
            self.fs.cc(ch, 123, 0)

        table: list[tuple] = []
        for note in range(128):
            entries = []
            for z in zones:
                if z.enabled and z.lo <= note <= z.hi:
                    out = note + z.transpose
                    if 0 <= out <= 127:
                        entries.append((z.channel, out, curve_for(z.curve, z.fixed_velocity)))
            table.append(tuple(entries))

        self.zones = zones
        self.preset_id = preset_id or self.preset_id
        self.preset_name = preset_name or self.preset_name
        self.active_channels = tuple(sorted(seen_channels)) or (0,)
        self.routes = tuple(table)  # <-- the atomic swap the hot path relies on
        self.warnings = warnings
        return warnings

    # --------------------------------------------------------------- HOT PATH
    # Called from rtmidi's callback thread. Keep these boring.
    def note_on(self, note: int, velocity: int) -> None:
        routes = self.routes[note]
        if not routes:
            return
        noteon = self.fs.noteon
        for channel, out, curve in routes:
            noteon(channel, out, curve[velocity])
        self._held[note] = routes

    def note_off(self, note: int) -> None:
        routes = self._held[note]
        if routes is None:
            routes = self.routes[note]
        else:
            self._held[note] = None
        noteoff = self.fs.noteoff
        for channel, out, _curve in routes:
            noteoff(channel, out)

    def control(self, cc: int, value: int) -> None:
        send = self.fs.cc
        for channel in self.active_channels:
            send(channel, cc, value)

    def bend(self, value: int) -> None:
        send = self.fs.pitch_bend
        for channel in self.active_channels:
            send(channel, value)

    # ------------------------------------------------------------------ misc
    def panic(self) -> None:
        if self.fs is None:
            return
        for ch in range(GM_CHANNELS):
            self.fs.cc(ch, 64, 0)    # sustain off first, or notes hang on
            self.fs.cc(ch, 123, 0)   # all notes off
            self.fs.cc(ch, 120, 0)   # all sound off
        for i in range(128):
            self._held[i] = None

    def voice_count(self) -> int:
        return self.fs.get_active_voice_count() if self.fs is not None else 0

    def held_notes(self) -> list[int]:
        """Ground truth for which keys are down.

        The websocket feed reconstructs this from the event stream, which can drop
        frames under load. Shipping this snapshot on the 1 Hz heartbeat means a UI
        that missed a note-off un-sticks itself within a second instead of forever.
        """
        held = self._held
        return [n for n in range(128) if held[n] is not None]

    def preview(self, note: int, velocity: int = 90, channel: int | None = None) -> None:
        """Sound one note directly, bypassing zones. For UI auditioning."""
        if self.fs is not None:
            self.fs.noteon(self.active_channels[0] if channel is None else channel, note, velocity)

    def preview_off(self, note: int, channel: int | None = None) -> None:
        if self.fs is not None:
            self.fs.noteoff(self.active_channels[0] if channel is None else channel, note)

    def status(self) -> dict[str, Any]:
        audio = self.settings.get("audio", default=config.HARDWARE) or config.HARDWARE
        rate = float(audio.get("sample_rate", 48000.0))
        period = int(audio.get("period_size", 144))
        return {
            "started": self.started,
            "sample_rate": rate,
            "period_size": period,
            # In shared mode the period size is ignored, so quoting it as the buffer
            # would be a lie. Windows picks the engine period there (~10 ms at 48 kHz).
            "buffer_ms": round(period / rate * 1000, 2) if audio.get("exclusive", True) else None,
            "exclusive": bool(audio.get("exclusive", True)),
            "device": str(audio.get("device", "default") or "default"),
            "polyphony": int(audio.get("polyphony", 256)),
            "gain": float(audio.get("gain", 0.6)),
            "voices": self.voice_count(),
            "preset_id": self.preset_id,
            "preset_name": self.preset_name,
            "zones": [z.to_dict() for z in self.zones],
            "warnings": self.warnings,
            "soundfonts": list(self._sfids),
        }


# ----------------------------------------------------------------- preset i/o
def load_preset_file(path: Path) -> Preset:
    data = json.loads(path.read_text("utf-8"))
    zones = [Zone.from_dict(z) for z in data.get("zones", [])]
    return Preset(
        id=data.get("id", path.stem),
        name=data.get("name", path.stem.replace("-", " ").title()),
        description=data.get("description", ""),
        zones=zones,
    )


def load_presets() -> dict[str, Preset]:
    out: dict[str, Preset] = {}
    if not config.PRESET_DIR.exists():
        return out
    for p in sorted(config.PRESET_DIR.glob("*.json")):
        try:
            preset = load_preset_file(p)
            out[preset.id] = preset
        except Exception:  # noqa: BLE001 -- one bad preset must not kill the app
            continue
    return out


def save_preset(preset: Preset) -> Path:
    config.PRESET_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PRESET_DIR / f"{preset.id}.json"
    path.write_text(json.dumps(preset.to_dict(), indent=2), "utf-8")
    return path
