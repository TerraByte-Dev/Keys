"""The loop station -- overdub layers until you are an ensemble of yourself.

Bar-locked, not free-form. You choose a length in bars, get a count-in, and the take
ends itself on the bar line. The alternative -- start recording on the first note and
stop when you hit the button -- puts the loop length at the mercy of your reflexes,
and every take that is 40 ms too long drifts audibly by the fourth pass.

Four decisions carry this module:

**Recording taps the drain, not the MIDI callback.** ``Hub.push`` already stamps
``perf_counter()`` at callback entry, so an event picked off the drain carries exactly
the timestamp it would have had if we had recorded in the callback -- at zero cost to
the hot path. The only thing lost is events the queue dropped under load, which is the
same trade the UI already makes.

**One grid, owned by the metronome.** Tempo, meter and bar length all come from
``Metronome.grid()``, and the transport lines its bar lines up with the click by
starting it at a chosen tick. A loop station with its own tempo field would be two
clocks that agree right up until they don't. While the transport runs, the tempo ramp
is suppressed through ``Metronome.override()`` -- an accelerating click and a
fixed-length loop cannot both be right.

**perf_counter and the sequencer tick are related on a worker thread, never on the
audio thread.** ``metronome.clock_fit`` exists because its observations are stamped
inside an audio-thread callback and jitter by tens of milliseconds. Reading
``seq.get_tick()`` and ``perf_counter()` back to back on a normal thread has no such
problem: the error is one audio block of tick quantisation, under 3 ms, and the anchor
is refreshed several times a second so it cannot accumulate.

**The pedal becomes note length.** pyfluidsynth's sequencer wraps note, note_on,
note_off and timer -- there is no control-change event -- so a recorded layer has no
CC to replay. Rather than drop the sustain pedal, a note held under the pedal keeps
sounding until the pedal lifts. That reproduces what you heard using only the events
we can schedule.

Five layers, because there are sixteen MIDI channels, the metronome owns one, and your
live zones need the rest. Bass, chords, melody, pad and drums is a band.
"""

from __future__ import annotations

import ctypes
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import fluidsynth

from . import config
from .engine import DRUM_BANK, Engine
from .hub import CONTROL, NOTE_OFF, NOTE_ON
from .metronome import Metronome

# Channels a layer may claim. 15 is the metronome's; 0-9 are left to live zones, which
# includes 9 for a drum zone. Five is the ceiling and the UI says so.
LAYER_CHANNELS = (10, 11, 12, 13, 14)

SUSTAIN_CC = 64
MIN_BARS = 1
MAX_BARS = 32
MAX_LAYERS = len(LAYER_CHANNELS)

# The doorbell wakes the worker on every cycle boundary; this is only the safety net
# that stops a missed callback from stalling playback forever.
WAKE_TIMEOUT = 0.2
# How far ahead cycles are queued. Comfortably more than one wake period, and more
# than the shortest possible loop (1 bar at 300 bpm = 800 ms).
SCHEDULE_AHEAD_MS = 1400.0
LEAD_IN_MS = 250.0
# Nobody lands on the downbeat. Notes this far *before* the bar line are pulled onto
# it rather than wrapping round to the end of the loop, which is what makes an
# anticipated first beat sound like a first beat instead of a late last one.
PRE_ROLL_MS = 70.0
# A pad that fills the whole loop should not click at the seam.
OVERHANG_MS = 140.0
MAX_CYCLES_PER_FILL = 64
REC_BUFFER = 16384

STOPPED, PLAYING, COUNTING, RECORDING = "stopped", "playing", "counting", "recording"

_remove_events = fluidsynth.cfunc(
    "fluid_sequencer_remove_events", None,
    ("seq", ctypes.c_void_p, 1),
    ("source", ctypes.c_int, 1),
    ("dest", ctypes.c_int, 1),
    ("type", ctypes.c_int, 1),
)


@dataclass
class Note:
    pos: float      # ms from the loop's bar 1 beat 1, 0 <= pos < loop_ms
    key: int
    vel: int
    dur: float      # ms, already including any pedal hold


