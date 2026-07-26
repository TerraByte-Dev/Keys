"""Metronome driven by FluidSynth's sequencer -- never by a Python sleep.

Why this shape (all of it verified on this machine, see the probe results in
docs/FEASIBILITY.md):

* ``Sequencer(use_system_timer=False)`` + ``register_fluidsynth`` makes the **audio
  render thread** advance the sequencer clock. The clicks therefore ride the same
  clock the sound comes out of, and cannot drift against it. The system-timer mode
  is deprecated in FluidSynth 2.x and delivered no callbacks here at all.
* The sequencer's client callback runs **on that audio thread**. So the callback in
  here does two things and stops: stamp an observation, ring a doorbell. All actual
  scheduling happens on a normal worker thread that the doorbell wakes.
* Events are scheduled a short window ahead and the window is deliberately short
  (~1.5 beats), because already-queued events can only be cancelled wholesale.
  A tempo change flushes and re-fills, so the slider feels instant.

The clock mapping exists because the tick clock (audio) and ``perf_counter``
(system) are different clocks, and comparing a player's note onsets to the click
grid means relating the two. Individual observations are noisy -- Python callback
delivery on the audio thread jitters by tens of milliseconds -- so it is a
least-squares fit over many samples, which averages that out to about a millisecond.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from typing import Any

import fluidsynth

from . import config
from .engine import DRUM_BANK, Engine

# Reserved so a user drum zone on channel 9 and the metronome never fight.
METRONOME_CHANNEL = 15

# Safety net only. The doorbell from the audio thread is what normally wakes the
# scheduler; this timeout just stops a missed callback from silently stopping the
# click forever. It is not the timing source.
WAKE_TIMEOUT = 0.25
MIN_LOOKAHEAD_MS = 400.0
LEAD_IN_MS = 120.0

# pyfluidsynth does not wrap this one, but the DLL exports it. Without it, changing
# tempo could not cancel already-queued clicks and every bpm change would double up.
_remove_events = fluidsynth.cfunc(
    "fluid_sequencer_remove_events", None,
    ("seq", ctypes.c_void_p, 1),
    ("source", ctypes.c_int, 1),
    ("dest", ctypes.c_int, 1),
    ("type", ctypes.c_int, 1),
)


class Metronome:
    def __init__(self, engine: Engine, settings: config.Settings | None = None) -> None:
        self.engine = engine
        self.settings = settings or config.settings

        self._running = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: int | None = None
        self._lock = threading.Lock()

        self._cursor = 0.0          # tick (ms) of the next click not yet scheduled
        self._click_index = 0       # global click counter since start
        self._beats_fired = 0       # incremented by the audio thread, one per beat
        self._ramp_steps = 0        # completed tempo-ramp steps
        self._bars_at_step = 0

        # (tick, is_downbeat, bar, bpm) for every click we have scheduled
        self.clicks: deque[tuple[float, bool, int, float]] = deque(maxlen=4096)
        # (tick, perf_counter) samples used to relate the audio clock to the system one
        self._obs: deque[tuple[int, float]] = deque(maxlen=400)

    # ----------------------------------------------------------------- config
    def cfg(self) -> dict[str, Any]:
        base = dict(config.DEFAULTS["metronome"])
        base.update(self.settings.get("metronome", default={}) or {})
        return base

    def configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.settings.update({"metronome": patch})
        if self._running:
            self._reschedule()
        return self.cfg()

    # ------------------------------------------------------------ start/stop
    def start(self) -> None:
        with self._lock:
            if self._running or self.engine.sequencer is None:
                return
            self._prepare_channel()
            if self._client is None:
                self._client = self.engine.sequencer.register_client("metronome", self._on_beat)
            self._running = True
            self._stop.clear()
            self._click_index = 0
            self._beats_fired = 0
            self._ramp_steps = 0
            self._bars_at_step = 0
            self.clicks.clear()
            self._obs.clear()
            self._cursor = self.engine.sequencer.get_tick() + LEAD_IN_MS
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="metronome", daemon=True,
                )
                self._thread.start()
            self._wake.set()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._flush()
            self._wake.set()
        # Kill anything already sounding on the click channel.
        if self.engine.fs is not None:
            self.engine.fs.cc(METRONOME_CHANNEL, 120, 0)

    def toggle(self) -> bool:
        if self._running:
            self.stop()
        else:
            self.start()
        return self._running

    def shutdown(self) -> None:
        self.stop()
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    # ------------------------------------------------------------- internals
    def _prepare_channel(self) -> None:
        """Point the click channel at a drum kit.

        Bank 128 has to be selected by an explicit program_select -- sending bank 128
        as CC0 over the wire does nothing under FluidSynth's default gs bank mode.
        """
        fs = self.engine.fs
        if fs is None:
            return
        sfid = self.engine.load_soundfont(config.DEFAULT_SOUNDFONT)
        if sfid == -1:
            return
        kit = int(self.cfg().get("kit", 0))
        if fs.program_select(METRONOME_CHANNEL, sfid, DRUM_BANK, kit) == -1:
            fs.program_select(METRONOME_CHANNEL, sfid, DRUM_BANK, 0)
        fs.cc(METRONOME_CHANNEL, 7, 110)
        fs.cc(METRONOME_CHANNEL, 91, 8)   # a touch of reverb, not a dry tick in the ear

    def _flush(self) -> None:
        seq = self.engine.sequencer
        if seq is not None and _remove_events is not None:
            _remove_events(seq.sequencer, -1, -1, -1)
        self.clicks.clear()

    def _reschedule(self) -> None:
        """Tempo/meter changed: drop queued clicks and refill from now."""
        with self._lock:
            if not self._running or self.engine.sequencer is None:
                return
            self._flush()
            self._prepare_channel()
            self._click_index = 0
            self._beats_fired = 0
            self._bars_at_step = 0
            self._cursor = self.engine.sequencer.get_tick() + LEAD_IN_MS
        self._wake.set()

    def _on_beat(self, tick, event, seq, data) -> None:  # noqa: ANN001
        # AUDIO THREAD. Three cheap operations, nothing else. No scheduling here.
        self._obs.append((int(tick), time.perf_counter()))
        self._beats_fired += 1
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._running:
                try:
                    self._fill()
                except Exception:  # noqa: BLE001 -- the click must never take the app down
                    pass
            self._wake.wait(timeout=WAKE_TIMEOUT)
            self._wake.clear()

    def _current_bpm(self, cfg: dict[str, Any]) -> float:
        base = float(cfg.get("bpm", 80))
        bpm = base
        if cfg.get("ramp_enabled"):
            # The ceiling bounds how far the *ramp* climbs. It must never drag the
            # tempo below what you actually asked for -- setting 200 bpm with a
            # leftover ramp ceiling of 100 has to give you 200, not 100.
            ceiling = max(base, float(cfg.get("ramp_bpm_max", 160)))
            bpm = min(base + self._ramp_steps * float(cfg.get("ramp_bpm_step", 4)), ceiling)
        return max(20.0, min(300.0, bpm))

    def _fill(self) -> None:
        # Held across the whole fill so a concurrent _reschedule() cannot reset the
        # cursor halfway through and leave a few clicks of the old tempo queued.
        # The audio thread never takes this lock -- it only rings the doorbell.
        with self._lock:
            self._fill_locked()

    def _fill_locked(self) -> None:
        seq = self.engine.sequencer
        if seq is None or not self._running:
            return
        cfg = self.cfg()
        dest = self.engine.seq_dest
        beats_per_bar = max(1, int(cfg.get("beats_per_bar", 4)))
        sub = max(1, min(4, int(cfg.get("subdivision", 1))))
        clicks_per_bar = beats_per_bar * sub

        now = seq.get_tick()
        bpm = self._current_bpm(cfg)
        lookahead = max(MIN_LOOKAHEAD_MS, (60000.0 / bpm) * 1.5)

        # If the machine hitched hard enough that our cursor fell into the past,
        # do not spray a burst of catch-up clicks -- resync to now.
        if self._cursor < now:
            self._cursor = now + LEAD_IN_MS

        while self._cursor < now + lookahead:
            i = self._click_index
            pos = i % clicks_per_bar
            bar = i // clicks_per_bar

            if pos == 0 and i > 0:
                self._bars_at_step += 1
                if cfg.get("ramp_enabled") and self._bars_at_step >= max(1, int(cfg.get("ramp_bars", 8))):
                    self._bars_at_step = 0
                    if self._current_bpm(cfg) < float(cfg.get("ramp_bpm_max", 160)):
                        self._ramp_steps += 1
                bpm = self._current_bpm(cfg)

            is_downbeat = pos == 0
            is_beat = pos % sub == 0
            if is_downbeat:
                key, vel = int(cfg.get("accent_note", 56)), int(cfg.get("accent_velocity", 118))
            elif is_beat:
                key, vel = int(cfg.get("beat_note", 37)), int(cfg.get("beat_velocity", 92))
            else:
                key, vel = int(cfg.get("sub_note", 42)), int(cfg.get("sub_velocity", 55))

            at = int(round(self._cursor))
            seq.note(at, METRONOME_CHANNEL, key, vel, 45, dest=dest)
            if is_beat and self._client is not None:
                # One observation per beat, not per subdivision -- enough to fit the
                # clock map without waking the audio thread's Python side too often.
                seq.timer(at, dest=self._client)
            self.clicks.append((float(at), is_downbeat, bar, bpm))

            self._cursor += (60000.0 / bpm) / sub
            self._click_index += 1

    # ------------------------------------------------------------ clock model
    def clock_fit(self) -> tuple[float, float, int]:
        """Least squares fit of perf_counter seconds against sequencer ticks.

        Returns (slope, intercept, n). slope is ~0.001 (ticks are milliseconds).
        """
        obs = list(self._obs)
        n = len(obs)
        if n < 8:
            return (0.001, 0.0, n)
        sx = sy = sxx = sxy = 0.0
        for x, y in obs:
            sx += x
            sy += y
            sxx += x * x
            sxy += x * y
        denom = n * sxx - sx * sx
        if denom == 0:
            return (0.001, 0.0, n)
        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
        return (slope, intercept, n)

    def click_offset_ms(self, perf_time: float) -> float | None:
        """How far off the nearest click a note landed, in ms. + is late.

        Returns None when the metronome is not running or the clock model has not
        seen enough beats to be trusted yet.
        """
        if not self._running or not self.clicks:
            return None
        slope, intercept, n = self.clock_fit()
        if n < 8 or slope <= 0:
            return None
        best: float | None = None
        for tick, _down, _bar, _bpm in self.clicks:
            predicted = slope * tick + intercept
            delta = (perf_time - predicted) * 1000.0
            if best is None or abs(delta) < abs(best):
                best = delta
        return best

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        cfg = self.cfg()
        _slope, _b, n = self.clock_fit()
        # The audio thread counts beats as it plays them, so this is what you hear,
        # not what has merely been queued.
        beats_per_bar = max(1, int(cfg.get("beats_per_bar", 4)))
        fired = self._beats_fired
        bar = (fired - 1) // beats_per_bar if fired else 0
        beat = (fired - 1) % beats_per_bar if fired else 0
        return {
            "running": self._running,
            "config": cfg,
            "effective_bpm": self._current_bpm(cfg),
            "ramp_steps": self._ramp_steps,
            "bars_at_step": self._bars_at_step,
            "bar": bar,
            "beat": beat,
            "clock_samples": n,
            "channel": METRONOME_CHANNEL,
            "can_cancel_events": _remove_events is not None,
        }

    def ramp_setback(self) -> None:
        """Drop one tempo step. Call this when the player misses."""
        self._ramp_steps = max(0, self._ramp_steps - 1)
        self._bars_at_step = 0
