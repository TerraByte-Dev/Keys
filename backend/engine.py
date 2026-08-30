"""FluidSynth: the only thing in this app that makes sound.

Design rules that are not negotiable (see docs/ARCHITECTURE.md):

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
The obvious-looking alternatives are silently wrong -- read docs/HARDWARE.md
before "fixing" any of them.
"""

from __future__ import annotations

import ctypes
import json
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from . import config  # noqa: F401  (sets PATH + switch interval via backend/__init__)

import fluidsynth  # noqa: E402  -- must come after the package import above

# Bound once. note_off() stamps with this in the timed pedal mode, and the hot path
# does not do attribute lookups it can avoid.
_now = time.perf_counter

DRUM_BANK = 128
GM_CHANNELS = 16
SUSTAIN_CC = 64

# What the one pedal on a P-71 can be made to do. "" is the fourth option and the
# default: hand CC 64 straight to FluidSynth and stay out of it.
#   zone      -- sustain only a range of keys, so the left hand rings and the right
#                hand stays dry. No acoustic piano can do this; a split keyboard can.
#   sostenuto -- the middle pedal of a grand: catches what is sounding at the moment
#                you press, and nothing after. The classic answer to the same problem.
#   hold      -- latching. Press to sustain, press again to release, for a momentary
#                pedal and a passage where you would rather not hold your foot down.
PEDAL_MODES = ("zone", "sostenuto", "hold")

# General MIDI groups its 128 programs into sixteen families of eight, and every bank
# in a GS SoundFont is a variation on the same program numbers -- so program // 8 gives
# the family for all 287 presets, banked or not, without a lookup table of names.
#
# The GM family names are dated ("Chromatic Percussion", "Synth Effects"), so these are
# what a person would actually search for. A flat list of 287 is a list nobody reads.
FAMILIES = (
    "Piano", "Bells", "Organ", "Guitar", "Bass", "Strings", "Ensemble", "Brass",
    "Reed", "Flute", "Lead", "Pad", "Synth FX", "World", "Drums", "Effects",
)