@dataclass
class Layer:
    id: str
    name: str
    channel: int
    soundfont: str = config.DEFAULT_SOUNDFONT
    bank: int = 0
    program: int = 0
    gain: float = 0.85
    pan: float = 0.5
    muted: bool = False
    notes: list[Note] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "channel": self.channel,
            "soundfont": self.soundfont, "bank": self.bank, "program": self.program,
            "gain": self.gain, "pan": self.pan, "muted": self.muted,
            "notes": len(self.notes),
            # Enough for the UI to draw the layer without shipping every note twice a
            # second; the full note list only travels on save/load.
            "marks": [[round(n.pos, 1), n.key, n.vel, round(n.dur, 1)]
                      for n in self.notes[:512]],
        }


class LoopStation:
    def __init__(self, engine: Engine, metro: Metronome,
                 settings: config.Settings | None = None) -> None:
        self.engine = engine
        self.metro = metro
        self.settings = settings or config.settings

        self.state = STOPPED
        self.layers: list[Layer] = []
        self.origin = 0.0        # tick of the loop's first downbeat
        self.loop_ms = 0.0
        self.bars = 4
        self.last_error = ""

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: int | None = None
        self._next_cycle = 0
        self._armed = False
        self._rec_cycle: int | None = None
        self._rec_start = 0.0
        # Everything heard while the transport runs, so a take can reach back before
        # its own downbeat for the pre-roll. Bounded: a stuck transport must not grow
        # without limit.
        self._events: list[tuple[float, int, int, int]] = []
        # (tick, perf_counter) read back to back on this thread. See module docstring.
        self._anchor = (0.0, 0.0)

    # ------------------------------------------------------------------- config
    def cfg(self) -> dict[str, Any]:
        base = {"bars": 4, "click": True, "count_in_bars": 1}
        base.update(self.settings.get("loop", default={}) or {})
        base["bars"] = max(MIN_BARS, min(MAX_BARS, int(base.get("bars", 4))))
        base["count_in_bars"] = max(0, min(4, int(base.get("count_in_bars", 1))))
        return base

    def configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.settings.update({"loop": patch})
        # Changing the bar count with material recorded would leave every layer the
        # wrong length, so it only takes effect on an empty loop or after a stop.
        if "bars" in patch and self.state != STOPPED:
            self.last_error = "bar count applies when the transport is stopped"
        return self.cfg()

    # ---------------------------------------------------------------- transport
    def start(self) -> dict[str, Any]:
        """Roll the transport. Returns the state, or an error in `last_error`."""
        with self._lock:
            seq = self.engine.sequencer
            if seq is None:
                self.last_error = "audio engine is not running"
                return self.status()
            if self.state != STOPPED:
                return self.status()

            cfg = self.cfg()
            self.bars = int(cfg["bars"])
            _beat_ms, bar_ms, _beats = self.metro.grid()
            self.loop_ms = bar_ms * self.bars
            count_in = int(cfg["count_in_bars"])

            if self._client is None:
                self._client = seq.register_client("looper", self._on_cycle)

            # The click owns the grid: start it, then put bar 1 of the loop exactly
            # `count_in` bars later. Suppressing the ramp is not optional -- a click
            # that speeds up and a loop of fixed length disagree by design.
            self.metro.override({"ramp_enabled": False})
            if cfg.get("click", True):
                self.metro.stop()
                self.metro.start(at_tick=seq.get_tick() + LEAD_IN_MS)
                base = self.metro.start_tick
            else:
                base = seq.get_tick() + LEAD_IN_MS

            self.origin = base + count_in * bar_ms
            self._next_cycle = 0
            self._armed = False
            self._rec_cycle = None
            self._events.clear()
            self._touch_anchor()
            self.state = COUNTING if count_in else PLAYING
            self.last_error = ""

            for layer in self.layers:
                self._prepare_layer(layer)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="looper", daemon=True)
                self._thread.start()
            self._wake.set()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self.state == STOPPED:
                return self.status()
            # A take in progress at the moment you hit stop is still a take you played.
            if self.state == RECORDING:
                self._finish_take()
            self.state = STOPPED
            self._armed = False
            self._rec_cycle = None
            self._flush()
            self._silence_layers()
            self._events.clear()
        self.metro.release()
        self._wake.set()
        return self.status()

    def arm(self) -> dict[str, Any]:
        """Record the next whole cycle into a new layer."""
        if len(self.layers) >= MAX_LAYERS:
            self.last_error = (
                f"{MAX_LAYERS} layers is the ceiling -- there are 16 MIDI channels and "
                "your live zones need the rest. Delete one to record another."
            )
            return self.status()
        if self.state == STOPPED:
            self.start()
            if self.state == STOPPED:
                return self.status()
        with self._lock:
            self._armed = True
            self.last_error = ""
        self._wake.set()
        return self.status()

    def cancel(self) -> dict[str, Any]:
        """Disarm, and throw away a take that is currently being recorded."""
        with self._lock:
            self._armed = False
            self._rec_cycle = None
            if self.state == RECORDING:
                self.state = PLAYING
        return self.status()

    # ------------------------------------------------------------------ layers
    def update_layer(self, layer_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        layer = self._find(layer_id)
        if layer is None:
            self.last_error = "no such layer"
            return self.status()
        if "name" in patch:
            layer.name = str(patch["name"])[:40]
        if "muted" in patch:
            layer.muted = bool(patch["muted"])
        if "gain" in patch:
            layer.gain = max(0.0, min(1.0, float(patch["gain"])))
        if "pan" in patch:
            layer.pan = max(0.0, min(1.0, float(patch["pan"])))
        for key in ("soundfont", "bank", "program"):
            if key in patch:
                setattr(layer, key, patch[key] if key == "soundfont" else int(patch[key]))
        with self._lock:
            # Gain, pan and instrument are channel state, so they take effect on the
            # notes already queued for this cycle -- turn a layer down and you hear it
            # now. Mute additionally re-queues, so a muted layer stops occupying voices
            # instead of playing silently for another bar.
            self._prepare_layer(layer)
            if "muted" in patch:
                self._reflow()
        return self.status()

    def delete_layer(self, layer_id: str) -> dict[str, Any]:
        layer = self._find(layer_id)
        if layer is None:
            return self.status()
        with self._lock:
            self.layers = [x for x in self.layers if x.id != layer_id]
            self._silence(layer.channel)
            # Its notes are queued for the cycle already in flight; drop the whole
            # queue and re-fill so a deleted layer goes quiet now, not in four bars.
            self._reflow()
        return self.status()

    def clear(self) -> dict[str, Any]:
        self.stop()
        with self._lock:
            self.layers = []
        return self.status()

    # --------------------------------------------------------------- recording
    def on_event(self, t: float, kind: int, a: int, b: int) -> None:
        """Called from the drain loop for every MIDI event, transport running or not."""
        if self.state == STOPPED:
            return
        events = self._events
        if len(events) >= REC_BUFFER:
            del events[: REC_BUFFER // 2]
        events.append((t, kind, a, b))

    def _finish_take(self) -> None:
        """Turn the buffered events for the recorded cycle into a layer.

        Called with the lock held, from the worker, at the bar line that ends the take.
        """
        self._rec_cycle = None
        self._armed = False
        start, loop_ms = self._rec_start, self.loop_ms
        notes = self._build_notes(start, loop_ms)
        self.state = PLAYING
        if not notes:
            self.last_error = "nothing recorded -- the take was empty"
            return

        channel = self._free_channel()
        if channel is None:
            self.last_error = "no free MIDI channel for another layer"
            return
        layer = Layer(
            id=uuid.uuid4().hex[:8],
            name=f"Layer {len(self.layers) + 1}",
            channel=channel,
        )
        self._adopt_sound(layer, notes[0].key)
        layer.notes = notes
        self.layers.append(layer)
        self._prepare_layer(layer)
        self.last_error = ""
        # The new layer must join on the very next bar line, and the cycle after this
        # one is already queued without it.
        self._reflow()

    def _build_notes(self, start: float, loop_ms: float) -> list[Note]:
        """Events -> notes, resolving the sustain pedal into note length."""
        out: list[Note] = []
        pending: dict[int, tuple[float, int]] = {}
        sustained: set[int] = set()
        pedal = False

        def close(key: int, at: float) -> None:
            found = pending.pop(key, None)
            if found is None:
                return
            pos, vel = found
            dur = max(30.0, at - pos)
            out.append(Note(pos=pos, key=key, vel=vel, dur=dur))

        for t, kind, a, b in self._events:
            pos = self._tick_at(t) - start
            if pos < -PRE_ROLL_MS:
                continue
            if pos > loop_ms + PRE_ROLL_MS and kind == NOTE_ON:
                continue
            pos = max(0.0, pos)     # the pre-roll: an early note is an on-time note
            if kind == NOTE_ON:
                if pos >= loop_ms:
                    continue
                close(a, pos)       # a repeated key without its note-off
                pending[a] = (pos, b)
            elif kind == NOTE_OFF:
                if pedal:
                    sustained.add(a)
                else:
                    close(a, pos)
            elif kind == CONTROL and a == SUSTAIN_CC:
                was, pedal = pedal, b >= 64
                if was and not pedal:
                    for key in sorted(sustained):
                        close(key, pos)
                    sustained.clear()

        for key in sorted(pending):
            close(key, loop_ms)
        out.sort(key=lambda n: (n.pos, n.key))
        return out

    def _adopt_sound(self, layer: Layer, first_key: int) -> None:
        """Take the instrument from whichever zone you actually played the take in.

        Playing a bass line in the left half of a split should give you a bass layer.
        The zone under the take's first note is the only rule that gets that right
        without asking.
        """
        zones = [z for z in self.engine.zones if z.enabled]
        if not zones:
            return
        zone = next((z for z in zones if z.lo <= first_key <= z.hi), zones[0])
        layer.soundfont = zone.soundfont
        layer.bank = zone.bank
        layer.program = zone.program
        layer.gain = zone.gain
        layer.pan = zone.pan
        layer.name = zone.name or layer.name

    # --------------------------------------------------------------- scheduling
    def _touch_anchor(self) -> None:
        seq = self.engine.sequencer
        if seq is None:
            return
        # Back to back, on this thread. Anything between these two lines is error.
        tick = seq.get_tick()
        self._anchor = (float(tick), time.perf_counter())

    def _tick_at(self, perf_time: float) -> float:
        tick, perf = self._anchor
        return tick + (perf_time - perf) * 1000.0

    def _cycle_at(self, tick: float) -> int:
        if self.loop_ms <= 0 or tick < self.origin:
            return -1
        return int((tick - self.origin) // self.loop_ms)

    def _on_cycle(self, tick, event, seq, data) -> None:  # noqa: ANN001
        # AUDIO THREAD. Ring the doorbell and get out. No scheduling here, ever.
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.state != STOPPED:
                try:
                    self._tick()
                except Exception as exc:  # noqa: BLE001 -- the loop must never die
                    self.last_error = str(exc)
            self._wake.wait(timeout=WAKE_TIMEOUT)
            self._wake.clear()

    def _tick(self) -> None:
        with self._lock:
            if self.state == STOPPED or self.engine.sequencer is None:
                return
            self._touch_anchor()
            now = self.engine.sequencer.get_tick()
            cur = self._cycle_at(now)

            if self.state == COUNTING and cur >= 0:
                self.state = PLAYING
            if self._armed and self._rec_cycle is None:
                self._rec_cycle = max(0, cur + 1)
            if self._rec_cycle is not None:
                if cur >= self._rec_cycle and self.state != RECORDING:
                    self.state = RECORDING
                    self._rec_start = self.origin + self._rec_cycle * self.loop_ms
                elif self.state == RECORDING and cur > self._rec_cycle:
                    self._finish_take()

            self._fill(now)

    def _fill(self, now: float) -> None:
        seq = self.engine.sequencer
        if seq is None or self.loop_ms <= 0:
            return
        horizon = now + SCHEDULE_AHEAD_MS
        guard = 0
        while self.origin + self._next_cycle * self.loop_ms < horizon:
            guard += 1
            if guard > MAX_CYCLES_PER_FILL:
                break
            self._schedule_cycle(self._next_cycle)
            self._next_cycle += 1

    def _schedule_cycle(self, index: int) -> None:
        seq = self.engine.sequencer
        if seq is None:
            return
        start = self.origin + index * self.loop_ms
        dest = self.engine.seq_dest
        src = self._client if self._client is not None else -1
        # The doorbell that wakes this worker to queue the cycle after next.
        seq.timer(int(round(start)), source=src, dest=src)
        for layer in self.layers:
            if layer.muted:
                continue
            channel = layer.channel
            for n in layer.notes:
                dur = min(n.dur, self.loop_ms - n.pos + OVERHANG_MS)
                seq.note(int(round(start + n.pos)), channel, n.key, n.vel,
                         int(round(dur)), source=src, dest=dest)

    def _flush(self) -> None:
        """Drop our queued events -- ours only. The click's are not ours to cancel."""
        seq = self.engine.sequencer
        if seq is not None and _remove_events is not None and self._client is not None:
            _remove_events(seq.sequencer, self._client, -1, -1)

    def _reflow(self) -> None:
        """Re-queue from the next bar line, so a layer change is heard within a bar."""
        seq = self.engine.sequencer
        if seq is None or self.state == STOPPED:
            return
        self._flush()
        now = seq.get_tick()
        self._next_cycle = max(0, self._cycle_at(now) + 1)
        self._fill(now)
        self._wake.set()

    # -------------------------------------------------------------- synth setup
    def _prepare_layer(self, layer: Layer) -> None:
        fs = self.engine.fs
        if fs is None:
            return
        sfid = self.engine.load_soundfont(layer.soundfont)
        if sfid == -1:
            sfid = self.engine.load_soundfont(config.DEFAULT_SOUNDFONT)
        if sfid == -1:
            return
        if fs.program_select(layer.channel, sfid, layer.bank, layer.program) == -1:
            fs.program_select(layer.channel, sfid, DRUM_BANK if layer.bank == DRUM_BANK else 0, 0)
        gain = 0 if layer.muted else int(round(layer.gain * 127))
        fs.cc(layer.channel, 7, gain)
        fs.cc(layer.channel, 10, int(round(layer.pan * 127)))

    def _silence(self, channel: int) -> None:
        fs = self.engine.fs
        if fs is not None:
            fs.cc(channel, 123, 0)
            fs.cc(channel, 120, 0)

    def _silence_layers(self) -> None:
        for layer in self.layers:
            self._silence(layer.channel)

    def _free_channel(self) -> int | None:
        taken = {x.channel for x in self.layers}
        taken |= {z.channel for z in self.engine.zones if z.enabled}
        return next((c for c in LAYER_CHANNELS if c not in taken), None)

    def _find(self, layer_id: str) -> Layer | None:
        return next((x for x in self.layers if x.id == layer_id), None)

    # ----------------------------------------------------------------- storage
    def save(self, name: str) -> dict[str, Any]:
        safe = "".join(c for c in name if c.isalnum() or c in " -_").strip()[:40]
        if not safe:
            self.last_error = "give the loop a name"
            return self.status()
        _beat_ms, bar_ms, beats = self.metro.grid()
        path = config.RECORDING_DIR / f"{safe}.loop.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": safe,
            "bars": self.bars or self.cfg()["bars"],
            "bpm": round(60000.0 / (bar_ms / beats), 2),
            "beats_per_bar": beats,
            "layers": [{
                "name": x.name, "soundfont": x.soundfont, "bank": x.bank,
                "program": x.program, "gain": x.gain, "pan": x.pan, "muted": x.muted,
                "notes": [[round(n.pos, 2), n.key, n.vel, round(n.dur, 2)] for n in x.notes],
            } for x in self.layers],
        }, indent=1), "utf-8")
        self.last_error = ""
        return self.status()

    def saved(self) -> list[dict[str, Any]]:
        if not config.RECORDING_DIR.exists():
            return []
        out = []
        for p in sorted(config.RECORDING_DIR.glob("*.loop.json")):
            try:
                d = json.loads(p.read_text("utf-8"))
                out.append({"name": d.get("name", p.stem), "bars": d.get("bars", 4),
                            "bpm": d.get("bpm", 0), "layers": len(d.get("layers", []))})
            except Exception:  # noqa: BLE001 -- one bad file must not hide the rest
                continue
        return out

    def load(self, name: str) -> dict[str, Any]:
        path = config.RECORDING_DIR / f"{name}.loop.json"
        if not path.exists():
            self.last_error = f"no saved loop called '{name}'"
            return self.status()
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"could not read that loop: {exc}"
            return self.status()

        self.stop()
        layers: list[Layer] = []
        for i, d in enumerate(data.get("layers", [])[:MAX_LAYERS]):
            layers.append(Layer(
                id=uuid.uuid4().hex[:8],
                name=str(d.get("name", f"Layer {i + 1}")),
                channel=LAYER_CHANNELS[i],
                soundfont=str(d.get("soundfont", config.DEFAULT_SOUNDFONT)),
                bank=int(d.get("bank", 0)), program=int(d.get("program", 0)),
                gain=float(d.get("gain", 0.85)), pan=float(d.get("pan", 0.5)),
                muted=bool(d.get("muted", False)),
                notes=[Note(pos=float(n[0]), key=int(n[1]), vel=int(n[2]), dur=float(n[3]))
                       for n in d.get("notes", [])],
            ))
        self.layers = layers
        self.bars = max(MIN_BARS, min(MAX_BARS, int(data.get("bars", 4))))
        self.settings.update({"loop": {"bars": self.bars}})
        # The tempo it was played at, restored -- a loop recorded at 92 is not a loop
        # at 120, and the notes carry absolute milliseconds.
        bpm = float(data.get("bpm", 0) or 0)
        if 20 <= bpm <= 300:
            self.metro.configure({"bpm": bpm,
                                  "beats_per_bar": int(data.get("beats_per_bar", 4))})
        self.last_error = ""
        return self.status()

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        seq = self.engine.sequencer
        cfg = self.cfg()
        pos = 0.0
        cycle = -1
        if seq is not None and self.state != STOPPED and self.loop_ms > 0:
            now = float(seq.get_tick())
            cycle = self._cycle_at(now)
            if cycle >= 0:
                pos = (now - self.origin) % self.loop_ms / self.loop_ms
        _beat_ms, bar_ms, beats = self.metro.grid()
        running = self.state != STOPPED
        # A recorded layer is a list of absolute milliseconds. Move the tempo under it
        # and the loop is still the old length while the click is the new one -- so say
        # so out loud rather than let it sound like drift.
        desynced = running and abs(bar_ms * self.bars - self.loop_ms) > 1.0
        return {
            "state": self.state,
            "armed": self._armed,
            "tempo_locked": running,
            "desynced": desynced,
            "bars": self.bars if self.state != STOPPED else int(cfg["bars"]),
            "beats_per_bar": beats,
            "bar_ms": round(bar_ms, 2),
            "loop_ms": round(self.loop_ms if self.state != STOPPED else bar_ms * cfg["bars"], 2),
            "position": round(pos, 4),
            "cycle": cycle,
            "click": bool(cfg["click"]),
            "count_in_bars": int(cfg["count_in_bars"]),
            "layers": [x.to_dict() for x in self.layers],
            "max_layers": MAX_LAYERS,
            "error": self.last_error,
        }

    def shutdown(self) -> None:
        self.stop()
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