def family_of(bank: int, program: int) -> str:
    if bank == DRUM_BANK:
        return "Kits"
    return FAMILIES[min(15, max(0, program // 8))]


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
    # Stamped by save_preset, so "did Keys write this?" has the same answer from a
    # source checkout as it does frozen. A directory test cannot: in a checkout
    # DATA_DIR, BUNDLE and the repo's presets/ are all the same folder.
    saved: bool = False
    # The two global FX units, or {} for every preset that ships with Keys. Per-zone
    # gain/pan/reverb/chorus already persist -- those are Zone fields. These are the
    # nine unit params that live in Settings, and they are kept at preset level rather
    # than on the zones because the units themselves are global.
    #
    # Empty is what makes browsing the shelf safe: a shipped preset carries {} and
    # therefore still cannot overwrite a reverb you tuned, which is the promise
    # server.load_preset already makes. Only a profile you saved carries the units.
    effects: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "zones": [z.to_dict() for z in self.zones],
            "saved": self.saved,
            "effects": self.effects,
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

        # --- pedal ------------------------------------------------------------
        # "" means FluidSynth handles the damper itself: CC 64 goes down the wire and
        # nothing in Python is involved. That is the default and it costs one attribute
        # load in note_off().
        #
        # Every other mode has to be implemented here instead, because they all mean
        # "sustain SOME of the notes", and CC 64 has no way to say that -- the synth
        # sustains a whole channel or none of it. So the pedal is swallowed and a
        # note-off is held back for the notes the mode catches.
        #
        # _catch is a 128-byte mask, not a set or a range check: the hot path does one
        # indexed load, and rebuilding it is a pedal-rate operation, not a note-rate one.
        self.pedal_mode: str = ""
        self.pedal_lo, self.pedal_hi = config.instrument_range(self.settings)
        self._pedal_down = False
        self._catch = bytearray(128)
        # Routes for notes whose note-off the pedal is holding back.
        self._pedal_held: list[tuple | None] = [None] * 128
        # perf_counter at which each of those was caught, for the timed mode.
        self._pedal_at: list[float] = [0.0] * 128
        self.pedal_decay: float = 0.0   # seconds; 0 = hold until the pedal comes up

        # --- cold state (server thread only) ----------------------------------
        self._lock = threading.Lock()
        self._sfids: dict[str, int] = {}
        self._preset_cache: dict[str, list[dict[str, Any]]] = {}
        self.zones: list[Zone] = []
        self.preset_id: str = ""
        self.preset_name: str = ""
        self.started = False
        # True only between _suspend_hot_path() and the next start(). Nothing may refill
        # the routing table while it is set -- see _rebind_routes.
        self._suspended = False
        self.warnings: list[str] = []
        # Why the audio stream is not open, when it is not -- "" whenever audio is fine.
        # Read at boot into server._boot_errors and carried out of restart().
        self.audio_error: str = ""

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        if self.started:
            return
        # Cleared per attempt, not on success -- the fallback path below opens a working
        # stream on the wrong device and must keep its reason.
        self.audio_error = ""
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
        # The fallback is False to match config.HARDWARE. It used to be True, so a
        # settings dict that merely LACKED the key came up holding the output device
        # -- silencing Spotify, Discord and everything else on the machine. A default
        # that only applies once something has already gone slightly wrong is exactly
        # the one that must not be the destructive option.
        fs.setting("audio.wasapi.exclusive-mode", 1 if audio.get("exclusive", False) else 0)
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
        # Hand start() the device rather than letting it fetch its own. pyfluidsynth does
        # `device = device or self.get_setting(f'audio.{driver}.device')`, and get_setting
        # copies strings into a 32-byte buffer (fluidsynth.py:799-801) -- so a name over
        # 31 bytes comes back TRUNCATED and line 825 writes that stump back over the good
        # name set above, immediately before opening the driver. It is not a Bluetooth
        # bug, it is a length bug that Bluetooth names always trip: "Speakers (Realtek(R)
        # Audio)" is 27 and survives, "Headset (WH-1000XM4 Hands-Free AG Audio)" is 40 and
        # cannot. Measured: the same 38-char endpoint refuses via the settings path and
        # opens via this one. "default" is what the setting holds when unset, so passing
        # it through needs no special case.
        fs.start(driver="wasapi", device=device)

        # new_fluid_audio_driver returns NULL when the endpoint refuses; start() stores it
        # and returns FLUID_OK anyway (fluidsynth.py:826, 839), and nothing raises. The
        # truthiness of fs.audio_driver is the ONLY in-process signal that a stream
        # exists -- the same test tools/audio_check.py already makes. Not making it is
        # what let started=True stand with no driver attached, which is in turn what made
        # restart()'s guard and server.set_audio's warning unreachable.
        if fs.audio_driver is None and device != "default":
            # A pinned endpoint that will not open has usually gone away rather than said
            # no: Bluetooth headphones that slept, powered off, or roamed to a phone stop
            # enumerating entirely. Falling back to the default is what you would do by
            # hand; doing it silently is not, so the reason rides out on audio_error.
            # Rebuilding only the audio driver -- a second fs.start() would leak the MIDI
            # router and driver it already built.
            self.audio_error = (
                f'"{device}" would not open, so Keys is using the system default '
                "instead. If those are Bluetooth headphones, wake them and pick them "
                "again."
            )
            fs.setting("audio.wasapi.device", "default")
            fs.audio_driver = fluidsynth.new_fluid_audio_driver(fs.settings, fs.synth)
        if fs.audio_driver is None:
            self.audio_error = (
                f"the audio device refused {int(rate)} Hz"
                + (f" at {period} samples in exclusive mode -- try shared mode"
                   if audio.get("exclusive", False)
                   else " in shared mode -- try a different output device")
            )
            # Nothing above survives a failed open: this Synth holds a soundfont and the
            # sfids naming it, and stop() returns at its first line while started is
            # False, so it will never clean up after us. Undo it here or the next start()
            # builds a second Synth on top of the first.
            fs.delete()
            self.fs = None
            self._sfids.clear()
            self._preset_cache.clear()
            self.started = False
            # The hot path must not be left pointing at a Synth that no longer exists.
            # startup() carries on past a failed audio open -- it still wants MIDI, the
            # visual keyboard and the practice clock -- so notes DO arrive at an engine
            # whose fs is None, which 0.7.3 could never reach. Emptying the routing table
            # makes note_on/note_off return at their first branch instead of raising.
            self._suspend_hot_path()
            return

        self.apply_reverb(self.settings.get("reverb", default={}) or {})
        self.apply_chorus(self.settings.get("chorus", default={}) or {})

        # use_system_timer=False + register_fluidsynth means the *audio render thread*
        # advances the sequencer clock. Verified on this machine: self-advancing,
        # drift-free, and the same clock the sound comes out of. The system-timer mode
        # is deprecated in FluidSynth 2.x and did not deliver callbacks here at all.
        self.sequencer = fluidsynth.Sequencer(time_scale=1000, use_system_timer=False)
        self.seq_dest = self.sequencer.register_fluidsynth(fs)

        self._suspended = False
        self.started = True

    def _suspend_hot_path(self) -> None:
        """Make the MIDI callback a no-op before the Synth is torn down.

        The callback thread is still live during a restart and reaches self.fs on every
        note. Emptying the routing table and the channel list first means it returns at
        its very first branch instead of calling into a freed Synth. Order matters:
        these three assignments are each atomic, and all of them happen before delete().
        """
        self._suspended = True
        self.routes = EMPTY_ROUTES
        self.active_channels = ()
        self._pedal_down = False
        for i in range(128):
            self._held[i] = None
            self._pedal_held[i] = None

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
            return [self.audio_error or "audio engine did not restart"]
        warnings = self.set_zones(zones, preset_id, preset_name) if zones else []
        # Opened, but not on the device that was asked for. The fallback above makes this
        # the common failure now, and "sound, just not where you pointed it" must not
        # arrive as a silent success.
        if self.audio_error:
            warnings.insert(0, self.audio_error)
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
        """Load (once) and return the sfid for a SoundFont on the search path."""
        with self._lock:
            if name in self._sfids:
                return self._sfids[name]
            path = config.find_asset("soundfonts", name)
            if path is None or self.fs is None:
                return -1
            sfid = self.fs.sfload(str(path))
            if sfid != -1:
                self._sfids[name] = sfid
            return sfid

    def list_soundfonts(self) -> list[dict[str, Any]]:
        out = []
        for p in config.list_assets("soundfonts", "*"):
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
                        "family": family_of(bank, prog),
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

    def set_send(self, kind: str, value: float) -> float:
        """How much of what you are playing goes to the room. CC 91 / CC 93.

        The two units above are global and belong to Settings; this is the per-channel
        send into them, which is the one knob that changes the instrument rather than
        the room it is in. Live, on every enabled zone at once -- the per-zone sends in
        Layers are the same controllers, reached one zone at a time behind Apply.
        (CC 71/72/73/74/75 are not an option here: FluidSynth installs only the SF2.04
        default modulators and none of those CCs is among them. Rendered output is
        byte-identical at 0, 64 and 127, so a brightness knob would be a placebo.)

        Written back onto the Zone as well as down the wire, because set_zones() re-sends
        every zone's stored send -- without that the next zone edit would silently undo
        this. The Zone written is the engine's own copy: set_zones() clones what it is
        handed, so this never reaches the preset on the shelf. Safe from the hot path's
        point of view too -- the routing table carries channel, note and curve, never the
        sends, so nothing the callback thread reads moves here.
        """
        value = max(0.0, min(1.0, float(value)))
        if self.fs is None:
            return value
        reverb = kind == "reverb"
        for z in self.zones:
            if not z.enabled:
                continue
            if reverb:
                z.reverb = value
            else:
                z.chorus = value
            self.fs.cc(z.channel, 91 if reverb else 93, int(round(value * 127)))
        return value

    # ------------------------------------------------------------------ zones
    def set_zones(self, zones: Iterable[Zone], preset_id: str = "", preset_name: str = "") -> list[str]:
        """Apply a new zone set. Returns any warnings worth showing the user."""
        # replace(), not list(): what we are handed is usually a Preset's own Zone
        # objects, and set_send() writes the live send back onto every enabled zone.
        # A shallow copy shared them, so dragging the Effects reverb to 0.95 edited the
        # shipped preset in app_state.presets -- re-picking Acoustic Grand came back at
        # 0.95 instead of the 0.30 in its JSON, until a restart. The engine owns a
        # working copy; the catalog keeps the file's values.
        zones = [replace(z) for z in zones]
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

        self.zones = zones
        # No sticky fallback. An empty preset_id is the honest answer to "which saved
        # preset is loaded?" after you edit zones or pick an instrument by hand -- none
        # is. Keeping the old id made the UI re-light the wrong chip every second while
        # preset_name said something else entirely.
        self.preset_id = preset_id
        self.preset_name = preset_name
        self.active_channels = tuple(sorted(seen_channels)) or (0,)
        self._rebind_routes()
        self.warnings = warnings
        return warnings

    def _rebind_routes(self) -> None:
        """Rebuild the note -> (channel, out, curve) table from self.zones.

        Split out of set_zones because the master octave shift changes only this, and
        re-running set_zones for it would re-send every program_select and every CC --
        an audible click, and a soundfont reload, for a button you may press twice a bar.

        Refuses while the hot path is suspended, and that guard is not belt-and-braces.
        _suspend_hot_path() empties this very table so the callback thread returns at its
        first branch while the Synth is being freed; an OCT press arriving from a request
        thread inside that window would refill it and hand the callback a pointer into a
        deleted Synth. `started` is the wrong flag to test -- the check scripts drive a
        stub Synth without ever calling start().
        """
        if self._suspended:
            return
        shift = 12 * config.master_octave(self.settings)
        table: list[tuple] = []
        for note in range(128):
            entries = []
            for z in self.zones:
                if z.enabled and z.lo <= note <= z.hi:
                    out = note + shift + z.transpose
                    # A shift can push a key off the end of MIDI. Dropping it is right:
                    # there is no such pitch, and the alternative -- folding it back an
                    # octave -- would answer a key you pressed with a note you did not.
                    # The keyboard marks these dead, so it is visible rather than silent.
                    if 0 <= out <= 127:
                        entries.append((z.channel, out, curve_for(z.curve, z.fixed_velocity)))
            table.append(tuple(entries))
        self.routes = tuple(table)  # <-- the atomic swap the hot path relies on

    def apply_instrument(self) -> None:
        """Re-read the instrument settings and bring everything derived from them true.

        Called after the range or the octave shift is written. Two things go stale: the
        routing table bakes in the shift, and the pedal's catch mask is built from the
        zone met with the keys that exist.
        """
        if self.fs is None:
            return
        self._rebind_routes()
        if self.pedal_mode == "zone":
            lo_, hi_ = self._pedal_span()
            catch = self._catch
            # Rebuilt directly rather than through set_pedal, which releases everything
            # the pedal is holding -- right when the mode changes, wrong for this, where
            # it would cut a sustained chord off under your foot on every octave press.
            for i in range(128):
                catch[i] = 1 if lo_ <= i <= hi_ else 0

    def set_master_octave(self, octaves: int) -> int:
        """Shift the whole instrument by whole octaves. Returns what was applied.

        The shift is baked into the routing table rather than added in note_on, for two
        reasons. The hot path stays exactly as boring as it was -- no arithmetic, no
        second attribute load. And note_off is already correct for free: note_on stores
        the routes it actually used in self._held[note], so a key still down when the
        octave moves is released at the pitch it was struck at. That is the same
        invariant a mid-hold zone change relies on, and it is why this is not a new
        class of stuck note.
        """
        n = max(-config.MAX_OCTAVE, min(config.MAX_OCTAVE, int(octaves)))
        self.settings.update({"instrument": {"octave": n}})
        if self.fs is not None:
            self._rebind_routes()
        return n

    # --------------------------------------------------------------- HOT PATH
    # Called from rtmidi's callback thread. Keep these boring.
    def note_on(self, note: int, velocity: int) -> None:
        routes = self.routes[note]
        # Re-striking a key the pedal is still holding. Dropping the claim is right ONLY
        # when the new strike lands on the same voice -- then it steals it, and releasing
        # the old one later would cut the new note off.
        #
        # It does not always land on the same voice. The octave shift and zone transpose
        # are baked into the table, so a key struck at OCT 0 and re-struck at OCT -1 is a
        # different pitch on the way out, and the pedal's voice is left ringing with
        # nothing holding a reference to it: a stuck note no note-off can ever reach.
        # The empty case matters too -- a shift can push a key off the end of MIDI, and
        # the early return below would skip this entirely.
        #
        # Comparing whole route tuples is cheap: curve_for hands out cached tuples, so
        # the common "nothing moved" case settles on identity.
        pedal_held = self._pedal_held[note]
        if pedal_held is not None:
            self._pedal_held[note] = None
            if pedal_held != routes:
                noteoff = self.fs.noteoff
                for channel, out, _curve in pedal_held:
                    noteoff(channel, out)
        if not routes:
            return
        noteon = self.fs.noteon
        for channel, out, curve in routes:
            noteon(channel, out, curve[velocity])
        self._held[note] = routes

    def note_off(self, note: int) -> None:
        routes = self._held[note]
        # One attribute load and one bool test when the pedal is native, which is the
        # default. Everything below this line is off unless a pedal mode is set.
        if self._pedal_down and self._catch[note]:
            if routes is not None:
                self._pedal_held[note] = routes
                self._pedal_at[note] = _now()
                self._held[note] = None
            return
        if routes is None:
            routes = self.routes[note]
        else:
            self._held[note] = None
        noteoff = self.fs.noteoff
        for channel, out, _curve in routes:
            noteoff(channel, out)

    def control(self, cc: int, value: int) -> None:
        if cc == SUSTAIN_CC and self.pedal_mode:
            self._pedal(value >= 64)
            return          # swallowed: the synth must not also sustain the channel
        send = self.fs.cc
        for channel in self.active_channels:
            send(channel, cc, value)

    def _pedal(self, down: bool) -> None:
        """Pedal edge. Runs on the callback thread, a few times a second at most."""
        if self.pedal_mode == "hold":
            # Latching: the press is the whole event and the release means nothing.
            if not down:
                return
            if self._pedal_down:
                self._pedal_down = False
                self._release_pedal_held()
            else:
                self._pedal_down = True
            return
        if down == self._pedal_down:
            return
        if down:
            if self.pedal_mode == "sostenuto":
                # The middle pedal of a grand: it catches exactly what is sounding at
                # the instant it goes down, and nothing you play afterwards. A pianist
                # holds a bass note with it and then plays staccato over the top --
                # which is the thing one damper pedal cannot do.
                held = self._held
                catch = self._catch
                for i in range(128):
                    catch[i] = 1 if held[i] is not None else 0
            self._pedal_down = True
            return
        self._pedal_down = False
        self._release_pedal_held()

    def decay_tick(self, now: float) -> list[int]:
        """Release pedal-held notes whose decay has run out. Called from the drain.

        Deliberately NOT the sequencer, which is the house rule for musical timing --
        and this is the one case where the rule points the wrong way. A scheduled
        note-off cannot be cancelled individually (fluid_sequencer_remove_events
        filters by source, dest and type, never by note), so re-striking a key while
        its tail was still ringing would let the old event fire and cut the new note
        off mid-phrase. Holding the note here instead makes that case correct, and a
        decay tail is not a grid event: the drain's 16 ms resolution on "this stopped
        after three seconds" is inaudible, and there is nothing for it to drift against.
        """
        if not self.pedal_decay or self.fs is None:
            return []
        cutoff = now - self.pedal_decay
        released: list[int] = []
        pedal_held = self._pedal_held
        noteoff = self.fs.noteoff
        for i in range(128):
            routes = pedal_held[i]
            if routes is None or self._pedal_at[i] > cutoff:
                continue
            pedal_held[i] = None
            released.append(i)
            for channel, out, _curve in routes:
                noteoff(channel, out)
        return released

    def _release_pedal_held(self) -> None:
        fs = self.fs
        if fs is None:
            for i in range(128):
                self._pedal_held[i] = None
            return
        noteoff = fs.noteoff
        pedal_held = self._pedal_held
        for i in range(128):
            routes = pedal_held[i]
            if routes is None:
                continue
            pedal_held[i] = None
            for channel, out, _curve in routes:
                noteoff(channel, out)

    # ------------------------------------------------------------------ pedal
    def set_pedal(self, mode: str = "", lo: int | None = None, hi: int | None = None,
                  decay: float | None = None) -> dict[str, Any]:
        """Choose what the one pedal you own actually does.

        Rebinding order matters. The catch mask is built before the mode is published,
        so the callback thread never sees a live mode pointed at a stale mask.
        """
        mode = mode if mode in PEDAL_MODES else ""
        if decay is not None:
            # 0 means hold until the pedal comes up. 30 s is a ceiling, not a
            # recommendation -- past a few seconds this is a texture, not a pedal.
            self.pedal_decay = max(0.0, min(30.0, float(decay)))
        # What you ASKED for is what is kept, and the clamp to the keys that exist
        # happens where the range is used -- see _pedal_span below.
        #
        # The other way round is destructive and was: clamping on write meant declaring
        # a 61-key controller permanently rewrote a saved 21..108 pedal zone down to
        # 36..96 on the next boot, and widening back to 88 did not bring it back. A
        # settings change must not silently delete a different setting. This way
        # narrow-then-widen restores exactly what you had.
        if lo is not None:
            self.pedal_lo = max(0, min(127, int(lo)))
        if hi is not None:
            self.pedal_hi = max(0, min(127, int(hi)))
        if self.pedal_lo > self.pedal_hi:
            self.pedal_lo, self.pedal_hi = self.pedal_hi, self.pedal_lo

        catch = self._catch
        if mode == "zone":
            lo_, hi_ = self._pedal_span()
            for i in range(128):
                catch[i] = 1 if lo_ <= i <= hi_ else 0
        elif mode == "sostenuto":
            for i in range(128):
                catch[i] = 0          # filled at the moment the pedal goes down
        else:
            for i in range(128):
                catch[i] = 1          # "hold" catches everything; "" never reads it

        # Anything the old mode was holding is not the new mode's to hold.
        was_down = self._pedal_down
        self._pedal_down = False
        self._release_pedal_held()
        self.pedal_mode = mode
        if not mode and self.fs is not None:
            # Handing the damper back to FluidSynth: make sure it does not inherit a
            # pedal we swallowed the down-edge of.
            for ch in self.active_channels:
                self.fs.cc(ch, SUSTAIN_CC, 0)
        elif mode and was_down:
            self._pedal_down = True
        self.settings.update({"pedal": {
            "mode": mode, "lo": self.pedal_lo, "hi": self.pedal_hi,
            "decay": self.pedal_decay,
        }})
        return self.pedal_status()

    def _pedal_span(self) -> tuple[int, int]:
        """The pedal zone as it actually applies: what you asked for, met with what you
        have. "Sustain from A0" means nothing on a board whose lowest key is C2."""
        low_key, high_key = config.instrument_range(self.settings)
        return (max(low_key, min(high_key, self.pedal_lo)),
                max(low_key, min(high_key, self.pedal_hi)))

    def load_pedal(self) -> None:
        """Restore the saved pedal setup. Called once, after the Synth exists."""
        saved = self.settings.get("pedal", default={}) or {}
        self.set_pedal(
            mode=str(saved.get("mode", "") or ""),
            lo=saved.get("lo"), hi=saved.get("hi"), decay=saved.get("decay"),
        )

    def pedal_status(self) -> dict[str, Any]:
        # lo/hi are what the sliders should show -- what you asked for. eff_lo/eff_hi are
        # what the pedal is actually doing once your keyboard has had its say; the panel
        # says so when the two differ, rather than moving your slider behind your back.
        eff_lo, eff_hi = self._pedal_span()
        return {
            "mode": self.pedal_mode,
            "lo": self.pedal_lo,
            "hi": self.pedal_hi,
            "eff_lo": eff_lo,
            "eff_hi": eff_hi,
            "decay": self.pedal_decay,
            "down": self._pedal_down,
            "holding": [i for i in range(128) if self._pedal_held[i] is not None],
            "modes": ["", *PEDAL_MODES],
        }

    def bend(self, value: int) -> None:
        send = self.fs.pitch_bend
        for channel in self.active_channels:
            send(channel, value)

    # ------------------------------------------------------------------ misc
    def panic(self) -> None:
        if self.fs is None:
            return
        self._pedal_down = False
        for ch in range(GM_CHANNELS):
            self.fs.cc(ch, SUSTAIN_CC, 0)  # sustain off first, or notes hang on
            self.fs.cc(ch, 123, 0)   # all notes off
            self.fs.cc(ch, 120, 0)   # all sound off
        for i in range(128):
            self._held[i] = None
            self._pedal_held[i] = None
            self._pedal_at[i] = 0.0

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
        # These four describe a stream, so they are None when there is no stream. They
        # come from the settings dict, which happily holds 48 kHz and 144 samples for a
        # device that refused both -- printing "3.00 ms / 48k / Exclusive" over a driver
        # that never opened is precisely how a silent engine passed for a working one.
        live = self.fs is not None and self.fs.audio_driver is not None
        return {
            "started": self.started,
            "audio_error": self.audio_error,
            "sample_rate": rate if live else None,
            "period_size": period if live else None,
            # In shared mode the period size is ignored, so quoting it as the buffer
            # would be a lie. Windows picks the engine period there (~10 ms at 48 kHz).
            "buffer_ms": (round(period / rate * 1000, 2)
                          if live and audio.get("exclusive", False) else None),
            "exclusive": bool(audio.get("exclusive", False)) if live else None,
            "device": str(audio.get("device", "default") or "default"),
            "polyphony": int(audio.get("polyphony", 256)),
            "gain": float(audio.get("gain", 0.6)),
            "voices": self.voice_count(),
            "preset_id": self.preset_id,
            "preset_name": self.preset_name,
            "zones": [z.to_dict() for z in self.zones],
            # Carried on the engine's own status rather than only in settings because
            # the frontend needs it on the same object as `zones` -- the two together
            # are what decide which keys actually make a sound.
            "octave": config.master_octave(self.settings),
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
        # Absent means shipped. The 62 generated by tools/make_presets.py never carry
        # the key, so they read as factory forever without touching any of them.
        saved=bool(data.get("saved", False)),
        effects=dict(data.get("effects") or {}),
    )


def load_presets() -> dict[str, Preset]:
    """Presets from the search path: yours shadow the shipped ones by file name."""
    out: dict[str, Preset] = {}
    for p in config.list_assets("presets", "*.json"):
        try:
            preset = load_preset_file(p)
            out[preset.id] = preset
        except Exception:  # noqa: BLE001 -- one bad preset must not kill the app
            continue
    return out


def save_preset(preset: Preset) -> Path:
    # Stamped here rather than by the caller, so every route into this function --
    # the endpoint, a script, a future importer -- produces a file that knows it is
    # yours.
    preset.saved = True
    # Always to the data directory, never into the bundle. A preset written next to
    # the executable is a preset the next update deletes.
    config.PRESET_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PRESET_DIR / f"{preset.id}.json"
    path.write_text(json.dumps(preset.to_dict(), indent=2), "utf-8")
    return path
